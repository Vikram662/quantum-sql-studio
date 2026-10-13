"""Visual EXPLAIN: plan steps as cards, cost bars and an index advisor."""
import html
import math
import re

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout,
                               QWidget)

from . import icons
from .db import code_only, qi
from .theme import T

_STOP = r"(?:WHERE|ON|JOIN|LEFT|RIGHT|INNER|OUTER|CROSS|GROUP|ORDER|LIMIT|USING|NATURAL|STRAIGHT_JOIN|HAVING|UNION|SET|FORCE|USE|IGNORE|FOR|LOCK|WINDOW)"
_RX_FROM = re.compile(r"\b(?:FROM|JOIN|UPDATE|INTO)\s+((?:`[^`]+`|[\w$]+)(?:\.(?:`[^`]+`|[\w$]+))?)"
                      r"(?:\s+(?:AS\s+)?(?!" + _STOP + r"\b)([\w$]+))?", re.I)
_RX_COND = re.compile(r"(?:(`?[\w$]+`?)\.)?`?([\w$]+)`?\s*(<=>|!=|<>|>=|<=|=|<|>|\bIN\s*\(|\bLIKE\b|\bBETWEEN\b|"
                      r"\bIS\b)", re.I)
_RX_RHS = re.compile(r"(?:=|<=>)\s*(?:(`?[\w$]+`?)\.)`?([\w$]+)`?", re.I)
_RX_FUNC_ON_COL = re.compile(r"\b(DATE|YEAR|MONTH|LOWER|UPPER|TRIM|SUBSTRING|LEFT|DATE_FORMAT|CAST|IFNULL|COALESCE)"
                             r"\s*\(\s*(?:`?[\w$]+`?\.)?`?([\w$]+)`?[^)]*\)\s*(=|<|>|<=|>=|IN|LIKE|BETWEEN)", re.I)
_RX_LEADING_LIKE = re.compile(r"(?:`?[\w$]+`?\.)?`?([\w$]+)`?\s+LIKE\s+'%", re.I)

TYPE_INFO = {
    "ALL": ("danger", "Full table scan", "Reads every row of the table."),
    "index": ("warning", "Full index scan", "Reads the whole index — better than a table scan, still O(n)."),
    "range": ("accent", "Index range scan", "Uses an index to read only a range of rows."),
    "ref": ("success", "Index lookup", "Finds matching rows through a non-unique index."),
    "eq_ref": ("success", "Unique index lookup", "One row per join row through a primary / unique key."),
    "const": ("success", "Constant", "At most one matching row, read once."),
    "system": ("success", "System table", "Table with a single row."),
    "fulltext": ("success", "Full-text index", "Uses a FULLTEXT index."),
    "ref_or_null": ("success", "Index lookup (+NULL)", "Index lookup including NULL values."),
    "index_merge": ("accent", "Index merge", "Combines several indexes — a composite index is often faster."),
    "unique_subquery": ("success", "Unique subquery", "Subquery resolved through a unique index."),
    "index_subquery": ("accent", "Index subquery", "Subquery resolved through an index."),
}


def table_aliases(sql):
    """{alias_or_name: table} for tables referenced in FROM / JOIN / UPDATE / INTO."""
    out = {}
    for m in _RX_FROM.finditer(code_only(sql)):
        table = m.group(1).replace("`", "")
        alias = (m.group(2) or "").replace("`", "")
        name = table.split(".")[-1]
        out[name] = table
        if alias:
            out[alias] = table
    return out


def _clause(code, start_kw, end_kws):
    m = re.search(r"\b" + start_kw + r"\b(.*?)(?=\b(?:" + "|".join(end_kws) + r")\b|$)", code, re.I | re.S)
    return m.group(1) if m else ""


def analyze(sql, cols, rows, meta):
    """Build plan steps and advice from EXPLAIN output.
    meta: {table: {"columns": [names], "indexes": [{"name","type","columns"}], "rows": n}}"""
    names = [c.lower() for c in cols]
    get = lambda r, k: r[names.index(k)] if k in names else None  # noqa: E731
    aliases = table_aliases(sql)
    code = code_only(sql)
    where = _clause(code, "WHERE", ["GROUP", "ORDER", "LIMIT", "HAVING", "UNION", "FOR", "LOCK"])
    ons = " ".join(m.group(1) for m in re.finditer(r"\bON\b(.*?)(?=\b(?:JOIN|LEFT|RIGHT|INNER|CROSS|WHERE|GROUP|"
                                                    r"ORDER|LIMIT|STRAIGHT_JOIN)\b|$)", code, re.I | re.S))
    order = _clause(code, "ORDER\\s+BY", ["LIMIT", "UNION", "FOR", "LOCK"])
    single = len(set(aliases.values())) == 1

    def owner(alias, col):
        if alias:
            return aliases.get(alias.replace("`", ""))
        cands = [t for t in set(aliases.values()) if col in (meta.get(t, {}).get("columns") or [])]
        if len(cands) == 1:
            return cands[0]
        return next(iter(aliases.values())) if single else None

    eq, rng, join = {}, {}, {}

    def add(bucket, t, col):
        if col not in bucket.setdefault(t, []):
            bucket[t].append(col)
    for m in _RX_COND.finditer(where):
        t = owner(m.group(1), m.group(2))
        if not t or m.group(2).upper() in ("AND", "OR", "NOT", "NULL"):
            continue
        op = m.group(3).upper().strip()
        add(eq if op in ("=", "<=>", "IS") or op.startswith("IN") else rng, t, m.group(2))
    for text in (ons, where):
        for rx in (_RX_COND, _RX_RHS):
            for m in rx.finditer(text):
                if rx is _RX_COND and not m.group(1):
                    continue
                t = owner(m.group(1), m.group(2))
                if t and (rx is _RX_RHS or m.group(3).strip() in ("=", "<=>")):
                    add(join, t, m.group(2))
    order_cols = {}
    for part in order.split(","):
        m = re.match(r"\s*(?:(`?[\w$]+`?)\.)?`?([\w$]+)`?", part)
        if m:
            t = owner(m.group(1), m.group(2))
            if t:
                order_cols.setdefault(t, []).append(m.group(2))

    steps, advice, seen, first_of = [], [], set(), {}
    max_rows = max([int(get(r, "rows") or 0) for r in rows] + [1])
    for r in rows:
        driving = first_of.setdefault(get(r, "id"), len(steps)) == len(steps)
        alias = str(get(r, "table") or "")
        table = aliases.get(alias, alias)
        jtype = str(get(r, "type") or "")
        extra = str(get(r, "extra") or "")
        nrows = int(get(r, "rows") or 0)
        level, title, desc = TYPE_INFO.get(jtype, ("muted", jtype or "—", ""))
        steps.append({"id": get(r, "id"), "select_type": get(r, "select_type") or "", "alias": alias,
                      "table": table, "type": jtype, "level": level, "title": title, "desc": desc,
                      "key": get(r, "key"), "possible": get(r, "possible_keys"), "rows": nrows,
                      "filtered": get(r, "filtered"), "extra": extra,
                      "share": nrows / max_rows if max_rows else 0})
        m = meta.get(table) or {}
        tcols = m.get("columns") or []
        sorting = "filesort" in extra.lower()
        if (jtype in ("ALL", "index") or (sorting and nrows > 1000)) and table not in seen and tcols:
            seen.add(table)
            lead = [c for c in eq.get(table, []) if c in tcols]
            if not driving:
                lead += [c for c in join.get(table, []) if c in tcols and c not in lead]
            oc = [c for c in order_cols.get(table, []) if c in tcols and c not in lead] if sorting else []
            if oc:
                want, why = lead + oc, "filter and sort"
            else:
                want = lead + [c for c in rng.get(table, []) if c in tcols and c not in lead][:1]
                why = "filter / join"
            want = want[:4]
            qtable = ".".join(qi(p) for p in table.split("."))
            if want and not _covered(want, m.get("indexes") or []):
                name = "idx_" + "_".join([table.split(".")[-1]] + want)[:60]
                advice.append({"level": "danger" if nrows > 1000 else "warning",
                               "title": f"Add an index on {table} ({', '.join(want)})",
                               "detail": f"{title}{' + filesort' if sorting else ''} over ~{nrows:,} rows. "
                                         f"This index lets MySQL {why} using {', '.join(want)} instead of reading "
                                         "every row.",
                               "sql": f"CREATE INDEX {qi(name)} ON {qtable} ({', '.join(qi(c) for c in want)});"})
            elif not want and jtype == "ALL" and nrows > 1000:
                advice.append({"level": "warning", "title": f"{table} is read completely",
                               "detail": f"No WHERE / JOIN condition limits {table} (~{nrows:,} rows). Add a filter "
                                         "or LIMIT if you do not need every row.", "sql": None})
        if "temporary" in extra.lower():
            advice.append({"level": "info", "title": f"Temporary table for {alias}",
                           "detail": "GROUP BY / DISTINCT / UNION needs a temp table. An index on the GROUP BY "
                                     "columns can avoid it; large temp tables spill to disk.", "sql": None})
    for m in _RX_FUNC_ON_COL.finditer(where):
        advice.append({"level": "warning", "title": f"{m.group(1).upper()}() on column {m.group(2)}",
                       "detail": f"Wrapping {m.group(2)} in a function stops MySQL from using its index. Rewrite as a "
                                 f"range, e.g. {m.group(2)} >= '2024-01-01' AND {m.group(2)} < '2024-02-01'.",
                       "sql": None})
    raw_where = _clause(sql, "WHERE", ["GROUP", "ORDER", "LIMIT", "HAVING", "UNION"])
    for m in _RX_LEADING_LIKE.finditer(raw_where):
        advice.append({"level": "info", "title": f"LIKE '%…' on {m.group(1)}",
                       "detail": "A leading wildcard cannot use a B-tree index. Consider a FULLTEXT index or a "
                                 "prefix search (LIKE 'abc%').", "sql": None})
    if not advice and steps:
        good = all(s["level"] in ("success", "accent") for s in steps)
        advice.append({"level": "ok" if good else "info",
                       "title": "Plan looks good" if good else "No index suggestion",
                       "detail": "Every table is read through an index." if good else
                       "No obvious missing index found for this statement.", "sql": None})
    return steps, advice


def _covered(cols, indexes):
    want = [c.lower() for c in cols]
    for ix in indexes:
        have = [c.split("(")[0].lower() for c in ix["columns"]]
        if have[:len(want)] == want:
            return True
    return False


class _Bar(QWidget):
    def __init__(self, share, level):
        super().__init__()
        self.share, self.level = share, level
        self.setFixedHeight(6)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(T["border"]))
        p.drawRoundedRect(QRectF(self.rect()), 3, 3)
        w = self.width() * (0.04 + 0.96 * (math.log10(1 + self.share * 999) / 3 if self.share > 0 else 0))
        p.setBrush(QColor(T.get(self.level, T["accent"])))
        p.drawRoundedRect(QRectF(0, 0, w, self.height()), 3, 3)


def _badge(text, level):
    lbl = QLabel(text)
    col = T.get(level, T["muted"])
    lbl.setStyleSheet(f"color: {col}; border: 1px solid {col}; border-radius: 8px; padding: 1px 8px; "
                      "font-weight: 600; font-size: 8pt;")
    return lbl


class ExplainView(QScrollArea):
    def __init__(self, sql, cols, rows, meta, open_sql=None):
        super().__init__()
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(12, 10, 12, 12)
        lay.setSpacing(8)
        steps, advice = analyze(sql, cols, rows, meta)
        total = 1
        for s in steps:
            total *= max(1, s["rows"])
        h = QLabel("Execution plan")
        h.setObjectName("h2")
        lay.addWidget(h)
        lay.addWidget(QLabel(f"<span style='color:{T['muted']}'>Steps run top to bottom; nested loops multiply "
                             f"rows — about <b>{total:,}</b> row combinations examined.</span>"))
        for s in steps:
            lay.addWidget(self._step(s))
        h2 = QLabel("Advisor")
        h2.setObjectName("h2")
        lay.addSpacing(6)
        lay.addWidget(h2)
        for a in advice:
            lay.addWidget(self._advice(a, open_sql))
        lay.addStretch(1)
        self.setWidget(body)

    def _step(self, s):
        card = QFrame()
        card.setObjectName("card")
        v = QVBoxLayout(card)
        v.setContentsMargins(12, 8, 12, 10)
        v.setSpacing(4)
        top = QHBoxLayout()
        name = QLabel(f"<b>{html.escape(s['alias'])}</b>"
                      + (f" <span style='color:{T['muted']}'>({html.escape(s['table'])})</span>"
                         if s["table"] != s["alias"] else ""))
        top.addWidget(QLabel(f"<span style='color:{T['faint']}'>#{s['id']} {s['select_type']}</span>"))
        top.addWidget(name)
        top.addWidget(_badge(s["title"], s["level"]))
        top.addStretch(1)
        top.addWidget(QLabel(f"<b>~{s['rows']:,}</b> rows"
                             + (f" · {float(s['filtered']):.0f}% kept" if s["filtered"] not in (None, "") else "")))
        v.addLayout(top)
        v.addWidget(_Bar(s["share"], s["level"]))
        info = [s["desc"]]
        info.append(f"index used: <b>{s['key']}</b>" if s["key"] else "index used: <b>none</b>")
        if s["possible"]:
            info.append(f"candidates: {s['possible']}")
        if s["extra"]:
            ex = html.escape(s["extra"])
            for word, lvl in (("Using filesort", "warning"), ("Using temporary", "warning"),
                              ("Using index", "success")):
                ex = ex.replace(word, f"<span style='color:{T[lvl]}'>{word}</span>")
            info.append(ex)
        lbl = QLabel(" · ".join(i for i in info if i))
        lbl.setWordWrap(True)
        lbl.setStyleSheet(f"color: {T['muted']};")
        v.addWidget(lbl)
        return card

    def _advice(self, a, open_sql):
        card = QFrame()
        card.setObjectName("card")
        h = QHBoxLayout(card)
        h.setContentsMargins(12, 8, 12, 8)
        colors = {"danger": "danger", "warning": "warning", "info": "accent", "ok": "success"}
        esc = html.escape
        txt = QLabel(f"<span style='color:{T[colors[a['level']]]}; font-weight:600'>●</span> "
                     f"<b>{esc(a['title'])}</b><br><span style='color:{T['muted']}'>{esc(a['detail'])}</span>"
                     + (f"<br><code>{esc(a['sql'])}</code>" if a.get("sql") else ""))
        txt.setWordWrap(True)
        txt.setTextInteractionFlags(Qt.TextSelectableByMouse)
        h.addWidget(txt, 1)
        if a.get("sql"):
            b = QPushButton(icons.icon("copy"), "Copy")
            b.clicked.connect(lambda: QApplication.clipboard().setText(a["sql"]))
            h.addWidget(b)
            if open_sql:
                o = QPushButton(icons.icon("query"), "Open in query tab")
                o.clicked.connect(lambda: open_sql(a["sql"]))
                h.addWidget(o)
        return card

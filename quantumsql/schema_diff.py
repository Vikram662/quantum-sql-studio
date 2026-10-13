"""Schema diff & sync: compare two databases (on any sessions) and generate the ALTER script."""
import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QGridLayout, QHBoxLayout, QLabel,
                               QPushButton, QSplitter, QTreeWidget, QTreeWidgetItem, QVBoxLayout)

from . import icons
from .db import qi, run_async
from .editor import read_only_code_view
from .theme import T
from .ui import muted


def load_schema(conn, db):
    """{table: {"create": sql, "columns": {name: (definition, position)}, "keys": {name: definition},
    "options": str}} plus views/routines names."""
    out = {"tables": {}, "views": {}, "routines": {}}
    rows = conn.rows("SELECT TABLE_NAME, TABLE_TYPE FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s "
                     "ORDER BY TABLE_NAME", (db,))
    for name, ttype in rows:
        cols, data, _ = conn.query(f"SHOW CREATE {'VIEW' if ttype == 'VIEW' else 'TABLE'} {qi(db)}.{qi(name)}",
                                   log=False)
        create = data[0][1] if data else ""
        if ttype == "VIEW":
            out["views"][name] = _normalize_view(create, db)
        else:
            out["tables"][name] = parse_create(create)
    for name, rtype in conn.rows("SELECT ROUTINE_NAME, ROUTINE_TYPE FROM information_schema.ROUTINES "
                                 "WHERE ROUTINE_SCHEMA=%s", (db,)):
        out["routines"][f"{rtype} {name}"] = (rtype, name)
    return out


def _normalize_view(create, db):
    s = re.sub(r"DEFINER=\S+\s+", "", create)
    s = re.sub(r"SQL SECURITY \w+\s+", "", s)
    return s.replace(f"`{db}`.", "")


def parse_create(sql):
    """Split SHOW CREATE TABLE into columns, keys and table options."""
    lines = sql.splitlines()
    cols, keys, order = {}, {}, []
    for line in lines[1:]:
        ln = line.strip().rstrip(",")
        if ln.startswith(")"):
            options = re.sub(r"\s*AUTO_INCREMENT=\d+", "", ln[1:].strip())
            break
        if ln.startswith("`"):
            m = re.match(r"`((?:[^`]|``)+)`\s+(.*)", ln)
            cols[m.group(1)] = m.group(2)
            order.append(m.group(1))
        elif ln.upper().startswith("PRIMARY KEY"):
            keys["PRIMARY"] = ln
        else:
            m = re.match(r"(?:UNIQUE |FULLTEXT |SPATIAL )?KEY `((?:[^`]|``)+)`|CONSTRAINT `((?:[^`]|``)+)`", ln)
            if m:
                keys[("FK:" + m.group(2)) if m.group(2) else m.group(1)] = ln
    else:
        options = ""
    return {"create": sql, "columns": cols, "order": order, "keys": keys, "options": options}


def diff(src, dst, with_drops=False):
    """Changes that turn dst into src. Returns list of (kind, object, detail, sql)."""
    changes = []
    st, dt = src["tables"], dst["tables"]
    for name in sorted(set(st) - set(dt)):
        changes.append(("create", name, "table only in source",
                        re.sub(r"\s+AUTO_INCREMENT=\d+", "", st[name]["create"]) + ";"))
    for name in sorted(set(dt) - set(st)):
        changes.append(("drop", name, "table only in target",
                        ("" if with_drops else "-- ") + f"DROP TABLE {qi(name)};"))
    for name in sorted(set(st) & set(dt)):
        a, b = st[name], dt[name]
        parts, notes = [], []
        drop_fks = [k for k in b["keys"] if k.startswith("FK:") and a["keys"].get(k) != b["keys"][k]]
        for k in drop_fks:
            parts.append(f"DROP FOREIGN KEY {qi(k[3:])}")
            notes.append(f"drop FK {k[3:]}")
        for k in b["keys"]:
            if not k.startswith("FK:") and a["keys"].get(k) != b["keys"][k]:
                parts.append("DROP PRIMARY KEY" if k == "PRIMARY" else f"DROP INDEX {qi(k)}")
                if k not in a["keys"]:
                    notes.append(f"drop index {k}")
        prev = None
        for col in a["order"]:
            pos = f" AFTER {qi(prev)}" if prev else " FIRST"
            if col not in b["columns"]:
                parts.append(f"ADD COLUMN {qi(col)} {a['columns'][col]}{pos}")
                notes.append(f"add column {col}")
            elif a["columns"][col] != b["columns"][col]:
                parts.append(f"MODIFY COLUMN {qi(col)} {a['columns'][col]}")
                notes.append(f"modify {col}: {b['columns'][col]}  →  {a['columns'][col]}")
            prev = col
        for col in b["order"]:
            if col not in a["columns"]:
                if with_drops:
                    parts.append(f"DROP COLUMN {qi(col)}")
                notes.append(f"column {col} only in target" + ("" if with_drops else " (kept)"))
        for k, definition in a["keys"].items():
            if k.startswith("FK:"):
                continue
            if b["keys"].get(k) != definition:
                parts.append(f"ADD {definition}")
                notes.append(("change" if k in b["keys"] else "add") + f" index {k}")
        for k, definition in a["keys"].items():
            if k.startswith("FK:") and b["keys"].get(k) != definition:
                parts.append(f"ADD {definition}")
                notes.append(f"add FK {k[3:]}")
        if a["options"] != b["options"]:
            opts = a["options"]
            for opt in ("ENGINE", "DEFAULT CHARSET", "COLLATE", "COMMENT"):
                m = re.search(opt + r"=('(?:[^']|'')*'|\S+)", opts)
                n = re.search(opt + r"=('(?:[^']|'')*'|\S+)", b["options"])
                if m and (not n or m.group(1) != n.group(1)):
                    parts.append(f"{opt}={m.group(1)}")
                    notes.append(f"{opt.lower()}: {n.group(1) if n else '—'} → {m.group(1)}")
        if parts:
            changes.append(("alter", name, "; ".join(notes),
                            f"ALTER TABLE {qi(name)}\n  " + ",\n  ".join(parts) + ";"))
        elif notes:
            changes.append(("info", name, "; ".join(notes), ""))
    for name in sorted(set(src["views"]) | set(dst["views"])):
        if name not in dst["views"]:
            changes.append(("create", name, "view only in source", src["views"][name] + ";"))
        elif name not in src["views"]:
            changes.append(("drop", name, "view only in target",
                            ("" if with_drops else "-- ") + f"DROP VIEW {qi(name)};"))
        elif src["views"][name] != dst["views"][name]:
            changes.append(("alter", name, "view definition differs",
                            re.sub(r"^CREATE\s+", "CREATE OR REPLACE ", src["views"][name], flags=re.I) + ";"))
    for key in sorted(set(src["routines"]) - set(dst["routines"])):
        changes.append(("info", key, "routine only in source — copy it with “Show CREATE code”", ""))
    return changes


class SchemaDiffDialog(QDialog):
    def __init__(self, main, session=None, db=None):
        super().__init__(main)
        self.main = main
        self.setWindowTitle("Schema compare & sync")
        self.resize(1180, 720)
        self.changes = []
        lay = QVBoxLayout(self)
        grid = QGridLayout()
        self.src_s, self.src_db = QComboBox(), QComboBox()
        self.dst_s, self.dst_db = QComboBox(), QComboBox()
        for combo in (self.src_s, self.dst_s):
            for s in main.sessions:
                combo.addItem(icons.icon("server", s.color or None), s.name, s)
        self.src_s.currentIndexChanged.connect(lambda *_: self._fill(self.src_s, self.src_db))
        self.dst_s.currentIndexChanged.connect(lambda *_: self._fill(self.dst_s, self.dst_db))
        grid.addWidget(QLabel("<b>Source</b> (the desired schema)"), 0, 0)
        grid.addWidget(self.src_s, 0, 1)
        grid.addWidget(self.src_db, 0, 2)
        grid.addWidget(QLabel("<b>Target</b> (will be changed)"), 1, 0)
        grid.addWidget(self.dst_s, 1, 1)
        grid.addWidget(self.dst_db, 1, 2)
        self.drops = QCheckBox("Include DROP statements for objects missing in source")
        self.b_cmp = QPushButton(icons.icon("diff"), "Compare")
        self.b_cmp.setProperty("primary", True)
        self.b_cmp.clicked.connect(self.compare)
        grid.addWidget(self.drops, 0, 3)
        grid.addWidget(self.b_cmp, 1, 3)
        grid.setColumnStretch(2, 1)
        lay.addLayout(grid)
        self.summary = muted("Pick two databases — e.g. local development as source and the live server as target.")
        lay.addWidget(self.summary)
        split = QSplitter()
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Object", "Change"])
        self.tree.setColumnWidth(0, 230)
        self.tree.itemChanged.connect(lambda *_: self._render_script())
        self.tree.currentItemChanged.connect(self._show_item)
        split.addWidget(self.tree)
        self.script = read_only_code_view()
        split.addWidget(self.script)
        split.setSizes([520, 660])
        lay.addWidget(split, 1)
        row = QHBoxLayout()
        b_copy = QPushButton(icons.icon("copy"), "Copy script")
        b_copy.clicked.connect(lambda: QApplication.clipboard().setText(self.script.toPlainText()))
        b_open = QPushButton(icons.icon("query"), "Open script in a query tab on the target")
        b_open.clicked.connect(self._open)
        row.addWidget(muted("Untick changes you do not want. Review the script before running it."))
        row.addStretch(1)
        row.addWidget(b_copy)
        row.addWidget(b_open)
        lay.addLayout(row)
        if session is not None:
            self.src_s.setCurrentIndex(max(0, main.sessions.index(session)))
            self.dst_s.setCurrentIndex(max(0, main.sessions.index(session)))
        self._fill(self.src_s, self.src_db, db)
        self._fill(self.dst_s, self.dst_db)

    def _fill(self, s_combo, db_combo, select=None):
        s = s_combo.currentData()
        db_combo.clear()
        if s is None:
            return
        db_combo.addItems([d for d in self.main.database_names(s)
                           if d not in ("information_schema", "performance_schema", "mysql", "sys")])
        if select:
            db_combo.setCurrentText(select)

    def compare(self):
        s1, d1, s2, d2 = self.src_s.currentData(), self.src_db.currentText(), self.dst_s.currentData(), \
            self.dst_db.currentText()
        if not (s1 and s2 and d1 and d2):
            return
        if s1 is s2 and d1 == d2:
            self.summary.setText("Source and target are the same database.")
            return
        drops = self.drops.isChecked()
        c1, c2 = s1.conn.clone(), s2.conn.clone()
        self.b_cmp.setEnabled(False)
        self.summary.setText("Reading both schemas…")

        def work():
            try:
                return diff(load_schema(c1, d1), load_schema(c2, d2), drops)
            finally:
                c1.close()
                c2.close()

        def done(changes):
            self.b_cmp.setEnabled(True)
            self.changes = changes
            self._populate()

        def failed(e):
            self.b_cmp.setEnabled(True)
            self.summary.setText(f"Compare failed: {e}")
        run_async(work, done, failed)

    def _populate(self):
        self.tree.blockSignals(True)
        self.tree.clear()
        colors = {"create": T["success"], "drop": T["danger"], "alter": T["warning"], "info": T["muted"]}
        labels = {"create": "create", "drop": "drop", "alter": "alter", "info": "note"}
        for i, (kind, name, detail, sql) in enumerate(self.changes):
            it = QTreeWidgetItem([name, f"{labels[kind]}: {detail}"])
            it.setForeground(1, QColor(colors[kind]))
            it.setIcon(0, icons.icon("table"))
            it.setToolTip(1, detail)
            it.setData(0, Qt.UserRole, i)
            if sql:
                it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
                it.setCheckState(0, Qt.Checked if not sql.startswith("--") else Qt.Unchecked)
            self.tree.addTopLevelItem(it)
        self.tree.blockSignals(False)
        n = {k: sum(1 for c in self.changes if c[0] == k) for k in ("create", "alter", "drop")}
        self.summary.setText("Schemas are identical ✓" if not self.changes else
                             f"{n['create']} to create · {n['alter']} to alter · {n['drop']} only in target")
        self._render_script()

    def _render_script(self):
        parts = [f"-- Sync {self.dst_db.currentText()} to match {self.src_db.currentText()}",
                 "SET FOREIGN_KEY_CHECKS = 0;", ""]
        for i in range(self.tree.topLevelItemCount()):
            it = self.tree.topLevelItem(i)
            kind, name, detail, sql = self.changes[it.data(0, Qt.UserRole)]
            if sql and it.checkState(0) == Qt.Checked:
                parts += [f"-- {kind} {name}: {detail}", sql.lstrip("- ") if sql.startswith("-- DROP") else sql, ""]
        parts.append("SET FOREIGN_KEY_CHECKS = 1;")
        self.script.setPlainText("\n".join(parts) if len(parts) > 4 else "-- nothing to change")

    def _show_item(self, item, _prev):
        if item is None:
            return
        _kind, name, _detail, sql = self.changes[item.data(0, Qt.UserRole)]
        doc = self.script.document()
        cur = doc.find(f"{name}") if sql else None
        if cur and not cur.isNull():
            self.script.setTextCursor(cur)
            self.script.centerCursor()

    def _open(self):
        s = self.dst_s.currentData()
        if s:
            self.main.new_query(s, self.dst_db.currentText(), self.script.toPlainText() + "\n")
            self.accept()

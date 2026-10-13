"""Global search: find a value in every text/number column of a database."""
import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QHBoxLayout, QLineEdit, QProgressBar, QPushButton,
                               QSpinBox, QVBoxLayout)

from . import icons
from .db import literal, qi, qt, run_async
from .grid import DataGrid, ResultModel
from .ui import muted

TEXT_TYPES = ("char", "varchar", "text", "tinytext", "mediumtext", "longtext", "enum", "set", "json")
NUM_TYPES = ("int", "tinyint", "smallint", "mediumint", "bigint", "decimal", "float", "double")


class GlobalSearchDialog(QDialog):
    def __init__(self, main, session, db):
        super().__init__(main)
        self.main, self.session = main, session
        self.setWindowTitle("Search in database")
        self.resize(980, 600)
        self.task = None
        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        self.db = QComboBox()
        self.db.addItems(main.database_names(session))
        if db:
            self.db.setCurrentText(db)
        self.text = QLineEdit()
        self.text.setPlaceholderText("Value to find — e.g. an email, order number or phone")
        self.text.returnPressed.connect(self.start)
        self.mode = QComboBox()
        for label, key in (("contains", "like"), ("is exactly", "eq"), ("starts with", "prefix"),
                           ("regular expression", "regexp")):
            self.mode.addItem(label, key)
        self.b_go = QPushButton(icons.icon("search"), "Search")
        self.b_go.setProperty("primary", True)
        self.b_go.clicked.connect(self.start)
        row.addWidget(muted("Database"))
        row.addWidget(self.db)
        row.addWidget(self.mode)
        row.addWidget(self.text, 1)
        row.addWidget(self.b_go)
        lay.addLayout(row)
        opts = QHBoxLayout()
        self.numbers = QCheckBox("Also search numeric columns (exact match)")
        self.numbers.setChecked(True)
        self.case = QCheckBox("Case sensitive")
        self.max_rows = QSpinBox()
        self.max_rows.setRange(0, 1_000_000_000)
        self.max_rows.setValue(5_000_000)
        self.max_rows.setPrefix("Skip tables over ")
        self.max_rows.setSuffix(" rows (0 = no limit)")
        self.max_rows.setSingleStep(1_000_000)
        opts.addWidget(self.numbers)
        opts.addWidget(self.case)
        opts.addStretch(1)
        opts.addWidget(self.max_rows)
        lay.addLayout(opts)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.status = muted("Searches each table with one query — matching tables and columns are listed below. "
                            "Double-click a result to open the matching rows.")
        lay.addWidget(self.bar)
        lay.addWidget(self.status)
        self.grid = DataGrid()
        self.grid.doubleClicked.connect(self._open)
        lay.addWidget(self.grid, 1)
        self.results = []
        self._set_results([])
        self.text.setFocus()

    def _set_results(self, rows):
        self.results = rows
        self.grid.set_model(ResultModel(["Table", "Column", "Matching rows", "Example value"],
                                        [[r["table"], r["column"], r["count"], r["example"]] for r in rows]))

    def _condition(self, col, ctype, value, mode, case):
        c = qi(col)
        if ctype in NUM_TYPES:
            return f"{c} = {literal(value)}"
        target = f"BINARY {c}" if case else c
        if mode == "eq":
            return f"{target} = {literal(value)}"
        if mode == "prefix":
            return f"{target} LIKE {literal(_escape_like(value) + '%')}"
        if mode == "regexp":
            return f"{target} REGEXP {literal(value)}"
        return f"{target} LIKE {literal('%' + _escape_like(value) + '%')}"

    def start(self):
        if self.task is not None:
            self.task.cancelled = True
            return
        value = self.text.text()
        if not value:
            return
        db = self.db.currentText()
        mode = self.mode.currentData()
        case = self.case.isChecked()
        is_num = _is_number(value)
        numbers = self.numbers.isChecked() and is_num and mode in ("like", "eq")
        limit = self.max_rows.value()
        conn = self.session.conn.clone()
        self._set_results([])
        self.b_go.setText("Stop")
        t0 = time.monotonic()

        def work(task):
            found = []
            try:
                cols = conn.rows(
                    "SELECT c.TABLE_NAME, c.COLUMN_NAME, c.DATA_TYPE, t.TABLE_ROWS FROM information_schema.COLUMNS c "
                    "JOIN information_schema.TABLES t ON t.TABLE_SCHEMA=c.TABLE_SCHEMA AND t.TABLE_NAME=c.TABLE_NAME "
                    "WHERE c.TABLE_SCHEMA=%s AND t.TABLE_TYPE='BASE TABLE' ORDER BY c.TABLE_NAME, c.ORDINAL_POSITION",
                    (db,))
                tables = {}
                for table, col, ctype, nrows in cols:
                    ctype = ctype.lower()
                    if ctype in TEXT_TYPES or (numbers and ctype in NUM_TYPES):
                        tables.setdefault(table, {"rows": nrows or 0, "cols": []})["cols"].append((col, ctype))
                names = sorted(tables)
                for i, table in enumerate(names):
                    if task.cancelled:
                        break
                    info = tables[table]
                    task.progress(int(i * 100 / max(1, len(names))), f"{table}  ({i + 1} of {len(names)})")
                    if limit and info["rows"] > limit:
                        found.append({"table": table, "column": "(skipped)", "count": None,
                                      "example": f"~{info['rows']:,} rows — raise the limit to include"})
                        continue
                    conds = [self._condition(c, t, value, mode, case) for c, t in info["cols"]]
                    sums = ", ".join(f"SUM({cond})" for cond in conds)
                    row = conn.rows(f"SELECT {sums} FROM {qt(db, table)} WHERE {' OR '.join(conds)}")[0]
                    for (col, ctype), cnt, cond in zip(info["cols"], row, conds):
                        if cnt:
                            ex = conn.rows(f"SELECT {qi(col)} FROM {qt(db, table)} WHERE {cond} LIMIT 1")
                            example = str(ex[0][0])[:200] if ex else ""
                            found.append({"table": table, "column": col, "count": int(cnt), "example": example,
                                          "cond": cond})
                task.progress(100, "")
            finally:
                conn.close()
            return found, len(tables)

        def progress(pct, msg):
            self.bar.setValue(pct)
            if msg:
                self.status.setText(f"Searching {msg} …")

        def done(res):
            found, n = res
            self.task = None
            self.b_go.setText("Search")
            hits = [f for f in found if f["count"]]
            self._set_results(sorted(found, key=lambda f: (f["count"] is None, -(f["count"] or 0))))
            self.status.setText(f"{sum(f['count'] for f in hits):,} matching rows in {len({f['table'] for f in hits})}"
                                f" table(s) · searched {n} tables in {time.monotonic() - t0:.1f} s")

        def failed(e):
            self.task = None
            self.b_go.setText("Search")
            self.status.setText(f"Search failed: {e}")
        self.task = run_async(work, done, failed, progress)

    def _open(self, index):
        r = self.results[index.row()] if index.row() < len(self.results) else None
        if not r or not r.get("cond"):
            return
        db = self.db.currentText()
        self.main.new_query(self.session, db, f"SELECT *\nFROM {qi(r['table'])}\nWHERE {r['cond']}\nLIMIT 1000;\n")\
            .run_all()

    def reject(self):
        if self.task is not None:
            self.task.cancelled = True
        super().reject()


def _escape_like(v):
    return v.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _is_number(v):
    try:
        float(v)
        return True
    except ValueError:
        return False

"""Database tab: list of tables/views/routines with sizes and bulk actions."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLineEdit, QMessageBox, QVBoxLayout, QWidget

from .db import human_size, qt, run_async
from .dialogs import ExportDialog, import_file
from .grid import DataGrid, ResultModel
from .ui import bar, confirm, muted, tool

HEADERS = ["Name", "Type", "Rows", "Size", "Engine", "Collation", "Created", "Updated", "Comment"]


class DatabaseTab(QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        self.session = self.db = None
        self.objects = []
        self._loaded_key = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.title = muted("")
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter objects…")
        self.filter.setFixedWidth(240)
        self.filter.textChanged.connect(self._fill)
        lay.addWidget(bar(
            tool("plus", "Create table", lambda: self.main.create_table(self.session, self.db), "New table"),
            tool("query", "New query tab on this database", lambda: self.main.new_query(self.session, self.db),
                 "Query"),
            "|",
            tool("export", "Export selected tables", self._export, "Export"),
            tool("import", "Import SQL file into this database", self._import, "Import"),
            "|",
            tool("minus", "Empty (truncate) selected tables", self._truncate, "Empty"),
            tool("trash", "Drop selected objects", self._drop, "Drop"),
            "|", self.title, None, self.filter, tool("refresh", "Refresh", self.refresh)))
        self.grid = DataGrid()
        self.grid.setSelectionBehavior(DataGrid.SelectRows)
        self.grid.doubleClicked.connect(self._open)
        lay.addWidget(self.grid, 1)

    def set_database(self, session, db):
        self.session, self.db = session, db
        if self.isVisible():
            self._ensure()

    def showEvent(self, e):
        super().showEvent(e)
        self._ensure()

    def _ensure(self):
        if self.session and self.db and self._loaded_key != (id(self.session), self.db):
            self.refresh()

    def refresh(self):
        if not (self.session and self.db):
            return
        self._loaded_key = (id(self.session), self.db)
        conn, db = self.session.conn, self.db
        self.title.setText(f"Loading {db}…")
        run_async(lambda: conn.objects(db), self._loaded, lambda e: self.main.status(e, error=True))

    def _loaded(self, objects):
        self.objects = objects
        total = sum(o.get("size") or 0 for o in objects)
        n_tables = len([o for o in objects if o["kind"] == "table"])
        self.title.setText(f"{self.db}  ·  {n_tables} tables  ·  {human_size(total)}")
        self._fill()

    def _fill(self, *_):
        text = self.filter.text().lower()
        rows = []
        for o in self.objects:
            if text and text not in o["name"].lower():
                continue
            comment = "" if o["kind"] == "view" else o.get("comment") or ""
            rows.append([o["name"], o["kind"].capitalize(), o.get("rows") if o["kind"] == "table" else "",
                         human_size(o["size"]) if o.get("size") else "", o.get("engine") or "",
                         o.get("collation") or "", o.get("created") or "", o.get("updated") or "", comment])
        self.grid.set_model(ResultModel(HEADERS, rows))

    def _selected(self, kinds=("table", "view")):
        model = self.grid.model()
        if not model:
            return []
        rows = sorted({i.row() for i in self.grid.selectedIndexes()})
        out = []
        for r in rows:
            name, kind = model.rows[r][0], model.rows[r][1].lower()
            if kind in kinds:
                out.append((name, kind))
        return out

    def _open(self, index):
        row = self.grid.model().rows[index.row()]
        self.main.select_object(self.session, self.db, row[0], row[1].lower(), open_data=True)

    def _export(self):
        tables = [o["name"] for o in self.objects if o["kind"] == "table"]
        pre = [n for n, _ in self._selected(("table",))]
        ExportDialog(self, self.session, self.db, tables, pre).exec()

    def _import(self):
        if import_file(self, self.session, self.db):
            self.refresh()
            self.main.refresh_db(self.session, self.db)

    def _run(self, stmts, title):
        conn = self.session.conn

        def work():
            for s in stmts:
                conn.execute(s)
        run_async(work, lambda *_: (self.refresh(), self.main.refresh_db(self.session, self.db)),
                  lambda e: QMessageBox.critical(self, title, e))

    def _truncate(self):
        sel = self._selected(("table",))
        if sel and confirm(self, "Empty tables",
                           "Delete ALL rows from:\n\n" + "\n".join(n for n, _ in sel) + "\n\nThis cannot be undone.",
                           danger=True):
            self._run([f"TRUNCATE TABLE {qt(self.db, n)}" for n, _ in sel], "Empty tables")

    def _drop(self):
        model = self.grid.model()
        rows = sorted({i.row() for i in self.grid.selectedIndexes()}) if model else []
        objs = [(model.rows[r][0], model.rows[r][1].lower()) for r in rows]
        if objs and confirm(self, "Drop objects",
                            "Drop these objects?\n\n" + "\n".join(f"{k}  {n}" for n, k in objs)
                            + "\n\nThis cannot be undone.", danger=True):
            self._run([f"DROP {k.upper()} {qt(self.db, n)}" for n, k in objs], "Drop objects")

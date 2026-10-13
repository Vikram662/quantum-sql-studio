"""Table designer: create or alter a table's columns, keys and options."""
import re

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFormLayout, QFrame, QHBoxLayout, QHeaderView,
                               QLineEdit, QMessageBox, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout,
                               QWidget)

from . import icons
from .db import literal, qi, qt, run_async
from .dialogs import ForeignKeyDialog, IndexDialog, confirm_sql
from .editor import read_only_code_view
from .grid import DataGrid, ResultModel
from .ui import bar, confirm, muted, tool

TYPES = ["INT", "BIGINT", "TINYINT", "SMALLINT", "MEDIUMINT", "DECIMAL", "FLOAT", "DOUBLE", "BIT", "BOOLEAN",
         "VARCHAR", "CHAR", "TEXT", "TINYTEXT", "MEDIUMTEXT", "LONGTEXT", "DATE", "DATETIME", "TIMESTAMP", "TIME",
         "YEAR", "ENUM", "SET", "JSON", "BLOB", "TINYBLOB", "MEDIUMBLOB", "LONGBLOB", "BINARY", "VARBINARY",
         "UUID", "INET6", "GEOMETRY", "POINT"]
EXTRAS = ["", "AUTO_INCREMENT", "ON UPDATE CURRENT_TIMESTAMP"]
COLS = ["PK", "Name", "Datatype", "Length/Set", "Unsigned", "Allow NULL", "Default", "Extra", "Comment"]
C_PK, C_NAME, C_TYPE, C_LEN, C_UNS, C_NULL, C_DEF, C_EXTRA, C_COMMENT = range(9)


def default_clause(text):
    t = (text or "").strip()
    if not t:
        return ""
    up = t.upper()
    if up == "NULL":
        return " DEFAULT NULL"
    if (re.fullmatch(r"CURRENT_TIMESTAMP(\(\d*\))?", up) or re.fullmatch(r"[A-Z_][A-Z0-9_]*\(.*\)", up)
            or (len(t) >= 2 and t[0] == "'" and t[-1] == "'") or re.fullmatch(r"-?\d+(\.\d+)?", t)):
        return f" DEFAULT {t}"
    return f" DEFAULT {literal(t)}"


def column_sql(c):
    s = f"{qi(c['name'])} {c['type'].upper()}"
    if c["length"]:
        s += f"({c['length']})"
    if c["unsigned"]:
        s += " UNSIGNED"
    if c.get("zerofill"):
        s += " ZEROFILL"
    if c.get("collation"):
        s += f" COLLATE {c['collation']}"
    s += " NULL" if c["null"] else " NOT NULL"
    s += default_clause(c["default"])
    if c["extra"]:
        s += f" {c['extra']}"
    if c["comment"]:
        s += f" COMMENT {literal(c['comment'])}"
    return s


def parse_column(info, pk_cols):
    m = re.match(r"^(\w+)(?:\((.*)\))?(.*)$", info["Type"], re.S)
    base, length, rest = (m.group(1), m.group(2) or "", m.group(3).lower()) if m else (info["Type"], "", "")
    extra = (info.get("Extra") or "").replace("DEFAULT_GENERATED", "").strip()
    generated = "generated" in extra.lower()
    if extra.lower() == "auto_increment":
        extra = "AUTO_INCREMENT"
    elif extra.lower().startswith("on update current_timestamp"):
        extra = "ON UPDATE " + extra[len("on update "):].upper()
    default = info.get("Default")
    nullable = info.get("Null") == "YES"
    if default is None:
        default = "NULL" if nullable else ""
    return {"name": info["Field"], "type": base.upper(), "length": length, "unsigned": "unsigned" in rest,
            "zerofill": "zerofill" in rest, "null": nullable, "default": str(default), "extra": extra,
            "comment": info.get("Comment") or "", "collation": info.get("Collation") or "",
            "pk": info["Field"] in pk_cols, "generated": generated}


class TableTab(QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        self.session = self.db = self.table = None
        self.is_new = False
        self.orig_cols, self.orig_opts, self.orig_pk = [], {}, []
        self._loaded_key = None
        self.dirty = False

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.title = muted("")
        self.b_save = tool("save", "Save changes (Ctrl+S)", self.save, "Save")
        lay.addWidget(bar(self.b_save, tool("x", "Discard changes", self.discard, "Discard"), "|", self.title, None,
                          tool("refresh", "Reload", self.reload)))

        # options
        opts = QFrame()
        opts.setObjectName("banner")
        form = QFormLayout(opts)
        form.setContentsMargins(14, 10, 14, 10)
        form.setHorizontalSpacing(12)
        row1, row2 = QHBoxLayout(), QHBoxLayout()
        self.o_name = QLineEdit()
        self.o_comment = QLineEdit()
        self.o_engine = QComboBox()
        self.o_engine.setEditable(True)
        self.o_coll = QComboBox()
        self.o_coll.setEditable(True)
        row1.setSpacing(8)
        row1.addWidget(self.o_name, 2)
        row1.addSpacing(10)
        row1.addWidget(muted("Engine"))
        row1.addWidget(self.o_engine, 1)
        row1.addSpacing(10)
        row1.addWidget(muted("Collation"))
        row1.addWidget(self.o_coll, 1)
        row2.addWidget(self.o_comment)
        form.addRow("Table name", row1)
        form.addRow("Comment", row2)
        for w in (self.o_name, self.o_comment):
            w.textEdited.connect(self._mark_dirty)
        self.o_engine.currentTextChanged.connect(self._mark_dirty)
        self.o_coll.currentTextChanged.connect(self._mark_dirty)
        lay.addWidget(opts)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("subtabs")
        lay.addWidget(self.tabs, 1)

        # columns
        cols_page = QWidget()
        cl = QVBoxLayout(cols_page)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)
        cl.addWidget(bar(tool("plus", "Add column (Ins)", self.add_column, "Add"),
                         tool("trash", "Remove column", self.remove_column, "Remove"), "|",
                         tool("up", "Move up", lambda: self.move(-1)), tool("down", "Move down", lambda: self.move(1)),
                         None, muted("Tip: tick PK to make a column part of the primary key")))
        self.cols = QTableWidget(0, len(COLS))
        self.cols.setHorizontalHeaderLabels(COLS)
        self.cols.setAlternatingRowColors(True)
        self.cols.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.cols.setSelectionMode(QAbstractItemView.SingleSelection)
        self.cols.verticalHeader().setDefaultSectionSize(28)
        hh = self.cols.horizontalHeader()
        hh.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        for c, w in ((C_PK, 40), (C_NAME, 180), (C_TYPE, 140), (C_LEN, 120), (C_UNS, 70), (C_NULL, 80),
                     (C_DEF, 160), (C_EXTRA, 210)):
            self.cols.setColumnWidth(c, w)
        hh.setSectionResizeMode(C_COMMENT, QHeaderView.Stretch)
        self.cols.itemChanged.connect(self._mark_dirty)
        cl.addWidget(self.cols, 1)
        self.tabs.addTab(cols_page, "Columns")

        # indexes
        idx_page = QWidget()
        il = QVBoxLayout(idx_page)
        il.setContentsMargins(0, 0, 0, 0)
        il.setSpacing(0)
        il.addWidget(bar(tool("plus", "Add index", self.add_index, "Add index"),
                         tool("trash", "Drop index", self.drop_index, "Drop index"), None))
        self.idx_grid = DataGrid()
        self.idx_grid.setSelectionBehavior(QAbstractItemView.SelectRows)
        il.addWidget(self.idx_grid, 1)
        self.tabs.addTab(idx_page, "Indexes")

        # foreign keys
        fk_page = QWidget()
        fl = QVBoxLayout(fk_page)
        fl.setContentsMargins(0, 0, 0, 0)
        fl.setSpacing(0)
        fl.addWidget(bar(tool("plus", "Add foreign key", self.add_fk, "Add foreign key"),
                         tool("trash", "Drop foreign key", self.drop_fk, "Drop foreign key"), None))
        self.fk_grid = DataGrid()
        self.fk_grid.setSelectionBehavior(QAbstractItemView.SelectRows)
        fl.addWidget(self.fk_grid, 1)
        self.tabs.addTab(fk_page, "Foreign keys")

        self.code = read_only_code_view()
        self.tabs.addTab(self.code, "CREATE code")

    # ── loading ──
    def set_table(self, session, db, table):
        self.session, self.db, self.table, self.is_new = session, db, table, False
        if self.isVisible():
            self._ensure()

    def new_table(self, session, db):
        self.session, self.db, self.table, self.is_new = session, db, None, True
        self._loaded_key = ("new", id(session), db)
        self._populate([], [], [], {"name": "", "engine": "InnoDB", "collation": "", "comment": ""}, "")
        self.cols.blockSignals(True)
        self._add_row({"name": "id", "type": "INT", "length": "", "unsigned": True, "null": False, "default": "",
                       "extra": "AUTO_INCREMENT", "comment": "", "pk": True})
        self.cols.blockSignals(False)
        self.dirty = True
        self.o_name.setFocus()
        self._load_choices()

    def showEvent(self, e):
        super().showEvent(e)
        self._ensure()

    def _ensure(self):
        if self.session and self.table and not self.is_new and \
                self._loaded_key != (id(self.session), self.db, self.table):
            self.reload()

    def reload(self):
        if self.is_new or not (self.session and self.table):
            return
        self._loaded_key = (id(self.session), self.db, self.table)
        conn, db, table = self.session.conn, self.db, self.table
        self.title.setText(f"Loading {db}.{table}…")

        def work():
            pk = conn.primary_key(db, table)
            cols = [parse_column(c, pk) for c in conn.columns(db, table)]
            idx = conn.indexes(db, table)
            fks = conn.foreign_keys(db, table)
            row = conn.rows("SELECT ENGINE, TABLE_COLLATION, TABLE_COMMENT FROM information_schema.TABLES "
                            "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s", (db, table))
            engine, coll, comment = row[0] if row else ("", "", "")
            code = conn.create_code(db, table, "table")
            return cols, idx, fks, {"name": table, "engine": engine or "", "collation": coll or "",
                                    "comment": comment or ""}, code

        run_async(work, lambda r: self._populate(*r), lambda e: self.main.status(e, error=True))
        self._load_choices()

    def _load_choices(self):
        if getattr(self, "_choices_for", None) is self.session:
            return
        self._choices_for = self.session
        conn = self.session.conn

        def done(res):
            engines, colls = res
            cur_e, cur_c = self.o_engine.currentText(), self.o_coll.currentText()
            for combo, items, cur in ((self.o_engine, engines, cur_e), (self.o_coll, colls, cur_c)):
                combo.blockSignals(True)
                combo.clear()
                combo.addItems(items)
                combo.setCurrentText(cur)
                combo.blockSignals(False)
        run_async(lambda: (conn.engines(), conn.collations()), done)

    def _populate(self, cols, idx, fks, opts, code):
        self.orig_cols = [dict(c) for c in cols]
        self.orig_pk = [c["name"] for c in cols if c["pk"]]
        self.orig_opts = dict(opts)
        for w, key in ((self.o_name, "name"), (self.o_comment, "comment")):
            w.setText(opts[key])
        for combo, key in ((self.o_engine, "engine"), (self.o_coll, "collation")):
            combo.blockSignals(True)
            combo.setCurrentText(opts[key])
            combo.blockSignals(False)
        self.cols.blockSignals(True)
        self.cols.setRowCount(0)
        for c in cols:
            self._add_row(c, orig=c["name"])
        self.cols.blockSignals(False)
        self.idx_grid.set_model(ResultModel(["Name", "Type", "Columns"],
                                            [[i["name"], i["type"], ", ".join(i["columns"])] for i in idx]))
        self.fk_grid.set_model(ResultModel(
            ["Name", "Columns", "References", "On update", "On delete"],
            [[f["name"], ", ".join(f["columns"]), f"{f['ref_table']} ({', '.join(f['ref_columns'])})",
              f["on_update"], f["on_delete"]] for f in fks]))
        self.code.setPlainText(code)
        self.title.setText("New table" if self.is_new else f"{self.db}.{opts['name']}  ·  {len(cols)} columns")
        for i in (1, 2):
            self.tabs.setTabEnabled(i, not self.is_new)
        self.dirty = False
        self._update_save()

    # ── column rows ──
    def _check_item(self, checked):
        it = QTableWidgetItem()
        it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable)
        it.setCheckState(Qt.Checked if checked else Qt.Unchecked)
        return it

    def _add_row(self, c, orig=None, at=None):
        r = self.cols.rowCount() if at is None else at
        self.cols.insertRow(r)
        self.cols.setItem(r, C_PK, self._check_item(c.get("pk")))
        name = QTableWidgetItem(c["name"])
        name.setData(Qt.UserRole, orig)
        name.setData(Qt.UserRole + 1, c)
        name.setIcon(icons.icon("key") if c.get("pk") else icons.icon("column"))
        self.cols.setItem(r, C_NAME, name)
        tcombo = QComboBox()
        tcombo.setEditable(True)
        tcombo.addItems(TYPES)
        tcombo.setCurrentText(c["type"])
        tcombo.currentTextChanged.connect(self._mark_dirty)
        self.cols.setCellWidget(r, C_TYPE, tcombo)
        self.cols.setItem(r, C_LEN, QTableWidgetItem(c.get("length", "")))
        self.cols.setItem(r, C_UNS, self._check_item(c.get("unsigned")))
        self.cols.setItem(r, C_NULL, self._check_item(c.get("null")))
        self.cols.setItem(r, C_DEF, QTableWidgetItem(c.get("default", "")))
        ecombo = QComboBox()
        ecombo.setEditable(True)
        ecombo.addItems(EXTRAS)
        ecombo.setCurrentText(c.get("extra", ""))
        ecombo.currentTextChanged.connect(self._mark_dirty)
        self.cols.setCellWidget(r, C_EXTRA, ecombo)
        self.cols.setItem(r, C_COMMENT, QTableWidgetItem(c.get("comment", "")))
        if c.get("generated"):
            for col in range(len(COLS)):
                it = self.cols.item(r, col)
                if it:
                    it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                    it.setToolTip("Generated column — edit it with ALTER TABLE in a query tab")
            self.cols.cellWidget(r, C_TYPE).setEnabled(False)
            self.cols.cellWidget(r, C_EXTRA).setEnabled(False)

    def _row(self, r):
        base = self.cols.item(r, C_NAME).data(Qt.UserRole + 1) or {}
        return {
            "orig": self.cols.item(r, C_NAME).data(Qt.UserRole),
            "name": self.cols.item(r, C_NAME).text().strip(),
            "type": self.cols.cellWidget(r, C_TYPE).currentText().strip(),
            "length": self.cols.item(r, C_LEN).text().strip(),
            "unsigned": self.cols.item(r, C_UNS).checkState() == Qt.Checked,
            "zerofill": base.get("zerofill", False),
            "collation": base.get("collation", ""),
            "null": self.cols.item(r, C_NULL).checkState() == Qt.Checked,
            "default": self.cols.item(r, C_DEF).text(),
            "extra": self.cols.cellWidget(r, C_EXTRA).currentText().strip(),
            "comment": self.cols.item(r, C_COMMENT).text(),
            "pk": self.cols.item(r, C_PK).checkState() == Qt.Checked,
            "generated": base.get("generated", False),
        }

    def _rows(self):
        return [self._row(r) for r in range(self.cols.rowCount())]

    def add_column(self):
        r = self.cols.currentRow() + 1 if self.cols.currentRow() >= 0 else self.cols.rowCount()
        n = self.cols.rowCount() + 1
        self._add_row({"name": f"column_{n}", "type": "VARCHAR", "length": "255", "unsigned": False,
                       "null": True, "default": "NULL", "extra": "", "comment": ""}, at=r)
        self.cols.setCurrentCell(r, C_NAME)
        self.cols.editItem(self.cols.item(r, C_NAME))
        self._mark_dirty()

    def remove_column(self):
        r = self.cols.currentRow()
        if r >= 0:
            self.cols.removeRow(r)
            self._mark_dirty()

    def move(self, delta):
        r = self.cols.currentRow()
        t = r + delta
        if r < 0 or not (0 <= t < self.cols.rowCount()):
            return
        data = self._row(r)
        orig = data.pop("orig")
        base = self.cols.item(r, C_NAME).data(Qt.UserRole + 1)
        self.cols.blockSignals(True)
        self.cols.removeRow(r)
        self._add_row(data, orig=orig, at=t)
        self.cols.item(t, C_NAME).setData(Qt.UserRole + 1, base)
        self.cols.blockSignals(False)
        self.cols.setCurrentCell(t, C_NAME)
        self._mark_dirty()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Insert:
            self.add_column()
            return
        super().keyPressEvent(e)

    def _mark_dirty(self, *_):
        self.dirty = True
        self._update_save()
        self.cols.blockSignals(True)
        for r in range(self.cols.rowCount()):
            it = self.cols.item(r, C_NAME)
            pk = self.cols.item(r, C_PK)
            if it and pk:
                it.setIcon(icons.icon("key") if pk.checkState() == Qt.Checked else icons.icon("column"))
        self.cols.blockSignals(False)

    def _update_save(self):
        self.b_save.setEnabled(self.dirty)
        self.b_save.setText("Save •" if self.dirty else "Save")

    # ── SQL generation ──
    def build_sql(self):
        rows = self._rows()
        name = self.o_name.text().strip()
        if not name:
            raise ValueError("Table name is required.")
        names = [r["name"] for r in rows]
        if any(not n for n in names):
            raise ValueError("Every column needs a name.")
        if len(set(n.lower() for n in names)) != len(names):
            raise ValueError("Column names must be unique.")
        pk = [r["name"] for r in rows if r["pk"]]
        engine, coll = self.o_engine.currentText().strip(), self.o_coll.currentText().strip()
        comment = self.o_comment.text()

        if self.is_new:
            if not rows:
                raise ValueError("Add at least one column.")
            parts = ["  " + column_sql(r) for r in rows]
            if pk:
                parts.append(f"  PRIMARY KEY ({', '.join(qi(p) for p in pk)})")
            sql = f"CREATE TABLE {qt(self.db, name)} (\n" + ",\n".join(parts) + "\n)"
            if engine:
                sql += f" ENGINE={engine}"
            if coll:
                sql += f" COLLATE={coll}"
            if comment:
                sql += f" COMMENT={literal(comment)}"
            return sql

        clauses = []
        orig_names = [c["name"] for c in self.orig_cols]
        orig_by_name = {c["name"]: c for c in self.orig_cols}
        kept = [r["orig"] for r in rows if r["orig"]]
        if pk != self.orig_pk and self.orig_pk:
            clauses.append("DROP PRIMARY KEY")
        for n in orig_names:
            if n not in kept:
                clauses.append(f"DROP COLUMN {qi(n)}")
        orig_order = [n for n in orig_names if n in kept]
        for i, r in enumerate(rows):
            pos = " FIRST" if i == 0 else f" AFTER {qi(rows[i - 1]['name'])}"
            if not r["orig"]:
                clauses.append(f"ADD COLUMN {column_sql(r)}{pos}")
                continue
            if r["generated"]:
                continue
            moved = orig_order.index(r["orig"]) != kept.index(r["orig"])
            changed = column_sql(r) != column_sql(orig_by_name[r["orig"]])
            if changed or moved:
                clauses.append(f"CHANGE COLUMN {qi(r['orig'])} {column_sql(r)}{pos if moved else ''}")
        if pk != self.orig_pk and pk:
            clauses.append(f"ADD PRIMARY KEY ({', '.join(qi(p) for p in pk)})")
        if engine and engine != self.orig_opts.get("engine"):
            clauses.append(f"ENGINE={engine}")
        if coll and coll != self.orig_opts.get("collation"):
            clauses.append(f"COLLATE={coll}")
        if comment != self.orig_opts.get("comment"):
            clauses.append(f"COMMENT={literal(comment)}")
        if name != self.table:
            clauses.append(f"RENAME TO {qt(self.db, name)}")
        if not clauses:
            return ""
        return f"ALTER TABLE {qt(self.db, self.table)}\n  " + ",\n  ".join(clauses)

    def save(self):
        if not self.session:
            return
        try:
            sql = self.build_sql()
        except ValueError as exc:
            QMessageBox.warning(self, "Table designer", str(exc))
            return
        if not sql:
            self.dirty = False
            self._update_save()
            self.main.status("Nothing to save.")
            return
        if not confirm_sql(self, "Save table", sql + ";"):
            return
        conn, new_name = self.session.conn, self.o_name.text().strip()

        def done(_):
            was_new = self.is_new
            self.is_new, self.table = False, new_name
            self._loaded_key = None
            self.reload()
            self.main.status(f"Table {new_name} {'created' if was_new else 'saved'}.")
            self.main.refresh_db(self.session, self.db, select=new_name)
        run_async(lambda: conn.execute(sql), done, lambda e: QMessageBox.critical(self, "Save failed", e))

    def discard(self):
        if self.is_new:
            self.new_table(self.session, self.db)
        else:
            self._loaded_key = None
            self.reload()

    def maybe_save(self):
        """Ask about unsaved designer changes. Returns False to abort navigation."""
        if not self.dirty:
            return True
        box = QMessageBox(QMessageBox.Question, "Unsaved changes", "Save changes to the table design?",
                          QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel, self)
        res = box.exec()
        if res == QMessageBox.Save:
            self.save()
            return False
        if res == QMessageBox.Discard:
            self.dirty = False
            return True
        return False

    # ── indexes / foreign keys ──
    def _alter(self, clause, title):
        sql = f"ALTER TABLE {qt(self.db, self.table)} {clause}"
        if confirm_sql(self, title, sql + ";"):
            conn = self.session.conn
            run_async(lambda: conn.execute(sql), lambda *_: (setattr(self, "_loaded_key", None), self.reload()),
                      lambda e: QMessageBox.critical(self, title, e))

    def add_index(self):
        dlg = IndexDialog(self, [c["name"] for c in self.orig_cols])
        if dlg.exec() and dlg.clause():
            self._alter(dlg.clause(), "Add index")

    def drop_index(self):
        idx = self.idx_grid.currentIndex()
        if not idx.isValid():
            return
        name = self.idx_grid.model().rows[idx.row()][0]
        self._alter("DROP PRIMARY KEY" if name == "PRIMARY" else f"DROP INDEX {qi(name)}", "Drop index")

    def add_fk(self):
        conn, db = self.session.conn, self.db
        tables = [o["name"] for o in self.main.cached_objects(self.session, db) if o["kind"] == "table"]
        dlg = ForeignKeyDialog(self, self.table, [c["name"] for c in self.orig_cols], tables,
                               lambda t: [c["Field"] for c in conn.columns(db, t)])
        if dlg.exec():
            self._alter(dlg.clause(), "Add foreign key")

    def drop_fk(self):
        idx = self.fk_grid.currentIndex()
        if idx.isValid():
            name = self.fk_grid.model().rows[idx.row()][0]
            if confirm(self, "Drop foreign key", f"Drop foreign key {name}?", danger=True):
                self._alter(f"DROP FOREIGN KEY {qi(name)}", "Drop foreign key")

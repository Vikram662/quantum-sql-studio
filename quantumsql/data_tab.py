"""Data tab: browse and edit table rows with filtering, sorting and paging."""
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QAbstractItemView, QLineEdit, QMessageBox, QSpinBox, QVBoxLayout, QWidget

from . import icons
from .config import settings
from .db import literal, qi, qt, run_async
from .grid import DataGrid, ResultModel, export_rows_dialog
from .ui import bar, confirm, muted, tool


class DataTab(QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        self.session = self.db = self.table = None
        self.kind = "table"
        self.key_cols = []
        self.offset = 0
        self.total = 0
        self.sort = None  # (column, "ASC"|"DESC")
        self._loaded_key = None
        self.model = None
        self._saving = False
        self.undo_stacks = {}  # (session id, db, table) -> [{"time", "what", "stmts"}]

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self.title = muted("")
        self.b_insert = tool("plus", "Insert row (Ins)", self.insert_row, "Insert")
        self.b_delete = tool("trash", "Delete selected rows (Ctrl+Del)", self.delete_rows, "Delete")
        self.b_null = tool("x", "Set selected cells to NULL (Ctrl+Shift+N)", self.set_null, "NULL")
        self.b_save = tool("check", "Save changes (Ctrl+S)", self.save, "Save")
        self.b_discard = tool("x", "Discard changes", self.discard, "Discard")
        self.b_undo = tool("undo", "Undo the last saved change", self.undo, "Undo")
        lay.addWidget(bar(self.b_insert, self.b_delete, self.b_null, "|", self.b_save, self.b_discard, self.b_undo,
                          "|",
                          tool("export", "Export rows", self._export, "Export"), "|", self.title, None,
                          tool("refresh", "Refresh (F5)", self.refresh)))

        self.where = QLineEdit()
        self.where.setPlaceholderText("WHERE …   e.g.  status = 'active' AND id > 100   (Enter to apply)")
        self.where.addAction(icons.icon("filter"), QLineEdit.LeadingPosition)
        self.where.returnPressed.connect(self._apply_filter)
        self.limit = QSpinBox()
        self.limit.setRange(10, 1_000_000)
        self.limit.setSingleStep(500)
        self.limit.setValue(int(settings.get("data_limit", 1000)))
        self.limit.setPrefix("Limit ")
        self.limit.editingFinished.connect(self._limit_changed)
        self.page = muted("")
        lay.addWidget(bar(self.where, tool("x", "Clear filter", self._clear_filter), "|", self.limit,
                          tool("first", "First page", lambda: self._go(0)),
                          tool("left", "Previous page", lambda: self._go(self.offset - self.limit.value())),
                          self.page,
                          tool("right", "Next page", lambda: self._go(self.offset + self.limit.value())),
                          tool("last", "Last page", self._last), object_name="banner"))
        self.where.setMinimumWidth(320)

        self.grid = DataGrid()
        self.grid.horizontalHeader().setSortIndicatorShown(True)
        self.grid.horizontalHeader().sectionClicked.connect(self._header_clicked)
        self.grid.menu_hook = self._grid_menu
        lay.addWidget(self.grid, 1)

        for keys, fn in (("Ins", self.insert_row), ("Ctrl+Del", self.delete_rows),
                         ("Ctrl+Shift+N", self.set_null)):
            sc = QShortcut(QKeySequence(keys), self.grid)
            sc.setContext(Qt.WidgetWithChildrenShortcut)
            sc.activated.connect(fn)
        self._update_buttons()

    # ── loading ──
    def set_table(self, session, db, table, kind="table"):
        if (session, db, table) != (self.session, self.db, self.table):
            self.offset, self.sort = 0, None
            self.where.clear()
        self.session, self.db, self.table, self.kind = session, db, table, kind
        if self.isVisible():
            self._ensure()

    def showEvent(self, e):
        super().showEvent(e)
        self._ensure()

    def _ensure(self):
        if self.session and self.table and self._loaded_key != (id(self.session), self.db, self.table):
            self.refresh()

    def refresh(self):
        if not (self.session and self.table):
            return
        self._loaded_key = (id(self.session), self.db, self.table)
        conn, db, table = self.session.conn, self.db, self.table
        where = self.where.text().strip()
        limit, offset, sort = self.limit.value(), self.offset, self.sort
        sql = f"SELECT * FROM {qt(db, table)}"
        if where:
            sql += f" WHERE {where}"
        if sort:
            sql += f" ORDER BY {qi(sort[0])} {sort[1]}"
        sql += f" LIMIT {limit} OFFSET {offset}"
        count_sql = f"SELECT COUNT(*) FROM {qt(db, table)}" + (f" WHERE {where}" if where else "")
        self.title.setText(f"Loading {db}.{table}…")

        def work():
            keys = conn.primary_key(db, table) if self.kind == "table" else []
            if not keys and self.kind == "table":
                uniq = [i for i in conn.indexes(db, table) if i["type"] == "UNIQUE"]
                keys = [c.split("(")[0] for c in uniq[0]["columns"]] if uniq else []
            headers, rows, _ = conn.query(sql)
            total = conn.rows(count_sql)[0][0]
            return keys, headers, rows, total

        run_async(work, self._loaded, self._failed)

    def _failed(self, msg):
        self.title.setText("Error")
        QMessageBox.critical(self, "Data", msg)

    def _loaded(self, res):
        keys, headers, rows, total = res
        self.key_cols, self.total = keys, total
        self.model = ResultModel(headers, rows, editable=(self.kind == "table"), offset=self.offset)
        self.model.changed.connect(self._update_buttons)
        self.model.changed.connect(self._schedule_autosave)
        self.grid.table_name = self.table
        self.grid.set_model(self.model)
        self.grid.selectionModel().currentRowChanged.connect(self._row_left)
        if self.sort and self.sort[0] in headers:
            self.grid.horizontalHeader().setSortIndicator(
                headers.index(self.sort[0]), Qt.AscendingOrder if self.sort[1] == "ASC" else Qt.DescendingOrder)
        else:
            self.grid.horizontalHeader().setSortIndicator(-1, Qt.AscendingOrder)
        key_note = f"key: {', '.join(keys)}" if keys else "no primary key — edits match all columns"
        self.title.setText(f"{self.db}.{self.table}  ·  {total:,} rows  ·  {key_note}")
        end = self.offset + len(rows)
        self.page.setText(f"{self.offset + 1 if rows else 0:,}–{end:,} of {total:,}")
        self._update_buttons()

    def _update_buttons(self):
        editable = bool(self.model and self.model.editable)
        changes = self.model.change_count() if self.model else 0
        for b in (self.b_insert, self.b_delete, self.b_null):
            b.setEnabled(editable)
        self.b_save.setEnabled(changes > 0)
        self.b_discard.setEnabled(changes > 0)
        self.b_save.setText(f"Save ({changes})" if changes else "Save")
        stack = self._undo_stack()
        self.b_undo.setEnabled(bool(stack) and not self._saving)
        self.b_undo.setText(f"Undo ({len(stack)})" if stack else "Undo")
        self.b_undo.setToolTip(f"Undo: {stack[-1]['what']} ({stack[-1]['time']})\n\n"
                               + "\n".join(s[:160] for s in stack[-1]["stmts"][:8]) if stack
                               else "Nothing to undo — saved changes on this table can be undone here")

    # ── navigation ──
    def maybe_save(self):
        if not (self.model and self.model.has_changes()):
            return True
        res = QMessageBox.question(self, "Unsaved changes", f"Save {self.model.change_count()} pending row change(s)?",
                                   QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if res == QMessageBox.Save:
            self.save()
            return False
        if res == QMessageBox.Discard:
            self.model.discard()
            return True
        return False

    def _go(self, offset):
        if not self.maybe_save():
            return
        offset = max(0, min(offset, max(0, self.total - 1)))
        if offset != self.offset:
            self.offset = offset
            self.refresh()

    def _last(self):
        lim = self.limit.value()
        self._go(max(0, (self.total - 1) // lim * lim))

    def _limit_changed(self):
        settings.set("data_limit", self.limit.value())
        self.offset = 0
        if self.maybe_save():
            self.refresh()

    def _apply_filter(self):
        if self.maybe_save():
            self.offset = 0
            self.refresh()

    def _clear_filter(self):
        self.where.clear()
        self._apply_filter()

    def _header_clicked(self, section):
        if not self.model or not self.maybe_save():
            return
        col = self.model.headers[section]
        if not self.sort or self.sort[0] != col:
            self.sort = (col, "ASC")
        elif self.sort[1] == "ASC":
            self.sort = (col, "DESC")
        else:
            self.sort = None
        self.offset = 0
        self.refresh()

    # ── editing ──
    def insert_row(self):
        if self.model and self.model.editable:
            idx = self.model.add_row()
            self.grid.scrollTo(idx)
            self.grid.setCurrentIndex(idx)
            self.grid.edit(idx)

    def set_null(self):
        if self.model and self.model.editable:
            self.model.set_null(self.grid.selectedIndexes())

    def discard(self):
        if self.model:
            self.model.discard()

    # Edited cells of existing rows are written as soon as the editor closes;
    # a new row is inserted once the cursor leaves it, so it is not inserted half-filled.
    def _schedule_autosave(self):
        if self.model and not self._saving and self.model.pending_updates():
            QTimer.singleShot(0, self._autosave_updates)

    def _row_left(self, current, previous):
        model = self.model
        if (model and previous.isValid() and previous.row() < len(model.rows) and model.is_new(previous.row())
                and current.row() != previous.row()):
            QTimer.singleShot(0, self.save)

    def _autosave_updates(self):
        model = self.model
        if not model or self._saving:
            return
        rows = model.pending_updates()
        if not rows:
            return
        snapshots = {id(r): list(r) for r in rows}
        stmts, undo = model.pending_statements(self.db, self.table, self.key_cols, include_new=False,
                                               with_undo=True)
        self._saving = True
        key = self._undo_key()

        def done(res):
            n, reverse = res
            self._saving = False
            self._push_undo(key, f"edit of {n} row(s)", reverse)
            if self.model is not model:
                return
            model.mark_saved(snapshots)
            self.main.status(f"Saved {n} row change(s) to {self.table}.")

        def failed(e):
            self._saving = False
            QMessageBox.critical(self, "Save failed — nothing was changed", e)

        conn, ctx = self.session.conn, (self.db, self.table, list(self.key_cols))
        run_async(lambda: self._run_statements(conn, stmts, undo, ctx), done, failed)

    @staticmethod
    def _run_statements(conn, stmts, undo=None, ctx=None):
        """Run stmts in one transaction. Returns (count, reverse statements in undo order)."""
        reverse = []
        with conn.lock:
            conn.query("START TRANSACTION", log=False)
            try:
                for i, s in enumerate(stmts):
                    n = conn.execute(s)
                    if n == 0 and s.startswith("UPDATE"):
                        raise RuntimeError("Row not found (it was changed or deleted by someone else):\n" + s)
                    spec = undo[i] if undo else None
                    if isinstance(spec, str):
                        reverse.append(spec)
                    elif isinstance(spec, dict):
                        reverse.append(DataTab._undo_insert(conn, ctx, spec["inserted"]))
                conn.query("COMMIT", log=False)
            except Exception:
                conn.query("ROLLBACK", log=False)
                raise
        return len(stmts), [r for r in reversed(reverse) if r]

    @staticmethod
    def _undo_insert(conn, ctx, values):
        db, table, key_cols = ctx
        target = qt(db, table)
        if key_cols and all(k in values for k in key_cols):
            cond = {k: values[k] for k in key_cols}
        elif len(key_cols) == 1:
            new_id = conn.rows("SELECT LAST_INSERT_ID()")[0][0]
            if not new_id:
                return None
            cond = {key_cols[0]: new_id}
        elif values:
            cond = values
        else:
            return None
        where = " AND ".join(f"{qi(c)} IS NULL" if v is None else f"{qi(c)}={literal(v)}" for c, v in cond.items())
        return f"DELETE FROM {target} WHERE {where} LIMIT 1"

    # ── undo ──
    def _undo_key(self, session=None, db=None, table=None):
        return (id(session or self.session), db or self.db, table or self.table)

    def _undo_stack(self):
        return self.undo_stacks.get(self._undo_key(), []) if self.session else []

    def _push_undo(self, key, what, stmts):
        if stmts:
            stack = self.undo_stacks.setdefault(key, [])
            stack.append({"time": time.strftime("%H:%M:%S"), "what": what, "stmts": stmts})
            del stack[:-50]
        self._update_buttons()

    def undo(self):
        stack = self._undo_stack()
        if not stack or self._saving:
            return
        if self.model and self.model.has_changes() and not confirm(
                self, "Undo", "Unsaved edits on this page will be discarded. Continue?"):
            return
        entry = stack[-1]
        stmts = entry["stmts"]
        conn = self.session.conn
        self._saving = True

        def done(_):
            self._saving = False
            stack.pop()
            if self.model:
                self.model.discard()
            self.main.status(f"Undid {entry['what']} on {self.table}.")
            self._loaded_key = None
            self.refresh()

        def failed(e):
            self._saving = False
            self._update_buttons()
            QMessageBox.critical(self, "Undo failed — nothing was changed",
                                 f"{e}\n\nThe row may have been changed again since it was saved.")
        run_async(lambda: self._run_statements(conn, stmts), done, failed)

    def save(self):
        if not (self.model and self.model.has_changes()) or self._saving:
            return
        stmts, undo = self.model.pending_statements(self.db, self.table, self.key_cols, with_undo=True)
        self._saving = True
        key = self._undo_key()

        def done(res):
            n, reverse = res
            self._saving = False
            self._push_undo(key, f"save of {n} row change(s)", reverse)
            self.main.status(f"Saved {n} row change(s) to {self.table}.")
            self._loaded_key = None
            self.model.original.clear()
            self.model.modified.clear()
            self.model.new_rows.clear()
            self.refresh()

        def failed(e):
            self._saving = False
            QMessageBox.critical(self, "Save failed — nothing was changed", e)

        conn, ctx = self.session.conn, (self.db, self.table, list(self.key_cols))
        run_async(lambda: self._run_statements(conn, stmts, undo, ctx), done, failed)

    def delete_rows(self):
        if not (self.model and self.model.editable):
            return
        rows = sorted({i.row() for i in self.grid.selectedIndexes()})
        if not rows:
            return
        new = [r for r in rows if self.model.is_new(r)]
        existing = [r for r in rows if not self.model.is_new(r)]
        if not existing:
            self.model.remove_rows(new)
            return
        if not confirm(self, "Delete rows", f"Delete {len(existing)} row(s) from {self.table}?", danger=True):
            return
        limit = "" if self.key_cols else " LIMIT 1"
        originals = [self.model.original.get(id(self.model.rows[r]), self.model.rows[r]) for r in existing]
        stmts = [f"DELETE FROM {qt(self.db, self.table)} WHERE {self.model.where_clause(o, self.key_cols)}{limit}"
                 for o in originals]
        undo = [self.model.reinsert_statement(self.db, self.table, o) for o in originals]
        conn, key = self.session.conn, self._undo_key()

        def work():
            return self._run_statements(conn, stmts, undo)

        def done(res):
            self._push_undo(key, f"delete of {len(existing)} row(s)", res[1])
            self.model.remove_rows(rows)
            self.total -= len(existing)
            self.main.status(f"Deleted {len(existing)} row(s).")
        run_async(work, done, lambda e: QMessageBox.critical(self, "Delete failed", e))

    def _grid_menu(self, menu):
        if self.model and self.model.editable:
            menu.addAction(icons.icon("plus"), "Insert row\tIns", self.insert_row)
            menu.addAction(icons.icon("trash"), "Delete selected rows\tCtrl+Del", self.delete_rows)
            menu.addAction("Set NULL\tCtrl+Shift+N", self.set_null)
        idx = self.grid.currentIndex()
        if idx.isValid() and self.model:
            col = self.model.headers[idx.column()]
            val = self.model.rows[idx.row()][idx.column()]
            if val is not None and not isinstance(val, (bytes, bytearray)):
                cond = f"{qi(col)} = {literal(val)}"
                menu.addAction(icons.icon("filter"), f"Filter: {cond[:60]}",
                               lambda: (self.where.setText(cond), self._apply_filter()))

    def _export(self):
        if self.model:
            export_rows_dialog(self, self.model.headers, self.model.rows, self.table)

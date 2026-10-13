"""Result/data grid: table model with inline editing, view with copy/export."""
import csv
import datetime
import decimal
import io
import json

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QKeySequence
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QDialog, QDialogButtonBox, QFileDialog, QHeaderView,
                               QLabel, QMenu, QMessageBox, QPlainTextEdit, QTableView, QVBoxLayout)

from . import icons, theme
from .db import literal, qi, qt, to_text
from .theme import T

MAX_CELL_CHARS = 400
_NUMERIC = (int, float, decimal.Decimal)


def display_text(value):
    if value is None:
        return "NULL"
    if isinstance(value, (bytes, bytearray)):
        try:
            text = bytes(value).decode("utf-8")
        except UnicodeDecodeError:
            h = bytes(value[:64]).hex()
            return f"0x{h}{'…' if len(value) > 64 else ''}"
    else:
        text = str(value)
    if len(text) > MAX_CELL_CHARS:
        text = text[:MAX_CELL_CHARS] + "…"
    return text.replace("\r\n", "↵").replace("\n", "↵").replace("\t", " ")


class _Untouched:
    """Marker for cells of a new row the user has not filled (column default applies)."""


UNTOUCHED = _Untouched()


class ResultModel(QAbstractTableModel):
    changed = Signal()

    def __init__(self, headers, rows, editable=False, offset=0, parent=None):
        super().__init__(parent)
        self.headers = list(headers)
        self.rows = [list(r) for r in rows]
        self.editable = editable
        self.offset = offset
        self.original = {}      # row object id -> original values
        self.modified = {}      # row object id -> set(col)
        self.new_rows = set()   # row object ids
        self._null_font = QFont()
        self._null_font.setItalic(True)

    # ── Qt model API ──
    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.headers)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        row = self.rows[index.row()]
        value = row[index.column()]
        rid = id(row)
        if role == Qt.DisplayRole:
            if value is UNTOUCHED:
                return "(default)"
            return display_text(value)
        if role == Qt.EditRole:
            return "" if value is UNTOUCHED else to_text(value)
        if role == Qt.ForegroundRole:
            if value is None or value is UNTOUCHED:
                return QColor(T["null"])
        if role == Qt.FontRole and (value is None or value is UNTOUCHED):
            return self._null_font
        if role == Qt.BackgroundRole:
            if rid in self.new_rows:
                return QColor(T["row_new"])
            if index.column() in self.modified.get(rid, ()):
                return QColor(T["cell_modified"])
        if role == Qt.TextAlignmentRole and isinstance(value, _NUMERIC) and not isinstance(value, bool):
            return int(Qt.AlignRight | Qt.AlignVCenter)
        if role == Qt.ToolTipRole and isinstance(value, str) and (len(value) > 60 or "\n" in value):
            return value[:2000]
        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal:
            return self.headers[section] if section < len(self.headers) else None
        return str(self.offset + section + 1)

    def flags(self, index):
        f = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        if self.editable and index.isValid():
            value = self.rows[index.row()][index.column()]
            if not (isinstance(value, (bytes, bytearray)) and display_text(value).startswith("0x")):
                f |= Qt.ItemIsEditable
        return f

    def setData(self, index, value, role=Qt.EditRole):
        if role != Qt.EditRole or not index.isValid():
            return False
        self._set_cell(index.row(), index.column(), value)
        return True

    def _set_cell(self, r, c, value):
        row = self.rows[r]
        old = row[c]
        if old is UNTOUCHED and value == "":
            return
        if old is not UNTOUCHED and value is not None and old is not None and to_text(old) == value:
            return
        if value is None and old is None:
            return
        rid = id(row)
        if rid not in self.new_rows and rid not in self.original:
            self.original[rid] = list(row)
        row[c] = value
        self.modified.setdefault(rid, set()).add(c)
        idx = self.index(r, c)
        self.dataChanged.emit(idx, idx)
        self.changed.emit()

    def set_null(self, indexes):
        for idx in indexes:
            if self.flags(idx) & Qt.ItemIsEditable:
                self._set_cell(idx.row(), idx.column(), None)

    def sort(self, column, order=Qt.AscendingOrder):
        if self.editable or column < 0:
            return
        self.layoutAboutToBeChanged.emit()

        def key(row):
            v = row[column]
            if v is None:
                return (0, 0, "")
            if isinstance(v, _NUMERIC) and not isinstance(v, bool):
                return (1, float(v), "")
            return (2, 0, str(v).lower())
        self.rows.sort(key=key, reverse=(order == Qt.DescendingOrder))
        self.layoutChanged.emit()

    # ── editing ──
    def has_changes(self):
        return bool(self.new_rows or any(self.modified.values()))

    def change_count(self):
        return len(self.new_rows) + len([k for k, v in self.modified.items() if v and k not in self.new_rows])

    def add_row(self, at=None):
        at = len(self.rows) if at is None else at
        row = [UNTOUCHED] * len(self.headers)
        self.beginInsertRows(QModelIndex(), at, at)
        self.rows.insert(at, row)
        self.endInsertRows()
        self.new_rows.add(id(row))
        self.changed.emit()
        return self.index(at, 0)

    def remove_rows(self, row_numbers):
        for r in sorted(set(row_numbers), reverse=True):
            row = self.rows[r]
            self.beginRemoveRows(QModelIndex(), r, r)
            del self.rows[r]
            self.endRemoveRows()
            self.new_rows.discard(id(row))
            self.modified.pop(id(row), None)
            self.original.pop(id(row), None)
        self.changed.emit()

    def is_new(self, r):
        return id(self.rows[r]) in self.new_rows

    def discard(self):
        self.beginResetModel()
        self.rows = [self.original.get(id(r), r) for r in self.rows if id(r) not in self.new_rows]
        self.original.clear()
        self.modified.clear()
        self.new_rows.clear()
        self.endResetModel()
        self.changed.emit()

    def pending_updates(self):
        """Existing (non-new) rows with unsaved cell edits."""
        return [r for r in self.rows if self.modified.get(id(r)) and id(r) not in self.new_rows]

    def mark_saved(self, snapshots):
        """Mark rows clean after their UPDATE ran. `snapshots` maps row id -> values
        that were written; cells edited again since then stay pending."""
        for row in self.rows:
            rid = id(row)
            snap = snapshots.get(rid)
            if snap is None:
                continue
            left = {c for c in self.modified.get(rid, ()) if row[c] is not snap[c] and row[c] != snap[c]}
            if left:
                self.original[rid] = list(snap)
                self.modified[rid] = left
            else:
                self.original.pop(rid, None)
                self.modified.pop(rid, None)
        if self.rows:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self.rows) - 1, len(self.headers) - 1))
        self.changed.emit()

    def where_clause(self, values, key_cols):
        """WHERE identifying a row by key columns (or every column when no key)."""
        cols = key_cols or [h for h in self.headers]
        parts = []
        for col in cols:
            v = values[self.headers.index(col)]
            parts.append(f"{qi(col)} IS NULL" if v is None else f"{qi(col)}={literal(v)}")
        return " AND ".join(parts)

    def pending_statements(self, db, table, key_cols, include_new=True, with_undo=False):
        """Statements that save the pending edits. With `with_undo`, returns (stmts, undo) where each undo
        entry reverses its statement: SQL text, or {"inserted": {column: value}} resolved after the insert."""
        stmts, undo = [], []
        target = qt(db, table)
        limit = "" if key_cols else " LIMIT 1"
        for row in self.rows:
            rid = id(row)
            if rid in self.new_rows:
                if not include_new:
                    continue
                cols = [i for i, v in enumerate(row) if v is not UNTOUCHED]
                if cols:
                    stmts.append(f"INSERT INTO {target} ({', '.join(qi(self.headers[i]) for i in cols)}) "
                                 f"VALUES ({', '.join(literal(row[i]) for i in cols)})")
                else:
                    stmts.append(f"INSERT INTO {target} () VALUES ()")
                undo.append({"inserted": {self.headers[i]: row[i] for i in cols}})
            elif self.modified.get(rid):
                orig = self.original.get(rid, row)
                changed = sorted(self.modified[rid])
                sets = ", ".join(f"{qi(self.headers[c])}={literal(row[c])}" for c in changed)
                stmts.append(f"UPDATE {target} SET {sets} WHERE {self.where_clause(orig, key_cols)}{limit}")
                back = ", ".join(f"{qi(self.headers[c])}={literal(orig[c])}" for c in changed)
                undo.append(f"UPDATE {target} SET {back} WHERE {self.where_clause(row, key_cols)}{limit}")
        return (stmts, undo) if with_undo else stmts

    def reinsert_statement(self, db, table, values):
        """INSERT that puts a deleted row back."""
        cols = [i for i, v in enumerate(values) if v is not UNTOUCHED]
        return (f"INSERT INTO {qt(db, table)} ({', '.join(qi(self.headers[i]) for i in cols)}) "
                f"VALUES ({', '.join(literal(values[i]) for i in cols)})")


# ── Text export ────────────────────────────────────────────────────────────
def _json_value(v):
    if v is None or isinstance(v, (bool, int, float)):
        return v
    if isinstance(v, decimal.Decimal):
        return float(v) if v % 1 else int(v)
    if isinstance(v, (datetime.date, datetime.datetime, datetime.time, datetime.timedelta)):
        return str(v)
    return to_text(v)


def rows_to_text(fmt, headers, rows, table="export", with_headers=True):
    if fmt in ("csv", "tsv"):
        buf = io.StringIO()
        w = csv.writer(buf, delimiter="," if fmt == "csv" else "\t", lineterminator="\n")
        if with_headers:
            w.writerow(headers)
        for r in rows:
            w.writerow(["NULL" if v is None and fmt == "tsv" else to_text(v) for v in r])
        return buf.getvalue()
    if fmt == "json":
        return json.dumps([{h: _json_value(v) for h, v in zip(headers, r)} for r in rows],
                          ensure_ascii=False, indent=2)
    if fmt == "sql":
        cols = ", ".join(qi(h) for h in headers)
        return "\n".join(f"INSERT INTO {qi(table)} ({cols}) VALUES ({', '.join(literal(v) for v in r)});"
                         for r in rows) + "\n"
    if fmt == "md":
        esc = lambda v: display_text(v).replace("|", "\\|")  # noqa: E731
        lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
        lines += ["| " + " | ".join(esc(v) for v in r) + " |" for r in rows]
        return "\n".join(lines) + "\n"
    if fmt == "html":
        import html
        out = ["<table>", "<tr>" + "".join(f"<th>{html.escape(h)}</th>" for h in headers) + "</tr>"]
        for r in rows:
            out.append("<tr>" + "".join(f"<td>{html.escape(to_text(v)) if v is not None else '<i>NULL</i>'}</td>"
                                        for v in r) + "</tr>")
        out.append("</table>")
        return "\n".join(out)
    raise ValueError(fmt)


EXPORT_FILTERS = {
    "CSV (*.csv)": "csv", "Excel workbook (*.xlsx)": "xlsx", "Tab separated (*.tsv)": "tsv", "JSON (*.json)": "json",
    "SQL INSERT statements (*.sql)": "sql", "Markdown table (*.md)": "md", "HTML table (*.html)": "html",
}


def export_rows_dialog(parent, headers, rows, table="export"):
    if not headers:
        QMessageBox.information(parent, "Export", "There is no data to export.")
        return
    path, flt = QFileDialog.getSaveFileName(parent, "Export grid rows", f"{table}.csv", ";;".join(EXPORT_FILTERS))
    if not path:
        return
    fmt = EXPORT_FILTERS.get(flt, "csv")
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    if ext in ("csv", "tsv", "json", "sql", "md", "html", "xlsx"):
        fmt = ext
    try:
        if fmt == "xlsx":
            from .xlsx import write_xlsx
            write_xlsx(path, headers, rows, table)
        else:
            with open(path, "w", encoding="utf-8-sig" if fmt == "csv" else "utf-8", newline="") as f:
                f.write(rows_to_text(fmt, headers, rows, table))
    except OSError as exc:
        QMessageBox.critical(parent, "Export failed", str(exc))
        return
    return path


class ValueDialog(QDialog):
    """Multi-line viewer/editor for a single cell value."""

    def __init__(self, parent, column, text, read_only=False):
        super().__init__(parent)
        self.setWindowTitle(f"Value — {column}")
        self.resize(640, 420)
        lay = QVBoxLayout(self)
        self.edit = QPlainTextEdit(text)
        self.edit.setFont(theme.code_font(10))
        self.edit.setReadOnly(read_only)
        lay.addWidget(self.edit)
        info = QLabel(f"{len(text):,} characters")
        info.setObjectName("muted")
        lay.addWidget(info)
        btns = QDialogButtonBox(QDialogButtonBox.Close if read_only else QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def text(self):
        return self.edit.toPlainText()


class DataGrid(QTableView):
    """Table view with copy, export and value-viewer support. Owners can add
    menu entries through `menu_hook(menu)`."""

    status_hook = None  # fn(text): receives Excel-style stats of the selected cells

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlternatingRowColors(True)
        self.setSelectionBehavior(QAbstractItemView.SelectItems)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setWordWrap(False)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed
                             | QAbstractItemView.AnyKeyPressed)
        self.verticalHeader().setDefaultSectionSize(24)
        self.verticalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self.horizontalHeader().setHighlightSections(False)
        self.horizontalHeader().setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.horizontalHeader().setSectionsMovable(True)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._context_menu)
        self.menu_hook = None
        self.table_name = "export"

    def set_model(self, model):
        self.setModel(model)
        self.autosize_columns()

    def setModel(self, model):
        super().setModel(model)
        if self.selectionModel() is not None:
            self.selectionModel().selectionChanged.connect(self._selection_stats)

    def _selection_stats(self, *_):
        hook = DataGrid.status_hook
        if hook is None:
            return
        idxs = self.selectedIndexes()
        model = self.model()
        if len(idxs) < 2 or not model or len(idxs) > 500_000:
            hook("")
            return
        nums, non_null = [], 0
        for i in idxs:
            v = model.rows[i.row()][i.column()]
            if v is None:
                continue
            non_null += 1
            if isinstance(v, _NUMERIC) and not isinstance(v, bool):
                nums.append(float(v))
        text = f"{len(idxs):,} cells · {non_null:,} non-null"
        if nums:
            s = sum(nums)
            f = lambda x: f"{x:,.0f}" if abs(x) >= 1e5 else f"{x:,.2f}".rstrip("0").rstrip(".")  # noqa: E731
            text += f" · Sum {f(s)} · Avg {f(s / len(nums))} · Min {f(min(nums))} · Max {f(max(nums))}"
        hook(text)

    def autosize_columns(self):
        model = self.model()
        if not model:
            return
        self.ensurePolished()
        self.horizontalHeader().ensurePolished()
        fm = self.fontMetrics()
        bold = QFont(self.horizontalHeader().font())
        bold.setBold(True)
        hfm = QFontMetrics(bold)
        sample = min(model.rowCount(), 150)
        for c in range(model.columnCount()):
            w = hfm.horizontalAdvance(str(model.headerData(c, Qt.Horizontal))) + 34
            for r in range(sample):
                text = model.data(model.index(r, c), Qt.DisplayRole) or ""
                w = max(w, fm.horizontalAdvance(text[:80]) + 30)
            self.setColumnWidth(c, min(w, 400))

    # ── selection helpers ──
    def selected_block(self):
        """Selected cells as (headers, rows of raw values)."""
        model = self.model()
        idxs = self.selectedIndexes()
        if not model or not idxs:
            return [], []
        rows = sorted({i.row() for i in idxs})
        cols = sorted({i.column() for i in idxs}, key=lambda c: self.horizontalHeader().visualIndex(c))
        chosen = {(i.row(), i.column()) for i in idxs}
        headers = [model.headers[c] for c in cols]
        out = [[model.rows[r][c] if (r, c) in chosen else None for c in cols] for r in rows]
        return headers, out

    def copy_selection(self, with_headers=False, fmt="tsv"):
        headers, rows = self.selected_block()
        if not headers:
            return
        if fmt == "tsv":
            lines = ["\t".join(headers)] if with_headers else []
            lines += ["\t".join("NULL" if v is None else to_text(v).replace("\t", " ") for v in r) for r in rows]
            text = "\n".join(lines)
        else:
            text = rows_to_text(fmt, headers, rows, self.table_name)
        QApplication.clipboard().setText(text)

    def keyPressEvent(self, e):
        if e.matches(QKeySequence.Copy):
            self.copy_selection()
            return
        if e.key() in (Qt.Key_Return, Qt.Key_Enter) and e.modifiers() & Qt.ShiftModifier:
            self.show_value()
            return
        super().keyPressEvent(e)

    def _chart(self, model):
        from .result_tools import ChartDialog
        idxs = self.selectedIndexes()
        if len({i.column() for i in idxs}) >= 2 and len({i.row() for i in idxs}) >= 2:
            headers, rows = self.selected_block()
        else:
            headers, rows = model.headers, model.rows
        ChartDialog(self, headers, rows, self.table_name).exec()

    def show_value(self, index=None):
        index = index or self.currentIndex()
        model = self.model()
        if not index.isValid() or not model:
            return
        editable = bool(model.flags(index) & Qt.ItemIsEditable)
        dlg = ValueDialog(self, model.headers[index.column()], model.data(index, Qt.EditRole) or "",
                          read_only=not editable)
        if dlg.exec() == QDialog.Accepted and editable:
            model.setData(index, dlg.text())

    def _context_menu(self, pos):
        model = self.model()
        if not model:
            return
        menu = QMenu(self)
        if self.menu_hook:
            self.menu_hook(menu)
            if not menu.isEmpty():
                menu.addSeparator()
        menu.addAction(icons.icon("copy"), "Copy\tCtrl+C", self.copy_selection)
        menu.addAction("Copy with column names", lambda: self.copy_selection(True))
        sub = menu.addMenu("Copy as")
        for label, fmt in (("CSV", "csv"), ("JSON", "json"), ("SQL INSERT", "sql"), ("Markdown", "md")):
            sub.addAction(label, lambda f=fmt: self.copy_selection(fmt=f))
        menu.addSeparator()
        menu.addAction(icons.icon("view"), "View / edit value…\tShift+Enter", self.show_value)
        menu.addAction(icons.icon("export"), "Export grid rows…",
                       lambda: export_rows_dialog(self, model.headers, model.rows, self.table_name))
        menu.addAction(icons.icon("chart"), "Chart…", lambda: self._chart(model))
        menu.exec(self.viewport().mapToGlobal(pos))

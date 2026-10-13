"""Result panels: quick filter, chart builder and export around a DataGrid."""
import decimal

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QHBoxLayout, QLineEdit, QListWidget,
                               QListWidgetItem, QSpinBox, QVBoxLayout, QWidget)

from . import icons
from .charts import CategoryChart
from .grid import DataGrid, ResultModel, export_rows_dialog
from .ui import bar, muted, tool

_NUM = (int, float, decimal.Decimal)


def numeric_columns(headers, rows):
    out = []
    for c in range(len(headers)):
        vals = [r[c] for r in rows[:300] if r[c] is not None]
        if vals and all(isinstance(v, _NUM) and not isinstance(v, bool) or _is_num_text(v) for v in vals):
            out.append(c)
    return out


def _is_num_text(v):
    if isinstance(v, str):
        try:
            float(v)
            return True
        except ValueError:
            return False
    return False


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


class ChartDialog(QDialog):
    def __init__(self, parent, headers, rows, title="Chart"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(1000, 620)
        self.headers, self.rows = list(headers), list(rows)
        nums = numeric_columns(self.headers, self.rows)
        lay = QHBoxLayout(self)
        side = QWidget()
        side.setFixedWidth(240)
        sv = QVBoxLayout(side)
        sv.setContentsMargins(0, 0, 0, 0)
        self.kind = QComboBox()
        for label, key in (("Bar", "bar"), ("Line", "line"), ("Pie", "pie")):
            self.kind.addItem(label, key)
        self.label = QComboBox()
        self.label.addItems(self.headers)
        non_num = [c for c in range(len(self.headers)) if c not in nums]
        self.label.setCurrentIndex(non_num[0] if non_num else 0)
        self.values = QListWidget()
        defaults = [c for c in nums if c != self.label.currentIndex()][:1]
        for c, h in enumerate(self.headers):
            it = QListWidgetItem(h)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if c in defaults else Qt.Unchecked)
            self.values.addItem(it)
        self.group = QCheckBox("Sum rows with the same label")
        self.group.setChecked(True)
        self.sort = QComboBox()
        self.sort.addItems(["Keep row order", "Largest first", "Label A→Z"])
        self.limit = QSpinBox()
        self.limit.setRange(2, 5000)
        self.limit.setValue(40)
        self.limit.setPrefix("Show up to ")
        self.limit.setSuffix(" labels")
        for w, cap in ((self.kind, "Chart type"), (self.label, "Label (X axis)"), (self.values, "Values")):
            sv.addWidget(muted(cap))
            sv.addWidget(w)
        sv.addWidget(self.group)
        sv.addWidget(self.sort)
        sv.addWidget(self.limit)
        b_png = tool("export", "Save the chart as PNG", self._save_png, "Save PNG…")
        sv.addWidget(b_png)
        lay.addWidget(side)
        self.chart = CategoryChart(title)
        lay.addWidget(self.chart, 1)
        for sig in (self.kind.currentIndexChanged, self.label.currentIndexChanged, self.sort.currentIndexChanged,
                    self.limit.valueChanged):
            sig.connect(lambda *_: self._render())
        self.group.toggled.connect(lambda *_: self._render())
        self.values.itemChanged.connect(lambda *_: self._render())
        self._render()

    def _render(self):
        lc = self.label.currentIndex()
        vcs = [i for i in range(self.values.count()) if self.values.item(i).checkState() == Qt.Checked and i != lc]
        if not vcs:
            self.chart.set_data([], [], self.kind.currentData())
            return
        labels, series = [], {c: [] for c in vcs}
        if self.group.isChecked():
            pos = {}
            for r in self.rows:
                key = "NULL" if r[lc] is None else str(r[lc])
                if key not in pos:
                    pos[key] = len(labels)
                    labels.append(key)
                    for c in vcs:
                        series[c].append(0.0)
                for c in vcs:
                    series[c][pos[key]] += _f(r[c])
        else:
            for r in self.rows:
                labels.append("NULL" if r[lc] is None else str(r[lc]))
                for c in vcs:
                    series[c].append(_f(r[c]))
        order = list(range(len(labels)))
        if self.sort.currentIndex() == 1:
            order.sort(key=lambda i: -series[vcs[0]][i])
        elif self.sort.currentIndex() == 2:
            order.sort(key=lambda i: labels[i].lower())
        order = order[:self.limit.value()]
        self.chart.set_data([labels[i] for i in order],
                            [(self.headers[c], [series[c][i] for i in order]) for c in vcs],
                            self.kind.currentData())

    def _save_png(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save chart", "chart.png", "PNG image (*.png)")
        if path:
            self.chart.grab().save(path)


class ResultPanel(QWidget):
    """A result grid with a quick filter, row count, chart and export buttons."""

    def __init__(self, headers, rows, table_name="result", title="Result"):
        super().__init__()
        self.headers, self.all_rows = list(headers), list(rows)
        self.title = title
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Quick filter rows…   (column:text to search one column)")
        self.filter.addAction(icons.icon("filter"), QLineEdit.LeadingPosition)
        self.filter.setClearButtonEnabled(True)
        self.filter.textChanged.connect(self._apply)
        self.count = muted("")
        lay.addWidget(bar(self.filter, self.count, "|",
                          tool("chart", "Build a chart from these rows", self.chart, "Chart"),
                          tool("export", "Export rows (CSV, Excel, JSON, SQL …)", self.export, "Export"),
                          margins=(6, 3, 6, 3)))
        self.filter.setMinimumWidth(280)
        self.grid = DataGrid()
        self.grid.table_name = table_name
        lay.addWidget(self.grid, 1)
        self.grid.set_model(ResultModel(self.headers, self.all_rows))
        self.grid.horizontalHeader().setSortIndicator(-1, Qt.AscendingOrder)
        self.grid.setSortingEnabled(True)
        self._update_count(len(self.all_rows))

    def visible_rows(self):
        return self.grid.model().rows

    def _apply(self, text):
        text = text.strip()
        if not text:
            rows = self.all_rows
        else:
            col = None
            if ":" in text:
                name, _, rest = text.partition(":")
                lower = [h.lower() for h in self.headers]
                if name.strip().lower() in lower:
                    col, text = lower.index(name.strip().lower()), rest.strip()
            needle = text.lower()
            if col is not None:
                rows = [r for r in self.all_rows if needle in ("null" if r[col] is None else str(r[col])).lower()]
            else:
                rows = [r for r in self.all_rows
                        if any(needle in ("null" if v is None else str(v)).lower() for v in r)]
        widths = [self.grid.columnWidth(c) for c in range(len(self.headers))]
        self.grid.setModel(ResultModel(self.headers, rows))
        for c, w in enumerate(widths):
            self.grid.setColumnWidth(c, w)
        self._update_count(len(rows))

    def _update_count(self, n):
        total = len(self.all_rows)
        self.count.setText(f"{n:,} of {total:,} rows" if n != total else f"{total:,} rows")

    def chart(self):
        ChartDialog(self, self.headers, self.visible_rows(), self.title).exec()

    def export(self):
        export_rows_dialog(self, self.headers, self.visible_rows(), self.grid.table_name)

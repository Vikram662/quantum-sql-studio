"""Lightweight QPainter charts: live time series (monitor) and bar/line/pie (results)."""
import math
import time

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QToolTip, QWidget

from .theme import T

PALETTE = ["#3574f0", "#e0a84a", "#5fb865", "#e5534b", "#a77de8", "#2aacb8", "#d97bb0", "#8c8f96",
           "#c2b33f", "#6f8fd8"]


def color(i):
    return QColor(PALETTE[i % len(PALETTE)])


def nice_max(v):
    """Round an axis maximum up to 1, 2, 2.5 or 5 × 10^n."""
    if v <= 0:
        return 1
    exp = 10 ** math.floor(math.log10(v))
    for m in (1, 2, 2.5, 5, 10):
        if v <= m * exp:
            return m * exp
    return 10 * exp


def short_number(v):
    v = float(v)
    for unit, div in (("G", 1e9), ("M", 1e6), ("k", 1e3)):
        if abs(v) >= div:
            return f"{v / div:.1f}{unit}".replace(".0" + unit, unit)
    if v == int(v):
        return str(int(v))
    return f"{v:.2f}" if abs(v) < 10 else f"{v:.1f}"


class _Base(QWidget):
    def __init__(self, title="", parent=None):
        super().__init__(parent)
        self.title = title
        self.setMouseTracking(True)
        self.setMinimumHeight(150)
        self._font = QFont(self.font())
        self._font.setPointSizeF(8)
        self._title_font = QFont(self.font())
        self._title_font.setPointSizeF(9)
        self._title_font.setBold(True)

    def _frame(self, p):
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(QColor(T["border"]), 1))
        p.setBrush(QColor(T["panel"]))
        p.drawRoundedRect(r, 7, 7)
        if self.title:
            p.setFont(self._title_font)
            p.setPen(QColor(T["text"]))
            p.drawText(QRectF(12, 6, self.width() - 24, 20), Qt.AlignLeft | Qt.AlignVCenter, self.title)

    def _legend(self, p, items, right):
        """Draw legend chips right-aligned on the title row; returns nothing."""
        p.setFont(self._font)
        fm = p.fontMetrics()
        x = right
        for name, col in reversed(items):
            w = fm.horizontalAdvance(name) + 18
            x -= w
            p.setBrush(col)
            p.setPen(Qt.NoPen)
            p.drawRoundedRect(QRectF(x, 12, 8, 8), 2, 2)
            p.setPen(QColor(T["muted"]))
            p.drawText(QRectF(x + 11, 6, w - 11, 20), Qt.AlignLeft | Qt.AlignVCenter, name)


class TimeSeriesChart(_Base):
    """Rolling time-series chart. Call set_data(times, series) where series is a
    list of (name, values) aligned with `times` (epoch seconds)."""

    def __init__(self, title="", unit="", stacked=False, parent=None):
        super().__init__(title, parent)
        self.unit = unit
        self.stacked = stacked
        self.times = []
        self.series = []
        self.colors = {}
        self.ref_line = None  # (value, label)
        self._hover_x = None

    def set_data(self, times, series, ref_line=None):
        self.times = list(times)
        self.series = [(n, list(v)) for n, v in series]
        self.ref_line = ref_line
        for n, _ in self.series:
            if n not in self.colors:
                self.colors[n] = color(len(self.colors))
        self.update()

    def _plot_rect(self):
        return QRectF(46, 30, max(10, self.width() - 58), max(10, self.height() - 52))

    def paintEvent(self, _e):
        p = QPainter(self)
        self._frame(p)
        plot = self._plot_rect()
        n = len(self.times)
        totals = [0.0] * n
        if self.stacked:
            for _, vals in self.series:
                for i, v in enumerate(vals[:n]):
                    totals[i] += v or 0
            peak = max(totals, default=0)
        else:
            peak = max((v or 0 for _, vals in self.series for v in vals), default=0)
        if self.ref_line:
            peak = max(peak, self.ref_line[0])
        top = nice_max(peak * 1.1)
        self._legend(p, [(nm, self.colors[nm]) for nm, _ in self.series[:6]], self.width() - 12)

        # grid + y labels
        p.setFont(self._font)
        for k in range(5):
            y = plot.bottom() - plot.height() * k / 4
            p.setPen(QPen(QColor(T["border"]), 1, Qt.DotLine if k else Qt.SolidLine))
            p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            p.setPen(QColor(T["faint"]))
            p.drawText(QRectF(2, y - 8, plot.left() - 6, 16), Qt.AlignRight | Qt.AlignVCenter,
                       short_number(top * k / 4))
        if n < 2:
            p.setPen(QColor(T["faint"]))
            p.drawText(plot, Qt.AlignCenter, "Collecting samples…")
            return
        t0, t1 = self.times[0], self.times[-1]
        span = max(1e-6, t1 - t0)
        xs = [plot.left() + plot.width() * (t - t0) / span for t in self.times]

        def ypos(v):
            return plot.bottom() - plot.height() * min(1.0, (v or 0) / top)

        # x labels
        p.setPen(QColor(T["faint"]))
        for k in range(4):
            t = t0 + span * k / 3
            x = plot.left() + plot.width() * k / 3
            align = Qt.AlignLeft if k == 0 else Qt.AlignRight if k == 3 else Qt.AlignHCenter
            rect = QRectF(x - (0 if k == 0 else 60 if k == 3 else 30), plot.bottom() + 3, 60, 14)
            p.drawText(rect, align | Qt.AlignVCenter, time.strftime("%H:%M:%S", time.localtime(t)))

        base = [0.0] * n
        for name, vals in self.series:
            col = self.colors[name]
            vals = (vals + [0] * n)[:n]
            if self.stacked:
                upper = [base[i] + (vals[i] or 0) for i in range(n)]
                path = QPainterPath(QPointF(xs[0], ypos(upper[0])))
                for i in range(1, n):
                    path.lineTo(xs[i], ypos(upper[i]))
                for i in reversed(range(n)):
                    path.lineTo(xs[i], ypos(base[i]))
                path.closeSubpath()
                fill = QColor(col)
                fill.setAlpha(170)
                p.setPen(Qt.NoPen)
                p.setBrush(fill)
                p.drawPath(path)
                base = upper
            else:
                path = QPainterPath(QPointF(xs[0], ypos(vals[0])))
                for i in range(1, n):
                    path.lineTo(xs[i], ypos(vals[i]))
                p.setBrush(Qt.NoBrush)
                p.setPen(QPen(col, 1.8))
                p.drawPath(path)
        if self.ref_line:
            y = ypos(self.ref_line[0])
            p.setPen(QPen(QColor(T["danger"]), 1, Qt.DashLine))
            p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            p.setPen(QColor(T["danger"]))
            p.drawText(QRectF(plot.left() + 4, y - 15, 220, 14), Qt.AlignLeft, self.ref_line[1])
        if self._hover_x is not None and plot.left() <= self._hover_x <= plot.right():
            p.setPen(QPen(QColor(T["muted"]), 1, Qt.DashLine))
            p.drawLine(QPointF(self._hover_x, plot.top()), QPointF(self._hover_x, plot.bottom()))

    def mouseMoveEvent(self, e):
        n = len(self.times)
        plot = self._plot_rect()
        if n < 2 or not plot.contains(e.position()):
            self._hover_x = None
            QToolTip.hideText()
            self.update()
            return
        t0, t1 = self.times[0], self.times[-1]
        t = t0 + (e.position().x() - plot.left()) / plot.width() * (t1 - t0)
        i = min(range(n), key=lambda k: abs(self.times[k] - t))
        self._hover_x = plot.left() + plot.width() * (self.times[i] - t0) / max(1e-6, t1 - t0)
        lines = [time.strftime("%H:%M:%S", time.localtime(self.times[i]))]
        total = 0
        for name, vals in self.series:
            v = vals[i] if i < len(vals) else 0
            total += v or 0
            lines.append(f"<span style='color:{self.colors[name].name()}'>■</span> {name}: <b>{short_number(v)}</b>"
                         f"{self.unit}")
        if self.stacked and len(self.series) > 1:
            lines.append(f"Total: <b>{short_number(total)}</b>{self.unit}")
        QToolTip.showText(e.globalPosition().toPoint(), "<br>".join(lines), self)
        self.update()

    def leaveEvent(self, _e):
        self._hover_x = None
        self.update()


class CategoryChart(_Base):
    """Bar, line or pie chart over categories. series = [(name, [values])]."""

    def __init__(self, title="", parent=None):
        super().__init__(title, parent)
        self.mode = "bar"
        self.labels = []
        self.series = []
        self._hits = []

    def set_data(self, labels, series, mode=None):
        self.labels = [str(x) for x in labels]
        self.series = [(n, [float(v or 0) for v in vals]) for n, vals in series]
        if mode:
            self.mode = mode
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        self._frame(p)
        self._hits = []
        if not self.labels or not self.series:
            p.setPen(QColor(T["faint"]))
            p.drawText(self.rect(), Qt.AlignCenter, "Pick a label column and at least one numeric column")
            return
        if self.mode == "pie":
            self._pie(p)
            return
        self._legend(p, [(n, color(i)) for i, (n, _) in enumerate(self.series[:6])], self.width() - 12)
        p.setFont(self._font)
        fm = p.fontMetrics()
        plot = QRectF(52, 32, max(10, self.width() - 66), max(10, self.height() - 74))
        lo = min(0.0, min(min(v) for _, v in self.series))
        hi = nice_max(max(max(v) for _, v in self.series) * 1.05)
        span = max(1e-9, hi - lo)
        p.setFont(self._font)
        for k in range(5):
            val = lo + span * k / 4
            y = plot.bottom() - plot.height() * k / 4
            p.setPen(QPen(QColor(T["border"]), 1, Qt.DotLine if k else Qt.SolidLine))
            p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
            p.setPen(QColor(T["faint"]))
            p.drawText(QRectF(0, y - 8, plot.left() - 6, 16), Qt.AlignRight | Qt.AlignVCenter, short_number(val))
        n = len(self.labels)
        slot = plot.width() / n

        def ypos(v):
            return plot.bottom() - plot.height() * (v - lo) / span

        step = max(1, int(math.ceil(n * 70 / plot.width())))
        p.setPen(QColor(T["muted"]))
        for i, lab in enumerate(self.labels):
            if i % step:
                continue
            x = plot.left() + slot * i
            p.drawText(QRectF(x - 20, plot.bottom() + 4, slot + 40, 16), Qt.AlignHCenter | Qt.AlignTop,
                       fm.elidedText(lab, Qt.ElideRight, int(slot * step) + 30))
        if self.mode == "bar":
            m = len(self.series)
            bw = max(1.0, slot * 0.78 / m)
            for si, (name, vals) in enumerate(self.series):
                p.setBrush(color(si))
                p.setPen(Qt.NoPen)
                for i, v in enumerate(vals):
                    x = plot.left() + slot * i + slot * 0.11 + bw * si
                    y0, y1 = ypos(max(lo, 0)), ypos(v)
                    r = QRectF(x, min(y0, y1), bw - (1 if bw > 3 else 0), abs(y0 - y1))
                    p.drawRect(r)
                    self._hits.append((r, f"{self.labels[i]}<br>{name}: <b>{short_number(v)}</b>"))
        else:
            for si, (name, vals) in enumerate(self.series):
                pts = [QPointF(plot.left() + slot * (i + 0.5), ypos(v)) for i, v in enumerate(vals)]
                path = QPainterPath(pts[0])
                for pt in pts[1:]:
                    path.lineTo(pt)
                p.setPen(QPen(color(si), 2))
                p.setBrush(Qt.NoBrush)
                p.drawPath(path)
                p.setBrush(color(si))
                for i, pt in enumerate(pts):
                    if n <= 60:
                        p.drawEllipse(pt, 2.6, 2.6)
                    self._hits.append((QRectF(pt.x() - slot / 2, plot.top(), slot, plot.height()),
                                       f"{self.labels[i]}<br>{name}: <b>{short_number(vals[i])}</b>"))

    def _pie(self, p):
        vals = [max(0.0, v) for v in self.series[0][1]]
        total = sum(vals) or 1
        size = min(self.width() * 0.55, self.height() - 50)
        rect = QRectF(20, 36, size, size)
        ang = 90 * 16
        order = sorted(range(len(vals)), key=lambda i: -vals[i])
        shown = order[:9]
        other = sum(vals[i] for i in order[9:])
        parts = [(self.labels[i], vals[i]) for i in shown] + ([("Other", other)] if other else [])
        p.setFont(self._font)
        fm = p.fontMetrics()
        for k, (lab, v) in enumerate(parts):
            span = -int(round(v / total * 360 * 16))
            p.setBrush(color(k))
            p.setPen(QPen(QColor(T["panel"]), 1.5))
            p.drawPie(rect, ang, span)
            ang += span
            y = 40 + k * 18
            x = rect.right() + 22
            p.setPen(Qt.NoPen)
            p.drawRoundedRect(QRectF(x, y + 3, 9, 9), 2, 2)
            p.setPen(QColor(T["text"]))
            text = f"{lab}  —  {short_number(v)} ({v / total * 100:.1f}%)"
            p.drawText(QRectF(x + 14, y, self.width() - x - 20, 16), Qt.AlignLeft | Qt.AlignVCenter,
                       fm.elidedText(text, Qt.ElideRight, int(self.width() - x - 24)))

    def mouseMoveEvent(self, e):
        for r, tip in self._hits:
            if r.contains(e.position()):
                QToolTip.showText(e.globalPosition().toPoint(), tip, self)
                return
        QToolTip.hideText()

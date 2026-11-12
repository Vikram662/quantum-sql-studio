"""Small widget helpers shared by the tabs."""
from PySide6.QtCore import QEvent, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QMessageBox, QProgressBar, QToolButton, QWidget

from . import icons
from .theme import T


class ProgressBar(QProgressBar):
    """Progress bar with a theme-coloured gradient fill (stylesheet), a soft highlight that
    sweeps across the fill while work is running, like the Windows progress bar, and a
    percentage that is white over the fill and normal text colour over the empty part."""

    SHINE_PX_PER_S = 260
    SHINE_WIDTH = 110

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setRange(0, 100)
        self.setTextVisible(False)  # the style draws bar and chunk; the text is painted below
        self._shine = 0.0
        self._busy = True
        self._anim = QTimer(self)
        self._anim.setInterval(33)
        self._anim.timeout.connect(self._step)

    def set_busy(self, busy):
        """False stops the shine on a bar left part-filled (failed or cancelled work)."""
        self._busy = busy
        self.update()

    def _running(self):
        return self._busy and self.minimum() < self.value() < self.maximum()

    def _step(self):
        if self._running():
            self._shine += self.SHINE_PX_PER_S * self._anim.interval() / 1000
            self.update()

    def showEvent(self, event):
        super().showEvent(event)
        self._anim.start()

    def hideEvent(self, event):
        self._anim.stop()
        super().hideEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        span = self.maximum() - self.minimum()
        if span <= 0:
            return
        inner = self.rect().adjusted(1, 1, -1, -1)  # inside the 1px border from the stylesheet
        fill = int(inner.width() * (self.value() - self.minimum()) / span)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self._running() and fill > 6:
            # Band travels from just before the fill to just past its end, then restarts.
            w = self.SHINE_WIDTH
            x = inner.left() - w + self._shine % (fill + w * 2)
            grad = QLinearGradient(x, 0, x + w, 0)
            glow = QColor(255, 255, 255, 0)
            grad.setColorAt(0, glow)
            glow.setAlpha(80)
            grad.setColorAt(0.5, glow)
            glow.setAlpha(0)
            grad.setColorAt(1, glow)
            clip = QPainterPath()
            clip.addRoundedRect(QRectF(inner.left(), inner.top(), fill, inner.height()), 3, 3)
            p.setClipPath(clip)
            p.fillRect(QRectF(x, inner.top(), w, inner.height()), grad)
            p.setClipping(False)
        text = self.text()
        if not text:
            p.end()
            return
        p.setFont(self.font())
        for color, clip in ((T["accent_text"], inner.adjusted(0, 0, fill - inner.width(), 0)),
                            (T["text"], inner.adjusted(fill, 0, 0, 0))):
            p.save()
            p.setClipRect(clip)
            p.setPen(QColor(color))
            p.drawText(self.rect(), Qt.AlignCenter, text)
            p.restore()
        p.end()


def _draw_spinner(p, rect, angle, width=3.0):
    track = QColor(T["muted"])
    track.setAlpha(60)
    p.setPen(QPen(track, width))
    p.drawEllipse(rect)
    p.setPen(QPen(QColor(T["accent"]), width, Qt.SolidLine, Qt.RoundCap))
    p.drawArc(rect, int(-angle * 16), 100 * 16)


class Spinner(QWidget):
    """Small rotating arc for status bars and toolbars; hidden while idle."""

    def __init__(self, size=16, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._angle = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._step)
        self.hide()

    def _step(self):
        self._angle = (self._angle + 30) % 360
        self.update()

    def start(self):
        if not self._timer.isActive():
            self._timer.start(80)
        self.show()

    def stop(self):
        self._timer.stop()
        self.hide()

    def paintEvent(self, _e):
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.Antialiasing)
            _draw_spinner(p, QRectF(self.rect()).adjusted(2, 2, -2, -2), self._angle, 2.0)
        finally:
            p.end()


class LoadingOverlay(QWidget):
    """Dims a widget and shows a spinner + message while its data loads.

    Appears only after a short delay, so fast loads don't flicker. It also blocks clicks on stale content."""

    DELAY_MS = 250

    def __init__(self, target, text="Loading…"):
        super().__init__(target)
        self.text = text
        self._angle = 0
        self._spin = QTimer(self)
        self._spin.timeout.connect(self._step)
        self._delay = QTimer(self)
        self._delay.setSingleShot(True)
        self._delay.timeout.connect(self._appear)
        self._font = QFont(self.font())
        self._font.setPointSizeF(9)
        target.installEventFilter(self)
        self.hide()

    def start(self, text=None):
        if text:
            self.text = text
        if self.isVisible():
            self.update()
        elif not self._delay.isActive():
            self._delay.start(self.DELAY_MS)

    def stop(self):
        self._delay.stop()
        self._spin.stop()
        self.hide()

    def _appear(self):
        self.setGeometry(self.parentWidget().rect())
        self.raise_()
        self.show()
        self._spin.start(80)

    def _step(self):
        self._angle = (self._angle + 30) % 360
        self.update()

    def eventFilter(self, obj, event):
        if obj is self.parentWidget() and event.type() == QEvent.Resize:
            self.setGeometry(obj.rect())
        return False

    def paintEvent(self, _e):
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.Antialiasing)
            veil = QColor(T["panel"])
            veil.setAlpha(110)  # light veil: the old content stays readable underneath
            p.fillRect(self.rect(), veil)
            cx, cy = self.width() / 2, self.height() / 2 - 10
            _draw_spinner(p, QRectF(cx - 10, cy - 10, 20, 20), self._angle, 2.0)
            p.setFont(self._font)
            p.setPen(QColor(T["muted"]))
            p.drawText(QRectF(8, cy + 16, self.width() - 16, 20), Qt.AlignHCenter | Qt.AlignTop, self.text)
        finally:
            p.end()


def tool(icon_name, tip, slot=None, text=None, checkable=False):
    b = QToolButton()
    b.setIcon(icons.icon(icon_name))
    b.setToolTip(tip)
    if text:
        b.setText(text)
        b.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
    b.setCheckable(checkable)
    if slot:
        b.clicked.connect(lambda *_: slot())
    return b


def bar(*items, object_name="bar", margins=(8, 5, 8, 5)):
    """Horizontal toolbar frame. Pass widgets, None for a stretch, or '|' for a divider."""
    frame = QFrame()
    frame.setObjectName(object_name)
    lay = QHBoxLayout(frame)
    lay.setContentsMargins(*margins)
    lay.setSpacing(4)
    for it in items:
        if it is None:
            lay.addStretch(1)
        elif it == "|":
            line = QFrame()
            line.setFrameShape(QFrame.VLine)
            line.setFixedWidth(9)
            line.setStyleSheet("QFrame { color: palette(mid); }")
            lay.addWidget(line)
        elif isinstance(it, QWidget):
            lay.addWidget(it)
    return frame


def muted(text=""):
    lbl = QLabel(text)
    lbl.setObjectName("muted")
    return lbl


def confirm(parent, title, text, danger=False):
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(text)
    box.setIcon(QMessageBox.Warning if danger else QMessageBox.Question)
    box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
    box.setDefaultButton(QMessageBox.No if danger else QMessageBox.Yes)
    return box.exec() == QMessageBox.Yes

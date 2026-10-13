"""Small widget helpers shared by the tabs."""
from PySide6.QtCore import QEvent, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QMessageBox, QToolButton, QWidget

from . import icons
from .theme import T


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

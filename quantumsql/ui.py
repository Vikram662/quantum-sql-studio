"""Small widget helpers shared by the tabs."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QMessageBox, QToolButton, QWidget

from . import icons


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

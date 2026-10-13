"""Command palette (Ctrl+Shift+P): fuzzy-search every action, database, table and saved query."""
from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QDialog, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout

from . import icons
from .theme import T


def fuzzy_score(query, text):
    """Subsequence match score (higher is better), or None when query is not a subsequence of text."""
    if not query:
        return 0
    q, t = query.lower(), text.lower()
    if q in t:
        pos = t.index(q)
        return 1000 - pos * 2 - len(t) * 0.1 + (200 if pos == 0 or not t[pos - 1].isalnum() else 0)
    score, ti, last = 0.0, 0, -2
    for ch in q:
        ti = t.find(ch, ti)
        if ti < 0:
            return None
        if ti == last + 1:
            score += 8
        if ti == 0 or not t[ti - 1].isalnum():
            score += 10
        score -= ti * 0.05
        last = ti
        ti += 1
    return score - len(t) * 0.1


class CommandPalette(QDialog):
    """entries: list of (icon_name, title, detail, callback)."""

    def __init__(self, parent, entries, placeholder="Type a command, table or database…"):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)
        self.entries = entries
        self.setObjectName("palette")
        self.setStyleSheet(f"QDialog#palette {{ background: {T['raised']}; border: 1px solid {T['border_strong']}; "
                           f"border-radius: 8px; }} QListWidget {{ background: transparent; }} "
                           f"QListWidget::item {{ padding: 6px 8px; border-radius: 5px; }} "
                           f"QListWidget::item:selected {{ background: {T['accent']}; color: {T['accent_text']}; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText(placeholder)
        self.edit.addAction(icons.icon("command"), QLineEdit.LeadingPosition)
        self.edit.textChanged.connect(self._filter)
        self.edit.installEventFilter(self)
        self.list = QListWidget()
        self.list.setIconSize(QSize(16, 16))
        self.list.itemActivated.connect(self._run)
        self.list.itemClicked.connect(self._run)
        lay.addWidget(self.edit)
        lay.addWidget(self.list, 1)
        if parent is not None:
            w = min(720, int(parent.width() * 0.6))
            self.resize(w, 420)
            top = parent.mapToGlobal(parent.rect().topLeft())
            self.move(top.x() + (parent.width() - w) // 2, top.y() + 70)
        self._filter("")
        self.edit.setFocus()

    def add_entries(self, entries):
        self.entries.extend(entries)
        self._filter(self.edit.text())

    def _filter(self, text):
        text = text.strip()
        scored = []
        for e in self.entries:
            s = fuzzy_score(text, e[1]) if text else 0
            if s is None and text:
                s2 = fuzzy_score(text, f"{e[1]} {e[2]}")
                s = None if s2 is None else s2 - 50
            if s is not None:
                scored.append((s, e))
        if text:
            scored.sort(key=lambda x: -x[0])
        self.list.clear()
        for _, (ic, title, detail, cb) in scored[:200]:
            it = QListWidgetItem(icons.icon(ic), f"{title}" + (f"    —  {detail}" if detail else ""))
            it.setData(Qt.UserRole, cb)
            self.list.addItem(it)
        if self.list.count():
            self.list.setCurrentRow(0)

    def eventFilter(self, obj, e):
        if obj is self.edit and e.type() == e.Type.KeyPress:
            if e.key() in (Qt.Key_Down, Qt.Key_Up, Qt.Key_PageDown, Qt.Key_PageUp):
                self.list.keyPressEvent(e)
                return True
            if e.key() in (Qt.Key_Return, Qt.Key_Enter):
                it = self.list.currentItem()
                if it:
                    self._run(it)
                return True
            if e.key() == Qt.Key_Escape:
                self.reject()
                return True
        return super().eventFilter(obj, e)

    def _run(self, item):
        cb = item.data(Qt.UserRole)
        self.accept()
        if cb:
            cb()

"""SQL code editor: line numbers, syntax highlighting, autocomplete, comment toggle."""
import re

from PySide6.QtCore import QEvent, QRect, QRegularExpression, QSize, QStringListModel, Qt, Signal
from PySide6.QtGui import (QColor, QFont, QPainter, QSyntaxHighlighter, QTextCharFormat,
                           QTextCursor, QTextFormat)
from PySide6.QtWidgets import QCompleter, QPlainTextEdit, QTextEdit, QWidget

from . import theme
from .theme import T

KEYWORDS = """
ACCESSIBLE ADD ALL ALTER ANALYZE AND AS ASC AUTO_INCREMENT BEFORE BEGIN BETWEEN BIGINT BINARY BLOB BOOLEAN BOTH BY
CALL CASCADE CASE CHANGE CHAR CHARACTER CHARSET CHECK COLLATE COLUMN COLUMNS COMMENT COMMIT CONSTRAINT CREATE CROSS
CURRENT_DATE CURRENT_TIME CURRENT_TIMESTAMP CURSOR DATABASE DATABASES DATE DATETIME DAY DECIMAL DECLARE DEFAULT
DEFINER DELAYED DELETE DELIMITER DESC DESCRIBE DETERMINISTIC DISTINCT DIV DO DOUBLE DROP DUPLICATE EACH ELSE ELSEIF
ENGINE ENUM ESCAPE EVENT EXISTS EXIT EXPLAIN FALSE FETCH FIELDS FLOAT FLUSH FOR FORCE FOREIGN FROM FULL FULLTEXT
FUNCTION GRANT GROUP HANDLER HAVING HIGH_PRIORITY IF IGNORE IN INDEX INFILE INNER INOUT INSERT INT INTEGER INTERVAL
INTO INVOKER IS ITERATE JOIN JSON KEY KEYS KILL LEADING LEAVE LEFT LIKE LIMIT LINES LOAD LOCK LONGBLOB LONGTEXT LOOP
MEDIUMBLOB MEDIUMINT MEDIUMTEXT MODIFY NATURAL NOT NULL NUMERIC OFFSET ON OPTIMIZE OPTION OR ORDER OUT OUTER
PARTITION PRIMARY PRIVILEGES PROCEDURE PROCESSLIST READ REAL REFERENCES REGEXP RENAME REPAIR REPEAT REPLACE RESTRICT
RETURN RETURNS REVOKE RIGHT RLIKE ROLLBACK ROUTINE ROW ROWS SCHEMA SELECT SEPARATOR SET SHOW SIGNAL SMALLINT SQL
START STATUS STRAIGHT_JOIN TABLE TABLES TEMPORARY TERMINATED TEXT THEN TIME TIMESTAMP TINYBLOB TINYINT TINYTEXT TO
TRAILING TRANSACTION TRIGGER TRUE TRUNCATE UNION UNIQUE UNLOCK UNSIGNED UPDATE USAGE USE USER USING VALUES VARBINARY
VARCHAR VARIABLES VIEW WHEN WHERE WHILE WITH WRITE XOR YEAR ZEROFILL
""".split()

FUNCTIONS = """
ABS AVG CAST CEIL CEILING COALESCE CONCAT CONCAT_WS CONVERT COUNT CURDATE CURTIME DATE_ADD DATE_FORMAT DATE_SUB
DATEDIFF DAYNAME DAYOFWEEK FIELD FIND_IN_SET FLOOR FORMAT FROM_UNIXTIME GREATEST GROUP_CONCAT HEX IFNULL INSTR
JSON_ARRAY JSON_CONTAINS JSON_EXTRACT JSON_OBJECT JSON_UNQUOTE LAST_INSERT_ID LCASE LEAST LEFT LENGTH LOCATE LOWER
LPAD LTRIM MAX MD5 MID MIN MOD MONTH MONTHNAME NOW NULLIF PI POW POWER RAND REPLACE REVERSE ROUND ROW_NUMBER RPAD
RTRIM SHA1 SHA2 SIGN SQRT STR_TO_DATE SUBSTR SUBSTRING SUBSTRING_INDEX SUM SYSDATE TIMESTAMPDIFF TRIM UCASE UNHEX
UNIX_TIMESTAMP UPPER UUID VERSION WEEK YEARWEEK DENSE_RANK RANK LAG LEAD
""".split()


def _fmt(color, bold=False, italic=False):
    f = QTextCharFormat()
    f.setForeground(QColor(color))
    if bold:
        f.setFontWeight(QFont.DemiBold)
    if italic:
        f.setFontItalic(True)
    return f


class SqlHighlighter(QSyntaxHighlighter):
    def __init__(self, doc):
        super().__init__(doc)
        kw = "|".join(KEYWORDS)
        fn = "|".join(FUNCTIONS)
        ci = QRegularExpression.CaseInsensitiveOption
        self.rules = [
            (QRegularExpression(rf"\b(?:{kw})\b", ci), _fmt(T["syn_keyword"], bold=True)),
            (QRegularExpression(rf"\b(?:{fn})(?=\s*\()", ci), _fmt(T["syn_function"])),
            (QRegularExpression(r"\b\d+(?:\.\d+)?\b|\b0x[0-9a-fA-F]+\b"), _fmt(T["syn_number"])),
            (QRegularExpression(r"@@?[\w.$]+"), _fmt(T["syn_variable"])),
            (QRegularExpression(r"`[^`]*`"), _fmt(T["syn_ident"])),
            (QRegularExpression(r"'(?:[^'\\]|\\.|'')*'?"), _fmt(T["syn_string"])),
            (QRegularExpression(r"\"(?:[^\"\\]|\\.|\"\")*\"?"), _fmt(T["syn_string"])),
            (QRegularExpression(r"(?:--\s|#).*$|--$"), _fmt(T["syn_comment"], italic=True)),
        ]
        self.comment_fmt = _fmt(T["syn_comment"], italic=True)
        self.c_start = QRegularExpression(r"/\*")
        self.c_end = QRegularExpression(r"\*/")

    def highlightBlock(self, text):
        for rx, fmt in self.rules:
            it = rx.globalMatch(text)
            while it.hasNext():
                m = it.next()
                self.setFormat(m.capturedStart(), m.capturedLength(), fmt)
        # multi-line /* */ comments
        self.setCurrentBlockState(0)
        if self.previousBlockState() == 1:
            start, search_from = 0, 0
        else:
            start = self._index(self.c_start, text, 0)
            search_from = start + 2
        while start >= 0:
            end_m = self.c_end.match(text, search_from)
            if end_m.hasMatch():
                length = end_m.capturedEnd() - start
                self.setFormat(start, length, self.comment_fmt)
                start = self._index(self.c_start, text, start + length)
                search_from = start + 2
            else:
                self.setCurrentBlockState(1)
                self.setFormat(start, len(text) - start, self.comment_fmt)
                break

    @staticmethod
    def _index(rx, text, offset):
        m = rx.match(text, offset)
        return m.capturedStart() if m.hasMatch() else -1


class _Gutter(QWidget):
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor

    def sizeHint(self):
        return QSize(self.editor.gutter_width(), 0)

    def paintEvent(self, event):
        self.editor.paint_gutter(event)


class CodeEditor(QPlainTextEdit):
    run_current = Signal()

    def __init__(self, parent=None, font_size=11):
        super().__init__(parent)
        self.setFont(theme.code_font(font_size))
        self.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.setTabStopDistance(self.fontMetrics().horizontalAdvance(" ") * 4)
        self.setStyleSheet(f"QPlainTextEdit {{ background: {T['editor_bg']}; border: none; border-radius: 0; "
                           f"padding: 0; selection-background-color: {T['selection']}; }}")
        self.gutter = _Gutter(self)
        self.blockCountChanged.connect(self._update_gutter_width)
        self.updateRequest.connect(self._update_gutter)
        self.cursorPositionChanged.connect(self._highlight_line)
        self._update_gutter_width()
        self._highlight_line()
        self.highlighter = SqlHighlighter(self.document())

        self.words_model = QStringListModel(self)
        self.completer = QCompleter(self.words_model, self)
        self.completer.setWidget(self)
        self.completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.completer.setModelSorting(QCompleter.UnsortedModel)
        self.completer.setFilterMode(Qt.MatchStartsWith)
        self.completer.setCompletionMode(QCompleter.PopupCompletion)
        self.completer.setMaxVisibleItems(12)
        self.completer.activated.connect(self._insert_completion)
        self.set_schema_words([])
        self.tab_hook = None  # fn(editor) -> bool; lets the owner expand snippets on Tab

    # ── gutter ──
    def gutter_width(self):
        digits = max(3, len(str(self.blockCount())))
        return 18 + self.fontMetrics().horizontalAdvance("9") * digits

    def _update_gutter_width(self, *_):
        self.setViewportMargins(self.gutter_width(), 0, 0, 0)

    def _update_gutter(self, rect, dy):
        if dy:
            self.gutter.scroll(0, dy)
        else:
            self.gutter.update(0, rect.y(), self.gutter.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_gutter_width()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        cr = self.contentsRect()
        self.gutter.setGeometry(QRect(cr.left(), cr.top(), self.gutter_width(), cr.height()))

    def paint_gutter(self, event):
        p = QPainter(self.gutter)
        p.fillRect(event.rect(), QColor(T["gutter"]))
        block = self.firstVisibleBlock()
        num = block.blockNumber()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + round(self.blockBoundingRect(block).height())
        current = self.textCursor().blockNumber()
        h = self.fontMetrics().height()
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                p.setPen(QColor(T["text"] if num == current else T["gutter_text"]))
                p.drawText(0, top, self.gutter.width() - 10, h, Qt.AlignRight, str(num + 1))
            block = block.next()
            top = bottom
            bottom = top + round(self.blockBoundingRect(block).height())
            num += 1

    def _highlight_line(self):
        sel = QTextEdit.ExtraSelection()
        sel.format.setBackground(QColor(T["editor_line"]))
        sel.format.setProperty(QTextFormat.FullWidthSelection, True)
        sel.cursor = self.textCursor()
        sel.cursor.clearSelection()
        self.setExtraSelections([sel])

    # ── autocomplete ──
    def set_schema_words(self, words):
        base = [k for k in KEYWORDS] + [f for f in FUNCTIONS]
        seen, out = set(), []
        for w in list(words) + base:
            if w.lower() not in seen:
                seen.add(w.lower())
                out.append(w)
        self.words_model.setStringList(out)

    def _word_prefix(self):
        tc = self.textCursor()
        block_text = tc.block().text()[:tc.positionInBlock()]
        m = re.search(r"[\w$]+$", block_text)
        return m.group(0) if m else ""

    def _insert_completion(self, word):
        prefix = self._word_prefix()
        tc = self.textCursor()
        tc.movePosition(QTextCursor.Left, QTextCursor.KeepAnchor, len(prefix))
        tc.insertText(word)
        self.setTextCursor(tc)

    def _show_completer(self, force=False):
        prefix = self._word_prefix()
        if not force and len(prefix) < 2:
            self.completer.popup().hide()
            return
        self.completer.setCompletionPrefix(prefix)
        if self.completer.completionCount() == 0 or (
                self.completer.completionCount() == 1 and self.completer.currentCompletion() == prefix):
            self.completer.popup().hide()
            return
        self.completer.popup().setCurrentIndex(self.completer.completionModel().index(0, 0))
        rect = self.cursorRect()
        rect.setWidth(self.completer.popup().sizeHintForColumn(0)
                      + self.completer.popup().verticalScrollBar().sizeHint().width() + 24)
        rect.translate(self.gutter_width(), 0)
        self.completer.complete(rect)

    # ── editing helpers ──
    def toggle_comment(self):
        tc = self.textCursor()
        doc = self.document()
        start = doc.findBlock(tc.selectionStart())
        end = doc.findBlock(tc.selectionEnd() - (1 if tc.hasSelection() else 0))
        blocks, b = [], start
        while b.isValid():
            blocks.append(b)
            if b == end:
                break
            b = b.next()
        non_empty = [x for x in blocks if x.text().strip()]
        uncomment = non_empty and all(x.text().lstrip().startswith("--") for x in non_empty)
        tc.beginEditBlock()
        for blk in blocks:
            text = blk.text()
            if not text.strip():
                continue
            c = QTextCursor(blk)
            indent = len(text) - len(text.lstrip())
            c.movePosition(QTextCursor.Right, QTextCursor.MoveAnchor, indent)
            if uncomment:
                n = 3 if text.lstrip().startswith("-- ") else 2
                c.movePosition(QTextCursor.Right, QTextCursor.KeepAnchor, n)
                c.removeSelectedText()
            else:
                c.insertText("-- ")
        tc.endEditBlock()

    def _indent(self, back=False):
        tc = self.textCursor()
        if not tc.hasSelection() and not back:
            tc.insertText("    ")
            return
        doc = self.document()
        start = doc.findBlock(tc.selectionStart())
        end = doc.findBlock(tc.selectionEnd() - (1 if tc.hasSelection() else 0))
        tc.beginEditBlock()
        b = start
        while b.isValid():
            c = QTextCursor(b)
            if back:
                text = b.text()
                n = min(4, len(text) - len(text.lstrip(" ")))
                if n:
                    c.movePosition(QTextCursor.Right, QTextCursor.KeepAnchor, n)
                    c.removeSelectedText()
            else:
                c.insertText("    ")
            if b == end:
                break
            b = b.next()
        tc.endEditBlock()

    def event(self, e):
        if e.type() == QEvent.ShortcutOverride and e.key() in (Qt.Key_Return, Qt.Key_Enter)                 and e.modifiers() & Qt.ControlModifier:
            e.accept()
            return True
        return super().event(e)

    def keyPressEvent(self, e):
        popup = self.completer.popup()
        if e.key() == Qt.Key_Tab and not e.modifiers() and self.tab_hook and self.tab_hook(self):
            popup.hide()
            return
        if e.key() in (Qt.Key_Return, Qt.Key_Enter) and e.modifiers() & Qt.ControlModifier:
            popup.hide()
            self.run_current.emit()
            return
        if popup.isVisible() and e.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Tab, Qt.Key_Escape, Qt.Key_Up,
                                             Qt.Key_Down, Qt.Key_PageUp, Qt.Key_PageDown):
            if e.key() == Qt.Key_Tab:
                idx = popup.currentIndex()
                if idx.isValid():
                    self._insert_completion(idx.data())
                popup.hide()
                return
            e.ignore()
            return
        if e.key() == Qt.Key_Space and e.modifiers() & Qt.ControlModifier:
            self._show_completer(force=True)
            return
        if e.key() == Qt.Key_Slash and e.modifiers() & Qt.ControlModifier:
            self.toggle_comment()
            return
        if e.key() == Qt.Key_Tab and not e.modifiers():
            self._indent()
            return
        if e.key() == Qt.Key_Backtab:
            self._indent(back=True)
            return
        if e.key() in (Qt.Key_Return, Qt.Key_Enter) and not e.modifiers():
            text = self.textCursor().block().text()
            indent = text[:len(text) - len(text.lstrip())]
            super().keyPressEvent(e)
            self.insertPlainText(indent)
            return
        super().keyPressEvent(e)
        if e.text() and (e.text().isalnum() or e.text() in "_$"):
            self._show_completer()
        elif e.key() in (Qt.Key_Backspace,) and popup.isVisible():
            self._show_completer()
        elif e.text():
            popup.hide()

    def wheelEvent(self, e):
        if e.modifiers() & Qt.ControlModifier:
            if e.angleDelta().y() > 0:
                self.zoomIn(1)
            else:
                self.zoomOut(1)
            self._update_gutter_width()
            return
        super().wheelEvent(e)


def read_only_code_view(parent=None):
    ed = CodeEditor(parent, font_size=10)
    ed.setReadOnly(True)
    return ed

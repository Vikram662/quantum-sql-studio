"""Editor productivity: snippets (abbreviation + Tab) and named query parameters (:name)."""
import re

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QListWidgetItem, QMessageBox, QPushButton, QSplitter, QVBoxLayout,
                               QWidget)

from . import icons
from .config import settings
from .db import literal
from .editor import CodeEditor
from .sqlformat import _tokens
from .ui import muted

CURSOR = "$|"

DEFAULT_SNIPPETS = {
    "sel": "SELECT *\nFROM $|\nWHERE 1\nLIMIT 100;",
    "selw": "SELECT *\nFROM $|\nWHERE  = ;",
    "cnt": "SELECT COUNT(*) FROM $|;",
    "ins": "INSERT INTO $| ()\nVALUES ();",
    "upd": "UPDATE $|\nSET  = \nWHERE ;",
    "del": "DELETE FROM $|\nWHERE ;",
    "grp": "SELECT , COUNT(*) AS n\nFROM $|\nGROUP BY \nORDER BY n DESC;",
    "dup": "-- find duplicate values\nSELECT , COUNT(*) AS n\nFROM $|\nGROUP BY \nHAVING n > 1\nORDER BY n DESC;",
    "lj": "LEFT JOIN $| ON  = ",
    "ij": "INNER JOIN $| ON  = ",
    "ct": ("CREATE TABLE $| (\n    id INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,\n"
           "    name VARCHAR(255) NOT NULL,\n    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,\n"
           "    updated_at TIMESTAMP NULL DEFAULT NULL ON UPDATE CURRENT_TIMESTAMP\n"
           ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;"),
    "idx": "CREATE INDEX idx_$| ON  ();",
    "fk": "ALTER TABLE $|\n    ADD CONSTRAINT fk_ FOREIGN KEY () REFERENCES  () ON DELETE CASCADE;",
    "addcol": "ALTER TABLE $|\n    ADD COLUMN  VARCHAR(255) NULL AFTER ;",
    "proc": "DELIMITER $$\nCREATE PROCEDURE $|()\nBEGIN\n    \nEND$$\nDELIMITER ;",
    "trg": ("DELIMITER $$\nCREATE TRIGGER $|\nBEFORE INSERT ON  FOR EACH ROW\nBEGIN\n    \nEND$$\n"
            "DELIMITER ;"),
    "size": ("-- table sizes in the current database\nSELECT TABLE_NAME, TABLE_ROWS,\n"
             "       ROUND((DATA_LENGTH + INDEX_LENGTH) / 1048576, 2) AS size_mb\n"
             "FROM information_schema.TABLES\nWHERE TABLE_SCHEMA = DATABASE()$|\nORDER BY size_mb DESC;"),
    "proc?": "SHOW FULL PROCESSLIST;$|",
    "txn": "START TRANSACTION;\n$|\n-- COMMIT;  or  ROLLBACK;",
    "json": "SELECT JSON_UNQUOTE(JSON_EXTRACT($|, '$.key')) AS value\nFROM ;",
    "page": "SELECT *\nFROM $|\nORDER BY id\nLIMIT :page_size OFFSET :offset;",
}


def snippets():
    user = settings.get("snippets")
    return dict(user) if isinstance(user, dict) else dict(DEFAULT_SNIPPETS)


def expand_snippet(editor):
    """If the word before the cursor is a snippet abbreviation, replace it. Returns True when expanded."""
    tc = editor.textCursor()
    if tc.hasSelection():
        return False
    before = tc.block().text()[:tc.positionInBlock()]
    m = re.search(r"([\w?]+)$", before)
    if not m:
        return False
    body = snippets().get(m.group(1))
    if body is None:
        return False
    indent = before[:len(before) - len(before.lstrip())] if before.strip() == m.group(1) else ""
    body = body.replace("\n", "\n" + indent)
    offset = body.find(CURSOR)
    body = body.replace(CURSOR, "")
    tc.beginEditBlock()
    tc.movePosition(tc.MoveOperation.Left, tc.MoveMode.KeepAnchor, len(m.group(1)))
    start = tc.selectionStart()
    tc.insertText(body)
    tc.endEditBlock()
    if offset >= 0:
        tc.setPosition(start + offset)
        editor.setTextCursor(tc)
    return True


class SnippetDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("SQL snippets")
        self.resize(820, 520)
        self.data = snippets()
        lay = QVBoxLayout(self)
        lay.addWidget(muted("Type an abbreviation in the editor and press Tab to expand it. "
                            f"Put {CURSOR} where the cursor should land."))
        split = QSplitter()
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._show)
        ll.addWidget(self.list, 1)
        row = QHBoxLayout()
        for text, slot in (("Add", self._add), ("Delete", self._delete), ("Reset defaults", self._reset)):
            b = QPushButton(text)
            b.clicked.connect(slot)
            row.addWidget(b)
        ll.addLayout(row)
        split.addWidget(left)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.abbr = QLineEdit()
        self.abbr.setPlaceholderText("abbreviation")
        self.abbr.editingFinished.connect(self._rename)
        self.body = CodeEditor(font_size=10)
        self.body.textChanged.connect(self._store)
        rl.addWidget(self.abbr)
        rl.addWidget(self.body, 1)
        split.addWidget(right)
        split.setSizes([220, 600])
        lay.addWidget(split, 1)
        box = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        box.accepted.connect(self._save)
        box.rejected.connect(self.reject)
        lay.addWidget(box)
        self._reload()

    def _reload(self, select=None):
        self.list.blockSignals(True)
        self.list.clear()
        for k in sorted(self.data):
            it = QListWidgetItem(icons.icon("code"), k)
            it.setToolTip(self.data[k][:300])
            self.list.addItem(it)
        self.list.blockSignals(False)
        keys = sorted(self.data)
        self.list.setCurrentRow(keys.index(select) if select in keys else 0)
        self._show(self.list.currentRow())

    def _key(self):
        it = self.list.currentItem()
        return it.text() if it else None

    def _show(self, _row):
        k = self._key()
        self.abbr.setText(k or "")
        self.body.blockSignals(True)
        self.body.setPlainText(self.data.get(k, "") if k else "")
        self.body.blockSignals(False)

    def _store(self):
        k = self._key()
        if k:
            self.data[k] = self.body.toPlainText()

    def _rename(self):
        k, new = self._key(), self.abbr.text().strip()
        if k and new and new != k and re.fullmatch(r"[\w?]+", new):
            self.data[new] = self.data.pop(k)
            self._reload(new)

    def _add(self):
        n = 1
        while f"snippet{n}" in self.data:
            n += 1
        self.data[f"snippet{n}"] = "SELECT $|"
        self._reload(f"snippet{n}")
        self.abbr.setFocus()
        self.abbr.selectAll()

    def _delete(self):
        k = self._key()
        if k:
            self.data.pop(k, None)
            self._reload()

    def _reset(self):
        if QMessageBox.question(self, "Snippets", "Replace your snippets with the built-in set?") == QMessageBox.Yes:
            self.data = dict(DEFAULT_SNIPPETS)
            self._reload()

    def _save(self):
        settings.set("snippets", self.data)
        self.accept()


# ── named parameters ─────────────────────────────────────────────────────────
def find_params(sql):
    """Names of :param placeholders outside strings and comments, in order of appearance."""
    names = []
    toks = _tokens(sql)
    for i, (kind, val) in enumerate(toks):
        if kind == "op" and val == ":" and i + 1 < len(toks) and toks[i + 1][0] == "word":
            prev = toks[i - 1] if i else ("ws", " ")
            if prev[0] in ("word", "num", "ident") or prev[1] in (":", ")"):
                continue
            if toks[i + 1][1] not in names:
                names.append(toks[i + 1][1])
    return names


def bind_params(sql, values):
    """Replace :name tokens with SQL literals (values: name -> (text, raw))."""
    out = []
    toks = _tokens(sql)
    i = 0
    while i < len(toks):
        kind, val = toks[i]
        if kind == "op" and val == ":" and i + 1 < len(toks) and toks[i + 1][0] == "word" \
                and toks[i + 1][1] in values:
            prev = toks[i - 1] if i else ("ws", " ")
            if not (prev[0] in ("word", "num", "ident") or prev[1] in (":", ")")):
                text, raw = values[toks[i + 1][1]]
                out.append(text if raw else _auto_literal(text))
                i += 2
                continue
        out.append(val)
        i += 1
    return "".join(out)


def _auto_literal(text):
    t = text.strip()
    if t.upper() == "NULL":
        return "NULL"
    if re.fullmatch(r"-?\d+(\.\d+)?", t):
        return t
    return literal(text)


class ParamDialog(QDialog):
    def __init__(self, parent, names):
        super().__init__(parent)
        self.setWindowTitle("Query parameters")
        self.resize(520, 120 + 36 * len(names))
        saved = settings.get("param_values", {}) or {}
        lay = QVBoxLayout(self)
        lay.addWidget(muted("Numbers and NULL are used as-is, everything else is quoted. "
                            "Tick “raw” to insert the text unchanged (e.g. a column name or NOW())."))
        grid = QGridLayout()
        self.fields = {}
        for r, name in enumerate(names):
            lbl = QLabel(f":{name}")
            lbl.setStyleSheet("font-weight: 600;")
            edit = QLineEdit(str(saved.get(name, {}).get("v", "")))
            raw = QCheckBox("raw")
            raw.setChecked(bool(saved.get(name, {}).get("raw")))
            grid.addWidget(lbl, r, 0, Qt.AlignRight)
            grid.addWidget(edit, r, 1)
            grid.addWidget(raw, r, 2)
            self.fields[name] = (edit, raw)
        lay.addLayout(grid)
        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.button(QDialogButtonBox.Ok).setText("Run")
        box.button(QDialogButtonBox.Ok).setProperty("primary", True)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        lay.addWidget(box)
        if names:
            self.fields[names[0]][0].setFocus()

    def values(self):
        vals = {n: (e.text(), r.isChecked()) for n, (e, r) in self.fields.items()}
        saved = settings.get("param_values", {}) or {}
        saved.update({n: {"v": v, "raw": raw} for n, (v, raw) in vals.items()})
        settings.set("param_values", dict(list(saved.items())[-200:]))
        return vals

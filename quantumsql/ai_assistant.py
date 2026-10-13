"""AI SQL assistant: write SQL from plain language, explain, fix and optimise queries.

Works with Anthropic Claude, OpenAI, Google Gemini, OpenRouter, Groq, DeepSeek, a local Ollama or any
OpenAI-compatible server (see ai_providers). Generated SQL is only inserted into the editor, never executed.
"""
import re

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel,
                               QLineEdit, QPlainTextEdit, QPushButton, QTextBrowser, QVBoxLayout)

from . import ai_providers as ai
from . import icons
from .db import run_async
from .theme import T
from .ui import muted

SCHEMA_CHAR_LIMIT = 400_000

MODES = [
    ("write", "Write SQL from a description"),
    ("explain", "Explain the query"),
    ("fix", "Fix the error"),
    ("optimize", "Optimise / suggest indexes"),
]

INSTRUCTIONS = """You are a MySQL / MariaDB expert built into Quantum SQL, a desktop database client.
The user may write in any language (including Hindi or Hinglish), but always reply in English.
When you write SQL, put the final statement(s) in one ```sql code block and use only the tables and columns in the
schema below, qualifying columns when a name is ambiguous. Exploratory SELECTs should have a LIMIT, and every UPDATE or
DELETE must have a WHERE clause. The user reviews and runs the SQL themselves. Keep explanations short and practical."""


def extract_sql(text):
    blocks = re.findall(r"```(?:sql|mysql)?\s*\n(.*?)```", text, re.S | re.I)
    return blocks[-1].strip() if blocks else ""


def load_schema(conn, db):
    """Compact schema text: one line per table with columns, keys and foreign keys (never row data)."""
    cols = {}
    for table, col, ctype, key, extra in conn.rows(
            "SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, COLUMN_KEY, EXTRA FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA=%s ORDER BY TABLE_NAME, ORDINAL_POSITION", (db,)):
        flag = " PK" if key == "PRI" else " UNIQUE" if key == "UNI" else ""
        flag += " AUTO_INCREMENT" if "auto_increment" in (extra or "") else ""
        cols.setdefault(table, []).append(f"{col} {ctype}{flag}")
    fks = {}
    for table, col, rtable, rcol in conn.rows(
            "SELECT TABLE_NAME, COLUMN_NAME, REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME FROM "
            "information_schema.KEY_COLUMN_USAGE WHERE TABLE_SCHEMA=%s AND REFERENCED_TABLE_NAME IS NOT NULL",
            (db,)):
        fks.setdefault(table, []).append(f"{col} -> {rtable}.{rcol}")
    rows = {t: n for t, n in conn.rows("SELECT TABLE_NAME, TABLE_ROWS FROM information_schema.TABLES "
                                       "WHERE TABLE_SCHEMA=%s", (db,))}
    lines, size, omitted = [], 0, []
    for table in sorted(cols):
        line = f"{table} (~{rows.get(table) or 0} rows): " + ", ".join(cols[table])
        if fks.get(table):
            line += " | FK " + ", ".join(fks[table])
        if size + len(line) > SCHEMA_CHAR_LIMIT:
            omitted.append(table)
            continue
        lines.append(line)
        size += len(line)
    return "\n".join(lines), omitted


class AiSettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("AI assistant settings")
        self.resize(640, 360)
        lay = QVBoxLayout(self)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.provider = QComboBox()
        for pid, p in ai.PROVIDERS.items():
            self.provider.addItem(p["label"], pid)
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.Password)
        self.key_link = QLabel()
        self.key_link.setOpenExternalLinks(True)
        self.base = QLineEdit()
        self.model = QComboBox()
        self.model.setEditable(True)
        self.model.setMinimumWidth(320)
        b_refresh = QPushButton(icons.icon("refresh"), "Refresh")
        b_refresh.setToolTip("Load the models your key can use")
        b_refresh.clicked.connect(self._refresh_models)
        mrow = QHBoxLayout()
        mrow.addWidget(self.model, 1)
        mrow.addWidget(b_refresh)
        form.addRow("Provider", self.provider)
        form.addRow("API key", self.key)
        form.addRow("", self.key_link)
        form.addRow("Base URL", self.base)
        form.addRow("Model", mrow)
        lay.addLayout(form)
        self.privacy = muted("")
        self.privacy.setWordWrap(True)
        lay.addWidget(self.privacy)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        lay.addStretch(1)
        row = QHBoxLayout()
        b_test = QPushButton(icons.icon("activity"), "Test connection")
        b_test.clicked.connect(self._test)
        box = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        box.accepted.connect(self._save)
        box.rejected.connect(self.reject)
        row.addWidget(b_test)
        row.addStretch(1)
        row.addWidget(box)
        lay.addLayout(row)
        self.provider.currentIndexChanged.connect(self._load)
        self.provider.setCurrentIndex(max(0, self.provider.findData(ai.current()["provider"])))
        self._load()

    def _pid(self):
        return self.provider.currentData()

    def _load(self, *_):
        pid = self._pid()
        p = ai.PROVIDERS[pid]
        cfg = ai.config_for(pid)
        self.key.setText(cfg["key"])
        self.key.setEnabled(not p.get("no_key") or pid == "custom")
        self.key.setPlaceholderText("not needed" if p.get("no_key") and pid != "custom" else
                                    f"paste your key{' (or set ' + p['env'] + ')' if p['env'] else ''}")
        self.key_link.setText(f"<a href='{p['key_url']}'>{'Get an API key' if not p.get('no_key') else 'Download'}"
                              f"</a>" if p["key_url"] else "")
        self.base.setText(cfg["base"])
        self.base.setEnabled(p["kind"] != "anthropic")
        self.model.clear()
        self.model.addItems(p["models"])
        self.model.setCurrentText(cfg["model"])
        self.model.lineEdit().setPlaceholderText("type a model id or press Refresh")
        self.privacy.setText(
            "Runs on this computer — nothing leaves your machine." if pid == "ollama" else
            "Sent to the provider: your question, the SQL you select and the current database's table / column "
            "names. Row data is never sent.")
        self.status.setText("")

    def _cfg(self):
        pid = self._pid()
        p = ai.PROVIDERS[pid]
        return {"provider": pid, "label": p["label"], "kind": p["kind"], "base": self.base.text().strip() or p["base"],
                "model": self.model.currentText().strip(), "key": self.key.text().strip(),
                "no_key": p.get("no_key", False)}

    def _say(self, text, kind="muted"):
        self.status.setText(text)
        self.status.setStyleSheet(f"color: {T.get(kind, T['muted'])};")

    def _refresh_models(self):
        cfg = self._cfg()
        if not cfg["key"] and not cfg["no_key"]:
            self._say("Enter the API key first.", "warning")
            return
        self._say("Loading models…")

        def done(models):
            cur = self.model.currentText()
            self.model.clear()
            self.model.addItems(models)
            self.model.setCurrentText(cur if cur in models or not models else models[0])
            self._say(f"{len(models)} models available.", "success")
        run_async(lambda: ai.list_models(cfg), done, lambda e: self._say(e, "danger"))

    def _test(self):
        cfg = self._cfg()
        problem = ai.ready(cfg)
        if problem:
            self._say(problem, "warning")
            return
        self._say(f"Asking {cfg['model']}…")

        def work(task):
            return ai.stream_answer(task, cfg, "Reply with exactly: OK", "", "Connection test")
        run_async(work, lambda t: self._say(f"✓ Working — {cfg['model']} replied: {t.strip()[:60]}", "success"),
                  lambda e: self._say(e, "danger"))

    def _save(self):
        cfg = self._cfg()
        if not cfg["model"]:
            self._say("Choose a model.", "warning")
            return
        ai.save(cfg["provider"], cfg["key"], cfg["model"], cfg["base"])
        self.accept()


class AiDialog(QDialog):
    """Non-modal assistant bound to a query tab."""

    _schema_cache = {}

    def __init__(self, tab, mode="write", text=""):
        super().__init__(tab)
        self.tab = tab
        self.task = None
        self.answer = ""
        self.setWindowTitle("AI SQL assistant")
        self.resize(820, 640)
        self.setWindowFlag(Qt.WindowMaximizeButtonHint, True)
        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        self.mode = QComboBox()
        for key, label in MODES:
            self.mode.addItem(label, key)
        self.mode.setCurrentIndex([k for k, _ in MODES].index(mode))
        self.mode.currentIndexChanged.connect(self._mode_changed)
        self.ctx = muted("")
        self.ctx.setWordWrap(True)
        b_set = QPushButton(icons.icon("settings"), "Provider / model")
        b_set.setToolTip("Choose the AI provider, model and API key")
        b_set.clicked.connect(self._settings)
        top.addWidget(self.mode)
        top.addWidget(self.ctx, 1)
        top.addWidget(b_set)
        lay.addLayout(top)
        self.prompt = QPlainTextEdit()
        self.prompt.setMaximumHeight(120)
        self.prompt.setPlainText(text)
        lay.addWidget(self.prompt)
        row = QHBoxLayout()
        self.b_send = QPushButton(icons.icon("sparkle"), "Ask AI")
        self.b_send.setProperty("primary", True)
        self.b_send.setDefault(True)
        self.b_send.clicked.connect(self.send)
        row.addWidget(muted("Ctrl+Enter to send"))
        row.addStretch(1)
        row.addWidget(self.b_send)
        lay.addLayout(row)
        self.out = QTextBrowser()
        self.out.setOpenExternalLinks(True)
        lay.addWidget(self.out, 1)
        actions = QHBoxLayout()
        self.b_insert = QPushButton(icons.icon("query"), "Insert SQL into editor")
        self.b_insert.clicked.connect(lambda: self._use_sql(replace=False))
        self.b_replace = QPushButton(icons.icon("edit"), "Replace editor / selection")
        self.b_replace.clicked.connect(lambda: self._use_sql(replace=True))
        b_copy = QPushButton(icons.icon("copy"), "Copy answer")
        b_copy.clicked.connect(lambda: QApplication.clipboard().setText(self.answer))
        actions.addWidget(muted("SQL is only inserted, never run automatically."))
        actions.addStretch(1)
        actions.addWidget(b_copy)
        actions.addWidget(self.b_replace)
        actions.addWidget(self.b_insert)
        lay.addLayout(actions)
        self._update_buttons()
        self._mode_changed()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter) and e.modifiers() & Qt.ControlModifier:
            self.send()
            return
        super().keyPressEvent(e)

    def _settings(self):
        if AiSettingsDialog(self).exec():
            self._mode_changed()

    def _mode_changed(self, *_):
        hints = {"write": "Describe what you need, e.g. “top 10 customers by revenue last month” or "
                          "“cancelled orders of last week, city-wise”",
                 "explain": "Optional: what do you want to know about the query?",
                 "fix": "Optional: anything else about the error?",
                 "optimize": "Optional: what is slow / how big are the tables?"}
        self.prompt.setPlaceholderText(hints[self.mode.currentData()])
        cfg = ai.current()
        db = self.tab.current_db()
        self.ctx.setText(f"{cfg['label'].split(' —')[0]} · {cfg['model'] or 'no model'}   |   "
                         f"{self.tab.session.name} · {db or 'no database selected (no schema context)'}")

    def _update_buttons(self):
        has_sql = bool(extract_sql(self.answer))
        self.b_insert.setEnabled(has_sql and self.task is None)
        self.b_replace.setEnabled(has_sql and self.task is None)

    def _query_text(self):
        tc = self.tab.editor.textCursor()
        if tc.hasSelection():
            return tc.selectedText().replace(" ", "\n")
        from .db import statement_at
        st = statement_at(self.tab.editor.toPlainText(), tc.position())
        return st[0] if st else self.tab.editor.toPlainText()

    def _build_prompt(self):
        mode = self.mode.currentData()
        note = self.prompt.toPlainText().strip()
        sql = self._query_text().strip()
        if mode == "write":
            if not note:
                return None
            return note + (f"\n\nThe editor currently contains (use it if relevant):\n```sql\n{sql}\n```" if sql else "")
        if not sql:
            return None
        if mode == "explain":
            return (f"Explain what this query does, step by step, and point out anything risky or slow.\n"
                    f"```sql\n{sql}\n```" + (f"\n\n{note}" if note else ""))
        if mode == "fix":
            err = getattr(self.tab, "last_error", "") or "(no error captured — find the problem)"
            failed = getattr(self.tab, "last_error_sql", "") or sql
            return (f"This statement failed:\n```sql\n{failed}\n```\nError:\n{err}\n\nExplain the cause in one or two "
                    "sentences and give the corrected SQL." + (f"\n\n{note}" if note else ""))
        return (f"Make this query faster. Suggest a rewritten query and any CREATE INDEX statements, and say why.\n"
                f"```sql\n{sql}\n```" + (f"\n\n{note}" if note else ""))

    def send(self):
        if self.task is not None:
            self.task.cancelled = True
            return
        cfg = ai.current()
        if ai.ready(cfg):
            if not AiSettingsDialog(self).exec():
                return
            cfg = ai.current()
            self._mode_changed()
            if ai.ready(cfg):
                self.out.setMarkdown(f"*{ai.ready(cfg)}*")
                return
        prompt = self._build_prompt()
        if prompt is None:
            self.out.setMarkdown("*Type a question, or put a query in the editor first.*")
            return
        session, db = self.tab.session, self.tab.current_db()
        version = session.conn.server_version
        cache_key = (id(session), db)
        conn = session.conn
        cache = AiDialog._schema_cache

        def work(task):
            if cache_key not in cache:
                cache[cache_key] = load_schema(conn, db) if db else ("", [])
            schema, omitted = cache[cache_key]
            context = (f"Server: {version}. Current database: {db or '(none)'}.\n"
                       f"Schema (table (~rows): columns | foreign keys):\n{schema or '(no database selected)'}")
            if omitted:
                context += (f"\n({len(omitted)} more tables not listed because the schema is very large: "
                            f"{', '.join(omitted[:200])})")
            return ai.stream_answer(task, cfg, INSTRUCTIONS, context, prompt)

        def progress(_pct, text):
            if text:
                self.answer = text
                self.out.setMarkdown(text)
                sb = self.out.verticalScrollBar()
                sb.setValue(sb.maximum())

        def done(text):
            self.task = None
            self.answer = text
            self.out.setMarkdown(text)
            self.b_send.setText("Ask AI")
            self._update_buttons()

        def failed(e):
            self.task = None
            self.b_send.setText("Ask AI")
            self.out.setHtml(f"<p style='color:{T['danger']}'>{e}</p>")
            self._update_buttons()

        self.answer = ""
        self.out.setMarkdown(f"*Waiting for {cfg['model']}…*")
        self.b_send.setText("Stop")
        self._update_buttons()
        self.task = run_async(work, done, failed, progress)

    def _use_sql(self, replace):
        sql = extract_sql(self.answer)
        if not sql:
            return
        ed = self.tab.editor
        tc = ed.textCursor()
        if replace:
            if not tc.hasSelection():
                tc.select(tc.SelectionType.Document)
            tc.insertText(sql + ("\n" if not sql.endswith("\n") else ""))
        else:
            tc.movePosition(tc.MoveOperation.End)
            prefix = "" if not ed.toPlainText().strip() else "\n\n"
            tc.insertText(prefix + sql + "\n")
        ed.setTextCursor(tc)
        ed.setFocus()
        self.tab.main.status("AI SQL inserted — review it, then run with F9 / Ctrl+Enter.")

    def reject(self):
        if self.task is not None:
            self.task.cancelled = True
        super().reject()

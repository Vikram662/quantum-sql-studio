"""Query tab: SQL editor with multiple result sets, explain, stop and file I/O."""
import os
import re
import time

import pymysql
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor, QTextDocument
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
                               QPushButton, QSplitter, QTabWidget, QTextEdit, QVBoxLayout, QWidget)

from . import icons, theme
from .config import history, settings
from .db import (is_ddl, is_write, missing_where, query_log, run_async, split_sql, statement_at,
                 statement_kind)
from .editor import CodeEditor
from .editor_tools import ParamDialog, bind_params, expand_snippet, find_params
from .explain_view import ExplainView, table_aliases
from .result_tools import ResultPanel
from .sqlformat import format_script
from .grid import DataGrid, ResultModel
from .theme import T
from .ui import bar, muted, tool

MAX_ROWS = 50_000


class QueryTab(QWidget):
    title_changed = Signal(object)

    def __init__(self, main, session, db=None, sql="", number=1):
        super().__init__()
        self.main = main
        self.session = session
        self.conn = None
        self.number = number
        self.path = None
        self.running = False
        self.in_txn = False
        self.txn_rows = 0
        self._t0 = 0

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self.db_combo = QComboBox()
        self.db_combo.setMinimumWidth(170)
        self.db_combo.setToolTip("Default database for this query tab")
        self.db_combo.currentTextChanged.connect(self._db_changed)
        self.b_run = tool("play", "Execute all / selection (F9)", self.run_all, "Run")
        self.b_run_cur = tool("play-line", "Execute current statement (Ctrl+Enter)", self.run_current, "Current")
        self.b_explain = tool("chart", "Explain current statement", self.explain, "Explain")
        self.b_stop = tool("stop", "Stop running query", self.stop, "Stop")
        self.b_stop.setEnabled(False)
        self.b_safe = tool("shield", "Safe transaction — write statements run inside a transaction; you review "
                                     "the result, then Commit or Rollback", None, "Safe txn", checkable=True)
        self.b_safe.setChecked(session.env == "production" or bool(settings.get("safe_txn", False)))
        self.b_safe.toggled.connect(lambda on: settings.set("safe_txn", on)
                                    if self.session.env != "production" else None)
        self.session_label = muted("")
        self.last_error = self.last_error_sql = ""
        self.b_ai = tool("sparkle", "AI assistant — write, explain, fix or optimise SQL (Ctrl+I)", self.ask_ai, "AI")
        self.b_fix = tool("sparkle", "Ask Claude why the last statement failed and how to fix it",
                          lambda: self.ask_ai("fix"), "Fix with AI")
        self.b_fix.hide()
        lay.addWidget(bar(self.b_run, self.b_run_cur, self.b_explain, self.b_stop, "|", self.b_safe, "|",
                          self.b_ai, self.b_fix, "|",
                          tool("open", "Open SQL file (Ctrl+O)", self.open_file),
                          tool("save", "Save SQL file", self.save_file),
                          tool("comment", "Toggle comment (Ctrl+/)", lambda: self.editor.toggle_comment()),
                          tool("format", "Format / beautify SQL (Ctrl+Shift+F)", self.beautify, "Format"),
                          tool("edit", "Uppercase keywords only", self.uppercase_keywords),
                          "|", muted("Database"), self.db_combo, None, self.session_label))

        self.txn_label = QLabel("")
        self.txn_label.setStyleSheet(f"color: {T['warning']}; font-weight: 600;")
        b_commit = QPushButton(icons.icon("check"), "Commit")
        b_commit.setProperty("primary", True)
        b_commit.clicked.connect(lambda: self.end_transaction(True))
        b_rollback = QPushButton(icons.icon("undo"), "Rollback")
        b_rollback.clicked.connect(lambda: self.end_transaction(False))
        self.txn_bar = bar(self.txn_label, None, b_rollback, b_commit, object_name="banner")
        self.txn_bar.hide()
        lay.addWidget(self.txn_bar)

        split = QSplitter(Qt.Vertical)
        editor_box = QWidget()
        ev = QVBoxLayout(editor_box)
        ev.setContentsMargins(0, 0, 0, 0)
        ev.setSpacing(0)
        self.editor = CodeEditor(font_size=11)
        self.editor.setPlainText(sql)
        self.editor.document().setModified(False)
        self.editor.run_current.connect(self.run_current)
        self.editor.tab_hook = expand_snippet
        self.editor.document().modificationChanged.connect(lambda *_: self.title_changed.emit(self))
        ev.addWidget(self.editor, 1)
        self.find = QLineEdit()
        self.find.setPlaceholderText("Find…  (Enter = next, Shift+Enter = previous, Esc = close)")
        self.find.addAction(icons.icon("search"), QLineEdit.LeadingPosition)
        self.find.returnPressed.connect(lambda: self._find())
        self.find.hide()
        ev.addWidget(self.find)
        split.addWidget(editor_box)

        self.results = QTabWidget()
        self.results.setObjectName("subtabs")
        self.messages = QPlainTextEdit()
        self.messages.setReadOnly(True)
        self.messages.setFont(theme.code_font(10))
        self.messages.setPlaceholderText("Press F9 to run the whole script, Ctrl+Enter to run the statement "
                                         "under the cursor.")
        self.results.addTab(self.messages, icons.icon("log"), "Messages")
        split.addWidget(self.results)
        split.setSizes([420, 320])
        split.setStretchFactor(0, 1)
        lay.addWidget(split, 1)

        self._wanted_db = db
        self._dbs_loaded = False
        if db:
            self.db_combo.blockSignals(True)
            self.db_combo.addItem(icons.icon("database"), db)
            self.db_combo.blockSignals(False)
        self._refresh_databases(db)

    # ── title / state ──
    def title(self):
        name = os.path.basename(self.path) if self.path else ("Query" if self.number == 1 else f"Query #{self.number}")
        return name + (" •" if self.editor.document().isModified() and (self.path or self.editor.toPlainText())
                       else "")

    def current_db(self):
        if not self._dbs_loaded:
            return self._wanted_db
        return self.db_combo.currentText() or None

    def _refresh_databases(self, select=None):
        self.session_label.setText(f"{self.session.name}")
        conn = self.session.conn

        def done(dbs):
            cur = select or self.db_combo.currentText()
            self.db_combo.blockSignals(True)
            self.db_combo.clear()
            self.db_combo.addItem("")
            for d in dbs:
                self.db_combo.addItem(icons.icon("database"), d)
            self.db_combo.setCurrentText(cur or "")
            self.db_combo.blockSignals(False)
            self._dbs_loaded = True
            self._db_changed(self.db_combo.currentText())
        run_async(conn.databases, done)

    def _db_changed(self, db):
        conn = self.session.conn
        dbs = [self.db_combo.itemText(i) for i in range(1, self.db_combo.count())]
        if not db:
            self.editor.set_schema_words(dbs)
            return

        def done(mapping):
            words = list(mapping.keys())
            for cols in mapping.values():
                words.extend(cols)
            self.editor.set_schema_words(words + dbs)
        run_async(lambda: conn.column_names(db), done)

    def set_session(self, session):
        self.session = session
        if self.conn:
            self.conn.close()
            self.conn = None
        self._refresh_databases()

    # ── running ──
    def run_all(self):
        tc = self.editor.textCursor()
        text = tc.selectedText().replace(" ", "\n") if tc.hasSelection() else self.editor.toPlainText()
        self._execute([s for s, _, _ in split_sql(text)])

    def run_current(self):
        st = statement_at(self.editor.toPlainText(), self.editor.textCursor().position())
        if st:
            self._flash(st[1], st[2])
            self._execute([st[0]])

    def explain(self):
        st = statement_at(self.editor.toPlainText(), self.editor.textCursor().position())
        if not st or self.running:
            return
        sql = st[0]
        if statement_kind(sql) == "EXPLAIN":
            sql = sql.split(None, 1)[1] if len(sql.split(None, 1)) > 1 else ""
        names = find_params(sql)
        if names:
            dlg = ParamDialog(self, names)
            if not dlg.exec():
                return
            sql = bind_params(sql, dlg.values())
        self._flash(st[1], st[2])
        if self.conn is None:
            self.conn = self.session.conn.clone()
        conn, db = self.conn, self.current_db()
        tables = set(table_aliases(sql).values())
        self.main.status("Explaining…")

        def work():
            with conn.lock:
                conn._ensure()
                if db:
                    conn.use(db)
                cols, rows, _ = conn.query("EXPLAIN " + sql)
                meta = {}
                for t in tables:
                    tdb, name = t.split(".", 1) if "." in t else (db, t)
                    try:
                        meta[t] = {"columns": [c["Field"] for c in conn.columns(tdb, name)],
                                   "indexes": conn.indexes(tdb, name)}
                    except Exception:  # noqa: BLE001 - advice is best effort
                        pass
                return cols, rows, meta

        def done(res):
            cols, rows, meta = res
            self._clear_results()
            view = ExplainView(sql, cols, rows, meta,
                               open_sql=lambda s: self.main.new_query(self.session, db, s + "\n"))
            self.results.addTab(view, icons.icon("chart"), "Visual explain")
            grid = DataGrid()
            grid.set_model(ResultModel(cols, rows))
            self.results.addTab(grid, icons.icon("table"), "EXPLAIN")
            self.results.setCurrentWidget(view)
            self._log([("ok", f"EXPLAIN  ·  {_short(sql)}")])
            self.main.status("Explain ready — see the advisor for index suggestions.")

        def failed(e):
            self._log([("error", e)])
            self.results.setCurrentWidget(self.messages)
            self.main.status(e, error=True)
        run_async(work, done, failed)

    def ask_ai(self, mode=None):
        from .ai_assistant import AiDialog
        tc = self.editor.textCursor()
        if mode is None:
            mode = "write" if not self.editor.toPlainText().strip() else ("explain" if tc.hasSelection() else "write")
        dlg = AiDialog(self, mode)
        dlg.show()
        if mode == "fix":
            dlg.send()

    def _clear_results(self):
        while self.results.count() > 1:
            w = self.results.widget(1)
            self.results.removeTab(1)
            w.deleteLater()

    def _flash(self, start, end):
        tc = QTextCursor(self.editor.document())
        tc.setPosition(start)
        tc.setPosition(min(end, len(self.editor.toPlainText())), QTextCursor.KeepAnchor)
        fmt = QTextCharFormat()
        fmt.setBackground(QColor(T["selection"]))
        sel = QTextEdit.ExtraSelection()
        sel.cursor, sel.format = tc, fmt
        self.editor.setExtraSelections([sel])

    # ── safe mode ──
    def _execute(self, stmts):
        if self.running:
            return
        if not stmts:
            self.main.status("Nothing to execute.")
            return
        names = []
        for st in stmts:
            names += [n for n in find_params(st) if n not in names]
        if names:
            dlg = ParamDialog(self, names)
            if not dlg.exec():
                return
            values = dlg.values()
            stmts = [bind_params(st, values) for st in stmts]
        no_where = [s for s in stmts if missing_where(s)]
        destructive = [s for s in stmts if statement_kind(s) in ("DROP", "TRUNCATE")]
        prod = self.session.env == "production"
        writes = [s for s in stmts if is_write(s) or is_ddl(s)]
        if not (no_where or destructive or (prod and writes)):
            self._run(stmts)
            return
        if self.conn is None:
            self.conn = self.session.conn.clone()
        conn, db = self.conn, self.current_db()
        targets = [s for s in stmts if statement_kind(s) in ("UPDATE", "DELETE")][:25]

        def estimate():
            est = {}
            with conn.lock:
                conn._ensure()
                if db:
                    conn.use(db)
                for st in targets:
                    try:
                        cols, rows, _ = conn.query("EXPLAIN " + st, log=False)
                        i = [c.lower() for c in cols].index("rows")
                        est[st] = max(int(r[i] or 0) for r in rows) if rows else 0
                    except Exception:  # noqa: BLE001 - the estimate is best effort
                        est[st] = None
            return est

        self.main.status("Checking what these statements will change…")
        run_async(estimate, lambda est: self._confirm_risky(stmts, no_where, destructive, writes, prod, est),
                  lambda _e: self._confirm_risky(stmts, no_where, destructive, writes, prod, {}))

    def _confirm_risky(self, stmts, no_where, destructive, writes, prod, est):
        def line(st):
            n = est.get(st)
            return f"   • {_short(st, 110)}" + (f"   → ~{n:,} row(s)" if n is not None else "")
        parts = []
        if no_where:
            parts.append(f"⚠ {len(no_where)} statement(s) have NO WHERE clause and change EVERY row:")
            parts += [line(s) for s in no_where[:8]]
        if destructive:
            parts.append(f"⚠ {len(destructive)} statement(s) drop or empty objects (cannot be rolled back):")
            parts += [line(s) for s in destructive[:8]]
        if prod:
            others = [s for s in writes if s not in no_where and s not in destructive]
            if others:
                parts.append(f"Production server “{self.session.name}”: {len(others)} write statement(s):")
                parts += [line(s) for s in others[:10]]
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("Safe mode — please confirm")
        box.setText("\n".join(parts))
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.Cancel)
        box.button(QMessageBox.Yes).setText("Run")
        box.setDefaultButton(QMessageBox.Cancel)
        txn = QCheckBox("Run inside a transaction so I can review and roll back (DDL is never rolled back)")
        txn.setChecked(True)
        box.setCheckBox(txn)
        if box.exec() != QMessageBox.Yes:
            self.main.status("Cancelled.")
            return
        if txn.isChecked():
            self.b_safe.setChecked(True)
        self._run(stmts)

    def end_transaction(self, commit):
        if not self.in_txn or self.conn is None or self.running:
            return
        conn = self.conn

        def done(_):
            self.in_txn = False
            self.txn_rows = 0
            self._show_txn()
            self.main.status("Transaction committed." if commit else "Transaction rolled back — nothing changed.")
        run_async(lambda: conn.query("COMMIT" if commit else "ROLLBACK"), done,
                  lambda e: QMessageBox.critical(self, "Transaction", e))

    def _show_txn(self):
        self.txn_bar.setVisible(self.in_txn)
        if self.in_txn:
            self.txn_label.setText(f"⏸ Transaction open — {self.txn_rows:,} row(s) changed so far. Nothing is "
                                   "saved until you Commit; Rollback undoes it all.")

    def _run(self, stmts):
        if self.conn is None:
            self.conn = self.session.conn.clone()
        conn, db = self.conn, self.current_db()
        start_txn = self.b_safe.isChecked() and not self.in_txn and any(is_write(s) for s in stmts)
        self.running = True
        self._t0 = time.perf_counter()
        self._set_running(True)
        self.main.status(f"Executing {len(stmts)} statement(s)…")

        def work(task):
            results, log, error = [], [], None
            with conn.lock:
                if conn.conn is None:
                    conn.connect()
                else:
                    conn.conn.ping(reconnect=True)
                if db:
                    conn.use(db)
                self.thread_id = conn.thread_id()
                cur = conn.conn.cursor()
                changed = 0
                if start_txn:
                    cur.execute("START TRANSACTION")
                    log.append(("info", "START TRANSACTION (safe mode) — review, then Commit or Rollback"))
                for i, sql in enumerate(stmts):
                    if task.cancelled:
                        log.append(("info", "Execution stopped."))
                        break
                    t0 = time.perf_counter()
                    query_log.message.emit(sql, "sql")
                    try:
                        cur.execute(sql)
                        ms = (time.perf_counter() - t0) * 1000
                        if cur.description:
                            headers = [d[0] for d in cur.description]
                            rows = cur.fetchall()
                            total = len(rows)
                            results.append((headers, list(rows[:MAX_ROWS]), total, sql, ms))
                            log.append(("ok", f"{total:,} row(s) returned  ·  {ms:.1f} ms  ·  {_short(sql)}"))
                        else:
                            if is_write(sql):
                                changed += max(0, cur.rowcount)
                            log.append(("ok", f"{cur.rowcount:,} row(s) affected  ·  {ms:.1f} ms  ·  {_short(sql)}"))
                        while cur.nextset():
                            pass
                    except pymysql.MySQLError as exc:
                        code = exc.args[0] if exc.args else ""
                        msg = exc.args[1] if len(exc.args) > 1 else str(exc)
                        error = f"SQL Error ({code}): {msg}"
                        log.append(("error", f"{error}\n    in statement {i + 1}: {_short(sql, 200)}"))
                        break
                try:
                    cur.execute("SELECT DATABASE()")
                    current = cur.fetchone()[0]
                except pymysql.MySQLError:
                    current = db
                cur.close()
                in_txn = bool(conn.conn.server_status & 1)
            return results, log, error, current, in_txn, changed

        self.task = run_async(work, self._finished, self._crashed)
        self._stmts = stmts

    def _set_running(self, running):
        self.b_stop.setEnabled(running)
        for b in (self.b_run, self.b_run_cur, self.b_explain):
            b.setEnabled(not running)
        self.main.query_state_changed(self)

    def _crashed(self, msg):
        if self.in_txn:
            msg += "\n\nThe connection was reset — the open transaction was rolled back."
        self.in_txn = False
        self.txn_rows = 0
        self._show_txn()
        self.running = False
        self._set_running(False)
        self._log([("error", msg)])
        self.results.setCurrentWidget(self.messages)
        if self.conn:
            self.conn.close()
            self.conn = None

    def _finished(self, res):
        results, log, error, current, in_txn, changed = res
        self.txn_rows = self.txn_rows + changed if in_txn else 0
        self.in_txn = in_txn
        self._show_txn()
        self.running = False
        self._set_running(False)
        total_ms = (time.perf_counter() - self._t0) * 1000
        self._clear_results()
        for i, (headers, rows, total, sql, ms) in enumerate(results, 1):
            label = f"Result #{i} ({total:,})" + (f" — first {MAX_ROWS:,}" if total > MAX_ROWS else "")
            panel = ResultPanel(headers, rows, _result_name(sql, i), label)
            idx = self.results.addTab(panel, icons.icon("table"), label)
            self.results.setTabToolTip(idx, sql[:500])
        self._log(log)
        self.results.setCurrentIndex(1 if results and not error else 0)
        n_ok = len([x for x in log if x[0] == "ok"])
        self.last_error = error or ""
        self.last_error_sql = self._stmts[n_ok] if error and n_ok < len(self._stmts) else ""
        self.b_fix.setVisible(bool(error))
        if error:
            self.main.status(error, error=True)
        else:
            self.main.status(f"{n_ok} statement(s) executed in {total_ms:.1f} ms.")
        self.main.set_query_time(total_ms)
        history.add(";\n".join(self._stmts), self.session.name, current, total_ms, not error)
        self.main.history_changed()
        if current is not None and current != self.current_db():
            self._wanted_db = current
            self.db_combo.setCurrentText(current)
        if any(is_ddl(s) for s in self._stmts) and not error:
            self.main.refresh_db(self.session, current)

    def _log(self, entries):
        stamp = time.strftime("%H:%M:%S")
        colors = {"ok": T["text"], "error": T["danger"], "info": T["muted"]}
        self.messages.clear()
        for kind, text in entries:
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(colors.get(kind, T["text"])))
            cur = self.messages.textCursor()
            cur.movePosition(QTextCursor.End)
            cur.insertText(f"[{stamp}] {text}\n", fmt)

    def stop(self):
        if not self.running:
            return
        self.task.cancelled = True
        tid = getattr(self, "thread_id", None)
        if tid:
            meta = self.session.conn
            run_async(lambda: meta.execute(f"KILL QUERY {int(tid)}"), None, None)
        self.main.status("Stopping query…")

    # ── editor helpers ──
    def show_find(self):
        self.find.show()
        self.find.setFocus()
        self.find.selectAll()

    def _find(self, backward=False):
        text = self.find.text()
        if not text:
            return
        flags = QTextDocument.FindBackward if backward else QTextDocument.FindFlag(0)
        if not self.editor.find(text, flags):
            tc = self.editor.textCursor()
            tc.movePosition(QTextCursor.End if backward else QTextCursor.Start)
            self.editor.setTextCursor(tc)
            self.editor.find(text, flags)

    def keyPressEvent(self, e):
        if self.find.hasFocus():
            if e.key() == Qt.Key_Escape:
                self.find.hide()
                self.editor.setFocus()
                return
            if e.key() in (Qt.Key_Return, Qt.Key_Enter) and e.modifiers() & Qt.ShiftModifier:
                self._find(backward=True)
                return
        super().keyPressEvent(e)

    def beautify(self):
        tc = self.editor.textCursor()
        if tc.hasSelection():
            text = tc.selectedText().replace("\u2029", "\n")
            tc.insertText(format_script(text).rstrip("\n"))
            return
        text = self.editor.toPlainText()
        if not text.strip():
            return
        pos = tc.position()
        tc.select(QTextCursor.Document)
        tc.insertText(format_script(text))
        tc.setPosition(min(pos, len(self.editor.toPlainText())))
        self.editor.setTextCursor(tc)
        self.main.status("Formatted.")

    def uppercase_keywords(self):
        import re
        from .editor import KEYWORDS, FUNCTIONS
        words = set(KEYWORDS) | set(FUNCTIONS)
        text = self.editor.toPlainText()
        out, i = [], 0
        # only touch text outside quotes and comments
        pattern = re.compile(r"('(?:[^'\\]|\\.|'')*'|\"(?:[^\"\\]|\\.)*\"|`[^`]*`|--[^\n]*|#[^\n]*|/\*.*?\*/)", re.S)
        for m in pattern.finditer(text):
            out.append(re.sub(r"\b[A-Za-z_]+\b", lambda w: w.group(0).upper() if w.group(0).upper() in words
                              else w.group(0), text[i:m.start()]))
            out.append(m.group(0))
            i = m.end()
        out.append(re.sub(r"\b[A-Za-z_]+\b", lambda w: w.group(0).upper() if w.group(0).upper() in words
                          else w.group(0), text[i:]))
        tc = self.editor.textCursor()
        pos = tc.position()
        tc.select(QTextCursor.Document)
        tc.insertText("".join(out))
        tc.setPosition(min(pos, len(self.editor.toPlainText())))
        self.editor.setTextCursor(tc)

    def insert_text(self, text):
        self.editor.textCursor().insertText(text)
        self.editor.setFocus()

    # ── files ──
    def open_file(self, path=None):
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, "Open SQL file", "", "SQL files (*.sql);;All files (*.*)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError as exc:
            QMessageBox.critical(self, "Open file", str(exc))
            return
        if os.path.getsize(path) > 20 * 1024 * 1024:
            QMessageBox.information(self, "Large file", "This file is large. Use File → Import SQL file to run it "
                                                        "without loading it into the editor.")
            return
        self.editor.setPlainText(text)
        self.path = path
        self.editor.document().setModified(False)
        self.title_changed.emit(self)

    def save_file(self, save_as=False):
        path = self.path
        if save_as or not path:
            path, _ = QFileDialog.getSaveFileName(self, "Save SQL file", path or "query.sql", "SQL files (*.sql)")
        if not path:
            return False
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(self.editor.toPlainText())
        except OSError as exc:
            QMessageBox.critical(self, "Save file", str(exc))
            return False
        self.path = path
        self.editor.document().setModified(False)
        self.title_changed.emit(self)
        self.main.status(f"Saved {path}")
        return True

    def maybe_close(self):
        if self.in_txn and not self.running:
            box = QMessageBox(QMessageBox.Warning, "Open transaction",
                              f"This tab has an open transaction ({self.txn_rows:,} row(s) changed).", parent=self)
            b_commit = box.addButton("Commit", QMessageBox.AcceptRole)
            b_rollback = box.addButton("Rollback", QMessageBox.DestructiveRole)
            box.addButton(QMessageBox.Cancel)
            box.exec()
            if box.clickedButton() not in (b_commit, b_rollback):
                return False
            try:
                self.conn.query("COMMIT" if box.clickedButton() is b_commit else "ROLLBACK")
            except Exception as exc:  # noqa: BLE001
                QMessageBox.critical(self, "Transaction", str(exc))
                return False
            self.in_txn = False
        if self.running:
            if QMessageBox.question(self, "Query running", "A query is still running. Stop it and close?") \
                    != QMessageBox.Yes:
                return False
            self.stop()
        if self.editor.document().isModified() and self.editor.toPlainText().strip():
            res = QMessageBox.question(self, "Unsaved query", f"Save changes to “{self.title().rstrip(' •')}”?",
                                       QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
            if res == QMessageBox.Cancel:
                return False
            if res == QMessageBox.Save and not self.save_file():
                return False
        return True

    def close_connection(self):
        if self.conn:
            self.conn.close()
            self.conn = None


def _result_name(sql, i):
    m = re.search(r"\bFROM\s+`?([\w$]+)`?(?:\.`?([\w$]+)`?)?", sql, re.I)
    return (m.group(2) or m.group(1)) if m else f"result_{i}"


def _short(sql, n=90):
    s = " ".join(sql.split())
    return s if len(s) <= n else s[:n] + "…"


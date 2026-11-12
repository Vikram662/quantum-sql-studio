"""Assorted dialogs: dump/import, progress, create database, SQL preview, index/FK editors."""
import html
import os
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QGroupBox,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
                               QPlainTextEdit, QPushButton, QVBoxLayout)

from . import dump, icons
from .db import format_clock, format_duration, qi, run_async
from .editor import read_only_code_view
from .theme import T, code_font
from .ui import ProgressBar


def _buttons(dialog, ok_text="OK"):
    box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    ok = box.button(QDialogButtonBox.Ok)
    ok.setText(ok_text)
    ok.setProperty("primary", True)
    box.accepted.connect(dialog.accept)
    box.rejected.connect(dialog.reject)
    return box


class ProgressDialog(QDialog):
    """Runs fn(task) in the background with a progress bar and Cancel button.

    With log=True it also shows what fn writes with task.log(), live, and stays open
    when the work ends so the log can be read, copied or saved."""

    LOG_BATCH = 2000  # most lines added per refresh; a flood beyond that is summarised

    def __init__(self, parent, title, fn, log=False):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        if log:
            self.resize(760, 480)
        else:
            self.resize(460, 140)
        self.setWindowFlag(Qt.WindowCloseButtonHint, False)
        lay = QVBoxLayout(self)
        self.label = QLabel("Starting…")
        self.label.setWordWrap(True)  # a long error message must not stretch the dialog
        self.bar = ProgressBar()
        self.elapsed = QLabel("")
        self.elapsed.setObjectName("muted")
        lay.addWidget(self.label)
        lay.addWidget(self.bar)
        self.log_view = None
        if log:
            self.log_view = QPlainTextEdit()
            self.log_view.setReadOnly(True)
            self.log_view.setFont(code_font(9))
            self.log_view.setLineWrapMode(QPlainTextEdit.NoWrap)
            self.log_view.setMaximumBlockCount(20_000)
            self.log_view.setMinimumHeight(220)
            lay.addWidget(self.log_view, 1)
        row = QHBoxLayout()
        row.addWidget(self.elapsed)
        row.addStretch(1)
        if log:
            copy_btn = QPushButton("Copy log")
            copy_btn.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.log_view.toPlainText()))
            save_btn = QPushButton("Save log…")
            save_btn.clicked.connect(self._save_log)
            for b in (copy_btn, save_btn):  # Enter must not copy or save; it belongs to Cancel / Close
                b.setAutoDefault(False)
                row.addWidget(b)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self._cancel_or_close)
        row.addWidget(self.cancel_btn)
        lay.addLayout(row)
        self.result = None
        self.error = None
        self._finished = False
        self._t0 = time.monotonic()
        self.task = None
        # Ticks on its own: progress events can be minutes apart while one huge statement runs.
        self._clock = QTimer(self)
        self._clock.timeout.connect(self._tick)
        self._clock.start(250 if log else 500)
        self._tick()
        self.task = run_async(fn, self._done, self._failed, self._progress)

    def _tick(self):
        self.elapsed.setText(format_duration(time.monotonic() - self._t0))
        self._drain_log()

    # ── log ──
    def _drain_log(self):
        if self.log_view is None or self.task is None:
            return
        q = self.task.log_lines
        n = len(q)
        if not n:
            return
        skipped = max(0, n - self.LOG_BATCH)
        for _ in range(skipped):
            q.popleft()
        lines = [q.popleft() for _ in range(n - skipped)]
        sb = self.log_view.verticalScrollBar()
        at_bottom = sb.value() >= sb.maximum() - 4
        colors = {"ok": T["success"], "warn": T["warning"], "error": T["danger"]}
        if skipped:
            self._append(f"… {skipped:,} lines skipped to keep up …", T["muted"])
        for when, level, text in lines:
            stamp = format_clock(when - self._t0)
            self._append(f"[{stamp}] {text}", colors.get(level))
        if at_bottom:
            sb.setValue(sb.maximum())

    def _append(self, text, color=None):
        if color:
            self.log_view.appendHtml(f'<span style="color:{color}">{html.escape(text)}</span>')
        else:
            self.log_view.appendPlainText(text)

    def _save_log(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save log", os.path.join(
            os.path.expanduser("~"), "Documents", f"import_{time.strftime('%Y%m%d_%H%M')}.log"),
            "Log file (*.log *.txt)")
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.log_view.toPlainText())

    # ── lifecycle ──
    def _progress(self, pct, msg):
        self.bar.setValue(pct)
        if msg:
            self.label.setText(msg)

    def _cancel(self):
        self.task.cancelled = True
        self.cancel_btn.setEnabled(False)
        self.label.setText("Cancelling…")

    def _cancel_or_close(self):
        if self._finished:
            self.done(QDialog.Accepted if self.error is None else QDialog.Rejected)
        else:
            self._cancel()

    def _finish(self, label):
        """Log mode: stop the clock and leave the dialog open with a Close button."""
        self._finished = True
        self._clock.stop()
        self.bar.set_busy(False)
        self._tick()
        self._drain_log()
        self.label.setText(label)
        self.cancel_btn.setText("Close")
        self.cancel_btn.setEnabled(True)
        self.cancel_btn.setProperty("primary", True)
        self.cancel_btn.style().polish(self.cancel_btn)
        self.cancel_btn.setFocus()

    def _done(self, result):
        self.result = result
        if self.log_view is None:
            self.accept()
            return
        self.bar.setValue(100)
        self._finish(self.label.text())

    def _failed(self, msg):
        self.error = "Cancelled by user." if self.task.cancelled else msg
        if self.log_view is None:
            self.reject()
            return
        self.task.log(self.error, "warn" if self.task.cancelled else "error")
        self._finish("Cancelled." if self.task.cancelled else f"Failed: {msg}")

    def reject(self):
        if self.log_view is not None and self._finished:
            self._cancel_or_close()
            return
        if self.result is None and self.error is None:  # Esc pressed while running
            self._cancel()
            return
        super().reject()


def run_with_progress(parent, title, fn, log=False):
    """Returns (result, error)."""
    dlg = ProgressDialog(parent, title, fn, log)
    dlg.exec()
    return dlg.result, dlg.error


class ExportDialog(QDialog):
    def __init__(self, parent, session, db, tables_all, preselect=None):
        super().__init__(parent)
        self.setWindowTitle(f"Export database — {db}")
        self.resize(560, 600)
        self.session, self.db = session, db
        lay = QVBoxLayout(self)

        grp = QGroupBox("Tables")
        gl = QVBoxLayout(grp)
        self.list = QListWidget()
        for t in tables_all:
            it = QListWidgetItem(icons.icon("table"), t)
            it.setCheckState(Qt.Checked if not preselect or t in preselect else Qt.Unchecked)
            self.list.addItem(it)
        gl.addWidget(self.list)
        sel = QHBoxLayout()
        for text, state in (("Select all", Qt.Checked), ("Select none", Qt.Unchecked)):
            b = QPushButton(text)
            b.clicked.connect(lambda _=False, s=state: [self.list.item(i).setCheckState(s)
                                                        for i in range(self.list.count())])
            sel.addWidget(b)
        sel.addStretch(1)
        gl.addLayout(sel)
        lay.addWidget(grp, 1)

        opt = QGroupBox("Options")
        ol = QVBoxLayout(opt)
        self.c_create_db = QCheckBox("Include CREATE DATABASE / USE")
        self.c_drop = QCheckBox("Add DROP TABLE IF EXISTS")
        self.c_drop.setChecked(True)
        self.c_structure = QCheckBox("Table structure")
        self.c_structure.setChecked(True)
        self.c_data = QCheckBox("Table data")
        self.c_data.setChecked(True)
        self.c_views = QCheckBox("Views")
        self.c_views.setChecked(True)
        self.c_routines = QCheckBox("Stored procedures and functions")
        self.c_routines.setChecked(True)
        self.c_triggers = QCheckBox("Triggers")
        self.c_triggers.setChecked(True)
        for c in (self.c_create_db, self.c_drop, self.c_structure, self.c_data, self.c_views, self.c_routines,
                  self.c_triggers):
            ol.addWidget(c)
        lay.addWidget(opt)

        out = QGroupBox("Output file")
        fl = QHBoxLayout(out)
        self.path = QLineEdit(os.path.join(os.path.expanduser("~"), "Documents",
                                           f"{db}_{time.strftime('%Y%m%d_%H%M')}.sql"))
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        fl.addWidget(self.path, 1)
        fl.addWidget(browse)
        lay.addWidget(out)
        hint = QLabel("Tip: use a .sql.gz or .zip extension for a compressed dump.")
        hint.setObjectName("muted")
        lay.addWidget(hint)
        lay.addWidget(_buttons(self, "Export"))

    def _browse(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save dump", self.path.text(),
                                              "SQL file (*.sql);;Gzip compressed (*.sql.gz);;ZIP archive (*.zip)")
        if path:
            self.path.setText(path)

    def tables(self):
        return [self.list.item(i).text() for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.Checked]

    def options(self):
        return {"create_db": self.c_create_db.isChecked(), "drop": self.c_drop.isChecked(),
                "structure": self.c_structure.isChecked(), "data": self.c_data.isChecked(),
                "views": self.c_views.isChecked(), "routines": self.c_routines.isChecked(),
                "triggers": self.c_triggers.isChecked()}

    def accept(self):
        if not self.path.text().strip():
            return
        tables = self.tables()
        if not tables and not (self.c_views.isChecked() or self.c_routines.isChecked()):
            QMessageBox.warning(self, "Export", "Select at least one table.")
            return
        conn = self.session.conn.clone()
        path, opts = self.path.text().strip(), self.options()
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        result, err = run_with_progress(
            self, "Exporting…", lambda task: dump.dump_database(task, conn, self.db, path, tables, opts))
        if err:
            QMessageBox.critical(self, "Export failed", err)
            return
        size = os.path.getsize(path) if os.path.exists(path) else 0
        QMessageBox.information(self, "Export finished",
                                f"Exported {result['tables']} tables and {result['views']} views.\n\n"
                                f"{path}\n{size / 1024 / 1024:.2f} MiB")
        super().accept()


def import_file(parent, session, database=None):
    """Pick an SQL file and execute it. Returns True when something ran."""
    path, _ = QFileDialog.getOpenFileName(parent, "Import SQL file", "",
                                          "SQL files (*.sql *.sql.gz *.gz *.zip);;All files (*.*)")
    if not path:
        return False
    size = os.path.getsize(path) / 1024 / 1024
    box = QMessageBox(parent)
    box.setWindowTitle("Import SQL file")
    box.setIcon(QMessageBox.Question)
    box.setText(f"Run all statements from\n{os.path.basename(path)} ({size:.2f} MiB)\n\n"
                f"on {session.name}{' → database ' + database if database else ''}?")
    stop = QCheckBox("Stop on first error")
    stop.setChecked(True)
    box.setCheckBox(stop)
    box.setStandardButtons(QMessageBox.Yes | QMessageBox.Cancel)
    if box.exec() != QMessageBox.Yes:
        return False
    conn = session.conn.clone()
    # The dialog shows the live log and stays open at the end with the summary, so no
    # separate message box is needed for success, errors or cancel.
    run_with_progress(parent, f"Importing {os.path.basename(path)}",
                      lambda task: dump.import_sql(task, conn, path, database, stop.isChecked()), log=True)
    return True


class CreateDatabaseDialog(QDialog):
    def __init__(self, parent, collations, default="utf8mb4_unicode_ci"):
        super().__init__(parent)
        self.setWindowTitle("Create database")
        self.resize(420, 150)
        form = QFormLayout(self)
        self.name = QLineEdit()
        self.coll = QComboBox()
        self.coll.setEditable(True)
        self.coll.addItems(collations)
        if default in collations:
            self.coll.setCurrentText(default)
        form.addRow("Name", self.name)
        form.addRow("Collation", self.coll)
        form.addRow(_buttons(self, "Create"))

    def sql(self):
        coll = self.coll.currentText().strip()
        charset = coll.split("_")[0] if coll else ""
        extra = f" CHARACTER SET {charset} COLLATE {coll}" if coll else ""
        return f"CREATE DATABASE {qi(self.name.text().strip())}{extra}"


class SqlPreviewDialog(QDialog):
    """Show generated SQL and let the user confirm it."""

    def __init__(self, parent, title, sql, ok_text="Execute"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(720, 420)
        lay = QVBoxLayout(self)
        lbl = QLabel("The following SQL will be executed:")
        lbl.setObjectName("muted")
        lay.addWidget(lbl)
        view = read_only_code_view()
        view.setPlainText(sql)
        view.setStyleSheet(view.styleSheet() + f"QPlainTextEdit {{ border: 1px solid {T['border']}; }}")
        lay.addWidget(view, 1)
        lay.addWidget(_buttons(self, ok_text))


def confirm_sql(parent, title, sql, ok_text="Execute"):
    return SqlPreviewDialog(parent, title, sql, ok_text).exec() == QDialog.Accepted


class IndexDialog(QDialog):
    def __init__(self, parent, columns):
        super().__init__(parent)
        self.setWindowTitle("Add index")
        self.resize(420, 420)
        form = QFormLayout(self)
        self.name = QLineEdit()
        self.name.setPlaceholderText("auto")
        self.kind = QComboBox()
        self.kind.addItems(["INDEX", "UNIQUE", "FULLTEXT", "PRIMARY"])
        self.cols = QListWidget()
        for c in columns:
            it = QListWidgetItem(icons.icon("column"), c)
            it.setCheckState(Qt.Unchecked)
            self.cols.addItem(it)
        form.addRow("Name", self.name)
        form.addRow("Type", self.kind)
        form.addRow("Columns", self.cols)
        form.addRow(_buttons(self, "Add"))

    def clause(self):
        cols = [self.cols.item(i).text() for i in range(self.cols.count())
                if self.cols.item(i).checkState() == Qt.Checked]
        if not cols:
            return None
        col_sql = ", ".join(qi(c) for c in cols)
        kind = self.kind.currentText()
        if kind == "PRIMARY":
            return f"ADD PRIMARY KEY ({col_sql})"
        name = self.name.text().strip() or ("idx_" + "_".join(cols))[:64]
        prefix = {"INDEX": "INDEX", "UNIQUE": "UNIQUE INDEX", "FULLTEXT": "FULLTEXT INDEX"}[kind]
        return f"ADD {prefix} {qi(name)} ({col_sql})"


class ForeignKeyDialog(QDialog):
    RULES = ["RESTRICT", "CASCADE", "SET NULL", "NO ACTION"]

    def __init__(self, parent, table, columns, tables, load_columns):
        super().__init__(parent)
        self.setWindowTitle("Add foreign key")
        self.resize(440, 260)
        self.load_columns = load_columns
        form = QFormLayout(self)
        self.name = QLineEdit(f"fk_{table}_")
        self.col = QComboBox()
        self.col.addItems(columns)
        self.ref_table = QComboBox()
        self.ref_table.addItems(tables)
        self.ref_col = QComboBox()
        self.on_delete = QComboBox()
        self.on_delete.addItems(self.RULES)
        self.on_update = QComboBox()
        self.on_update.addItems(self.RULES)
        self.ref_table.currentTextChanged.connect(self._reload_cols)
        form.addRow("Name", self.name)
        form.addRow("Column", self.col)
        form.addRow("References table", self.ref_table)
        form.addRow("References column", self.ref_col)
        form.addRow("On delete", self.on_delete)
        form.addRow("On update", self.on_update)
        form.addRow(_buttons(self, "Add"))
        self._reload_cols(self.ref_table.currentText())

    def _reload_cols(self, table):
        self.ref_col.clear()
        if table:
            self.ref_col.addItems(self.load_columns(table))

    def clause(self):
        name = self.name.text().strip()
        cons = f"CONSTRAINT {qi(name)} " if name else ""
        return (f"ADD {cons}FOREIGN KEY ({qi(self.col.currentText())}) REFERENCES "
                f"{qi(self.ref_table.currentText())} ({qi(self.ref_col.currentText())}) "
                f"ON DELETE {self.on_delete.currentText()} ON UPDATE {self.on_update.currentText()}")


class UserDialog(QDialog):
    def __init__(self, parent, databases, title="Add user", user="", host="%"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(420, 230)
        form = QFormLayout(self)
        self.user = QLineEdit(user)
        self.host = QLineEdit(host)
        self.host.setPlaceholderText("% = any host, localhost")
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        self.scope = QComboBox()
        self.scope.addItem("No privileges", None)
        self.scope.addItem("All privileges on all databases (*.*)", "*")
        for d in databases:
            self.scope.addItem(f"All privileges on {d}.*", d)
        form.addRow("User name", self.user)
        form.addRow("From host", self.host)
        form.addRow("Password", self.password)
        if not user:
            form.addRow("Grant", self.scope)
        form.addRow(_buttons(self, "Save"))

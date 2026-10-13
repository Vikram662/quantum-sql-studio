"""Assorted dialogs: dump/import, progress, create database, SQL preview, index/FK editors."""
import os
import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QGroupBox,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
                               QProgressBar, QPushButton, QVBoxLayout)

from . import dump, icons
from .db import qi, run_async
from .editor import read_only_code_view
from .theme import T


def _buttons(dialog, ok_text="OK"):
    box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    ok = box.button(QDialogButtonBox.Ok)
    ok.setText(ok_text)
    ok.setProperty("primary", True)
    box.accepted.connect(dialog.accept)
    box.rejected.connect(dialog.reject)
    return box


class ProgressDialog(QDialog):
    """Runs fn(task) in the background with a progress bar and Cancel button."""

    def __init__(self, parent, title, fn):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.resize(460, 140)
        self.setWindowFlag(Qt.WindowCloseButtonHint, False)
        lay = QVBoxLayout(self)
        self.label = QLabel("Starting…")
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.elapsed = QLabel("")
        self.elapsed.setObjectName("muted")
        lay.addWidget(self.label)
        lay.addWidget(self.bar)
        row = QHBoxLayout()
        row.addWidget(self.elapsed)
        row.addStretch(1)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self._cancel)
        row.addWidget(self.cancel_btn)
        lay.addLayout(row)
        self.result = None
        self.error = None
        self._t0 = time.monotonic()
        self.task = run_async(fn, self._done, self._failed, self._progress)

    def _progress(self, pct, msg):
        self.bar.setValue(pct)
        if msg:
            self.label.setText(msg)
        self.elapsed.setText(f"{time.monotonic() - self._t0:.1f} s")

    def _cancel(self):
        self.task.cancelled = True
        self.cancel_btn.setEnabled(False)
        self.label.setText("Cancelling…")

    def _done(self, result):
        self.result = result
        self.accept()

    def _failed(self, msg):
        self.error = "Cancelled by user." if self.task.cancelled else msg
        self.reject()

    def reject(self):
        if self.result is None and self.error is None:  # Esc pressed while running
            self._cancel()
            return
        super().reject()


def run_with_progress(parent, title, fn):
    """Returns (result, error)."""
    dlg = ProgressDialog(parent, title, fn)
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
    result, err = run_with_progress(
        parent, "Importing…", lambda task: dump.import_sql(task, conn, path, database, stop.isChecked()))
    if err:
        QMessageBox.critical(parent, "Import failed", err)
        return True
    msg = f"{result['ok']:,} of {result['total']:,} statements executed in {result['seconds']:.1f} s."
    if result["errors"]:
        details = "\n".join(result["errors"][:20])
        QMessageBox.warning(parent, "Import finished with errors", f"{msg}\n\n{details}")
    else:
        QMessageBox.information(parent, "Import finished", msg)
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

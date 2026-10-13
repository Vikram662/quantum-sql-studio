"""HeidiSQL-style session manager."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (QComboBox, QDialog, QFileDialog, QFormLayout, QFrame, QGroupBox, QHBoxLayout, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QSpinBox,
                               QToolButton, QVBoxLayout, QWidget)

from . import APP_NAME, APP_VERSION, icons
from .config import SessionStore, new_session
from .db import DbConnection, run_async
from .theme import T

ENVIRONMENTS = [("Development", "development", ""), ("Staging", "staging", "#e0a84a"),
                ("Production — confirm every write", "production", "#e5534b")]
COLORS = [("None", ""), ("Red", "#e5534b"), ("Orange", "#e0a84a"), ("Green", "#5fb865"),
          ("Blue", "#3574f0"), ("Purple", "#a77de8"), ("Teal", "#2aacb8")]


class SessionDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{APP_NAME} — Session manager")
        self.resize(900, 720)
        self.store = SessionStore()
        self.sessions = self.store.load()
        self.connection = None
        self.session = None
        self._loading = False

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # left: session list
        left = QWidget()
        left.setObjectName("sidebar")
        left.setFixedWidth(270)
        lv = QVBoxLayout(left)
        lv.setContentsMargins(12, 14, 12, 12)
        brand = QLabel(f"⚡ {APP_NAME}")
        brand.setObjectName("h1")
        lv.addWidget(brand)
        ver = QLabel(f"v{APP_VERSION} · MySQL / MariaDB client")
        ver.setObjectName("muted")
        lv.addWidget(ver)
        lv.addSpacing(12)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter sessions…")
        self.filter.addAction(icons.icon("search"), QLineEdit.LeadingPosition)
        self.filter.textChanged.connect(self._apply_filter)
        lv.addWidget(self.filter)
        self.list = QListWidget()
        self.list.setObjectName("sessions")
        self.list.setStyleSheet("QListWidget#sessions { border: none; background: transparent; }")
        self.list.currentRowChanged.connect(self._on_select)
        self.list.itemDoubleClicked.connect(lambda *_: self._open())
        lv.addWidget(self.list, 1)
        row = QHBoxLayout()
        for name, tip, slot in (("plus", "New session", self._new), ("copy", "Duplicate", self._duplicate),
                                ("trash", "Delete", self._delete)):
            b = QToolButton()
            b.setIcon(icons.icon(name))
            b.setToolTip(tip)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch(1)
        lv.addLayout(row)
        root.addWidget(left)

        # right: form
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(28, 22, 28, 18)
        self.title = QLabel("Connection")
        self.title.setObjectName("h1")
        rv.addWidget(self.title)
        sub = QLabel("MySQL / MariaDB over TCP/IP")
        sub.setObjectName("muted")
        rv.addWidget(sub)
        rv.addSpacing(14)

        card = QFrame()
        card.setObjectName("card")
        form = QFormLayout(card)
        form.setContentsMargins(18, 18, 18, 18)
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(10)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.f_name = QLineEdit()
        self.f_host = QLineEdit()
        self.f_port = QSpinBox()
        self.f_port.setRange(1, 65535)
        self.f_user = QLineEdit()
        self.f_pass = QLineEdit()
        self.f_pass.setEchoMode(QLineEdit.Password)
        eye = QAction(icons.icon("view"), "Show password", self.f_pass)
        eye.triggered.connect(lambda: self.f_pass.setEchoMode(
            QLineEdit.Normal if self.f_pass.echoMode() == QLineEdit.Password else QLineEdit.Password))
        self.f_pass.addAction(eye, QLineEdit.TrailingPosition)
        self.f_db = QLineEdit()
        self.f_db.setPlaceholderText("optional — leave empty to see all databases")
        self.f_color = QComboBox()
        for label, color in COLORS:
            self.f_color.addItem(icons.icon("dot", color or T["faint"]), label, color)
        self.f_env = QComboBox()
        for label, key, col in ENVIRONMENTS:
            self.f_env.addItem(icons.icon("dot", col or T["success"]), label, key)
        self.f_env.setToolTip("Production sessions ask before every write, run queries in a reviewable "
                              "transaction and are marked red")
        self.f_timeout = QSpinBox()
        self.f_timeout.setRange(1, 300)
        self.f_timeout.setSuffix(" s")
        hp = QHBoxLayout()
        self.f_port.setFixedWidth(90)
        hp.addWidget(self.f_host, 1)
        hp.addSpacing(10)
        hp.addWidget(QLabel("Port"))
        hp.addWidget(self.f_port)
        form.addRow("Session name", self.f_name)
        form.addRow("Hostname / IP", hp)
        form.addRow("User", self.f_user)
        form.addRow("Password", self.f_pass)
        form.addRow("Database", self.f_db)
        form.addRow("Environment", self.f_env)
        form.addRow("Color", self.f_color)
        form.addRow("Connect timeout", self.f_timeout)
        for w in (self.f_name, self.f_host, self.f_user, self.f_pass, self.f_db):
            w.textChanged.connect(self._sync)
        self.f_port.valueChanged.connect(self._sync)
        self.f_timeout.valueChanged.connect(self._sync)
        self.f_color.currentIndexChanged.connect(self._sync)
        self.f_env.currentIndexChanged.connect(self._sync)
        rv.addWidget(card)

        self.ssh = QGroupBox("Connect through an SSH tunnel")
        self.ssh.setCheckable(True)
        self.ssh.setChecked(False)
        self.ssh.setToolTip("Reach a MySQL server that only listens on the SSH host (no public MySQL port). "
                            "“Hostname / IP” above is then the MySQL host as seen from the SSH server — "
                            "usually 127.0.0.1.")
        sf = QFormLayout(self.ssh)
        sf.setHorizontalSpacing(16)
        sf.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.f_ssh_host = QLineEdit()
        self.f_ssh_host.setPlaceholderText("server.example.com")
        self.f_ssh_port = QSpinBox()
        self.f_ssh_port.setRange(1, 65535)
        self.f_ssh_port.setFixedWidth(90)
        sh = QHBoxLayout()
        sh.addWidget(self.f_ssh_host, 1)
        sh.addSpacing(10)
        sh.addWidget(QLabel("Port"))
        sh.addWidget(self.f_ssh_port)
        self.f_ssh_user = QLineEdit()
        self.f_ssh_pass = QLineEdit()
        self.f_ssh_pass.setEchoMode(QLineEdit.Password)
        self.f_ssh_pass.setPlaceholderText("leave empty when using a private key")
        self.f_ssh_key = QLineEdit()
        self.f_ssh_key.setPlaceholderText("optional — path to id_rsa / id_ed25519 / .pem")
        browse = QAction(icons.icon("open"), "Browse…", self.f_ssh_key)
        browse.triggered.connect(self._browse_key)
        self.f_ssh_key.addAction(browse, QLineEdit.TrailingPosition)
        self.f_ssh_key_pass = QLineEdit()
        self.f_ssh_key_pass.setEchoMode(QLineEdit.Password)
        self.f_ssh_key_pass.setPlaceholderText("only if the key is encrypted")
        sf.addRow("SSH host", sh)
        sf.addRow("SSH user", self.f_ssh_user)
        sf.addRow("SSH password", self.f_ssh_pass)
        sf.addRow("Private key", self.f_ssh_key)
        sf.addRow("Key passphrase", self.f_ssh_key_pass)
        for w in (self.f_ssh_host, self.f_ssh_user, self.f_ssh_pass, self.f_ssh_key, self.f_ssh_key_pass):
            w.textChanged.connect(self._sync)
        self.f_ssh_port.valueChanged.connect(self._sync)
        self.ssh.toggled.connect(self._sync)
        rv.addSpacing(10)
        rv.addWidget(self.ssh)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        rv.addSpacing(8)
        rv.addWidget(self.status)
        rv.addStretch(1)

        btns = QHBoxLayout()
        self.b_test = QPushButton(icons.icon("activity"), "Test connection")
        self.b_test.clicked.connect(self._test)
        b_save = QPushButton(icons.icon("save"), "Save")
        b_save.clicked.connect(self._save)
        b_cancel = QPushButton("Cancel")
        b_cancel.clicked.connect(self.reject)
        self.b_open = QPushButton("Open")
        self.b_open.setProperty("primary", True)
        self.b_open.setDefault(True)
        self.b_open.clicked.connect(self._open)
        btns.addWidget(self.b_test)
        btns.addWidget(b_save)
        btns.addStretch(1)
        btns.addWidget(b_cancel)
        btns.addWidget(self.b_open)
        rv.addLayout(btns)
        root.addWidget(right, 1)

        self._reload_list(0)

    # ── list ──
    def _reload_list(self, select=None):
        self.list.blockSignals(True)
        self.list.clear()
        for s in self.sessions:
            it = QListWidgetItem(icons.icon("server", s.get("color") or None), s["name"])
            it.setToolTip(f"{s['user']}@{s['host']}:{s['port']}")
            self.list.addItem(it)
        self.list.blockSignals(False)
        if self.sessions:
            self.list.setCurrentRow(min(select or 0, len(self.sessions) - 1))
        self._on_select(self.list.currentRow())

    def _apply_filter(self, text):
        for i in range(self.list.count()):
            it = self.list.item(i)
            it.setHidden(text.lower() not in it.text().lower())

    def _current(self):
        r = self.list.currentRow()
        return self.sessions[r] if 0 <= r < len(self.sessions) else None

    def _on_select(self, row):
        s = self._current()
        enabled = s is not None
        for w in (self.f_name, self.f_host, self.f_port, self.f_user, self.f_pass, self.f_db, self.f_color,
                  self.f_env, self.f_timeout, self.b_open, self.b_test, self.ssh):
            w.setEnabled(enabled)
        if not s:
            self.title.setText("No session")
            return
        self._loading = True
        self.f_name.setText(s["name"])
        self.f_host.setText(s["host"])
        self.f_port.setValue(int(s.get("port") or 3306))
        self.f_user.setText(s["user"])
        self.f_pass.setText(s.get("password", ""))
        self.f_db.setText(s.get("database", ""))
        self.f_color.setCurrentIndex(max(0, self.f_color.findData(s.get("color", ""))))
        self.f_timeout.setValue(int(s.get("timeout") or 10))
        self.f_env.setCurrentIndex(max(0, self.f_env.findData(s.get("env") or "development")))
        self.ssh.setChecked(bool(s.get("ssh_enabled")))
        self.f_ssh_host.setText(s.get("ssh_host", ""))
        self.f_ssh_port.setValue(int(s.get("ssh_port") or 22))
        self.f_ssh_user.setText(s.get("ssh_user", ""))
        self.f_ssh_pass.setText(s.get("ssh_password", ""))
        self.f_ssh_key.setText(s.get("ssh_key_file", ""))
        self.f_ssh_key_pass.setText(s.get("ssh_key_pass", ""))
        self._loading = False
        self.title.setText(s["name"])
        self.status.setText("")

    def _sync(self, *_):
        s = self._current()
        if self._loading or not s:
            return
        s.update(name=self.f_name.text().strip() or "Unnamed", host=self.f_host.text().strip(),
                 port=self.f_port.value(), user=self.f_user.text().strip(), password=self.f_pass.text(),
                 database=self.f_db.text().strip(), color=self.f_color.currentData() or "",
                 timeout=self.f_timeout.value(), env=self.f_env.currentData() or "development",
                 ssh_enabled=self.ssh.isChecked(), ssh_host=self.f_ssh_host.text().strip(),
                 ssh_port=self.f_ssh_port.value(), ssh_user=self.f_ssh_user.text().strip(),
                 ssh_password=self.f_ssh_pass.text(), ssh_key_file=self.f_ssh_key.text().strip(),
                 ssh_key_pass=self.f_ssh_key_pass.text())
        it = self.list.currentItem()
        it.setText(s["name"])
        it.setIcon(icons.icon("server", s["color"] or None))
        self.title.setText(s["name"])

    def _browse_key(self):
        path, _ = QFileDialog.getOpenFileName(self, "Private key", "", "Keys (*.pem *.key id_* *);;All files (*.*)")
        if path:
            self.f_ssh_key.setText(path)

    def _save(self, quiet=False):
        try:
            self.store.save(self.sessions)
            if not quiet:
                self._status("Sessions saved.", "success")
        except OSError as exc:
            self._status(f"Could not save sessions: {exc}", "danger")

    def _new(self):
        self.sessions.append(new_session(name=f"Session {len(self.sessions) + 1}"))
        self._reload_list(len(self.sessions) - 1)
        self.f_name.setFocus()
        self.f_name.selectAll()
        self._save(quiet=True)

    def _duplicate(self):
        s = self._current()
        if s:
            copy = new_session(**{k: v for k, v in s.items() if k != "id"})
            copy["name"] = s["name"] + " (copy)"
            self.sessions.append(copy)
            self._reload_list(len(self.sessions) - 1)
            self._save(quiet=True)

    def _delete(self):
        s = self._current()
        if s and QMessageBox.question(self, "Delete session", f"Delete session “{s['name']}”?") == QMessageBox.Yes:
            row = self.list.currentRow()
            self.sessions.remove(s)
            self._reload_list(max(0, row - 1))
            self._save(quiet=True)

    # ── connect ──
    def _status(self, text, kind="muted"):
        self.status.setText(text)
        self.status.setStyleSheet(f"color: {T.get(kind, T['muted'])};")

    def _busy(self, busy):
        self.b_open.setEnabled(not busy)
        self.b_test.setEnabled(not busy)
        self.setCursor(Qt.WaitCursor if busy else Qt.ArrowCursor)

    def _test(self):
        s = self._current()
        if not s:
            return
        conn = DbConnection(s)
        self._status(f"Connecting to {s['host']}:{s['port']}"
                     + (f" through SSH {s.get('ssh_host')}" if s.get("ssh_enabled") else "") + " …")
        self._busy(True)

        def work():
            conn.connect()
            version = conn.server_version
            conn.close()
            return version

        run_async(work, lambda v: (self._busy(False), self._status(f"✓ Connection OK — server {v}", "success")),
                  lambda e: (self._busy(False), self._status(f"✗ {e}", "danger")))

    def _open(self):
        s = self._current()
        if not s:
            return
        self._save(quiet=True)
        conn = DbConnection(s)
        self._status(f"Connecting to {s['host']}:{s['port']} …")
        self._busy(True)

        def done(_):
            self._busy(False)
            self.connection = conn
            self.session = dict(s)
            self.accept()

        run_async(conn.connect, done, lambda e: (self._busy(False), self._status(f"✗ {e}", "danger")))

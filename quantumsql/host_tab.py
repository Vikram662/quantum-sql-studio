"""Host tab: databases, processes, variables, status and users of a server."""
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (QCheckBox, QLineEdit, QMessageBox, QPlainTextEdit, QSplitter, QTabWidget,
                               QVBoxLayout, QWidget)

from . import theme
from .db import literal, run_async
from .dialogs import UserDialog
from .grid import DataGrid, ResultModel
from .ui import bar, confirm, muted, tool


class _GridPage(QWidget):
    """A grid with an optional client-side filter box."""

    def __init__(self, toolbar_items=(), filterable=False):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.filter = None
        items = list(toolbar_items)
        if filterable:
            self.filter = QLineEdit()
            self.filter.setPlaceholderText("Filter…")
            self.filter.setFixedWidth(260)
            self.filter.textChanged.connect(self._apply_filter)
            items = items + [None, self.filter]
        if items:
            lay.addWidget(bar(*items))
        self.grid = DataGrid()
        lay.addWidget(self.grid, 1)
        self.headers, self.rows = [], []

    def set_data(self, headers, rows):
        self.headers, self.rows = headers, rows
        self._apply_filter()

    def _apply_filter(self, *_):
        text = self.filter.text().lower() if self.filter else ""
        rows = [r for r in self.rows if text in " ".join(str(v) for v in r).lower()] if text else self.rows
        self.grid.set_model(ResultModel(self.headers, rows))


class HostTab(QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        self.session = None
        self.loaded = set()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.info = muted("")
        lay.addWidget(bar(self.info, None, tool("refresh", "Refresh (F5)", self.refresh)))

        self.tabs = QTabWidget()
        self.tabs.setObjectName("subtabs")
        lay.addWidget(self.tabs, 1)

        self.p_dbs = _GridPage(filterable=True)
        self.p_dbs.grid.doubleClicked.connect(self._open_db)
        self.auto = QCheckBox("Auto refresh (3 s)")
        self.auto.toggled.connect(lambda on: self.timer.start(3000) if on else self.timer.stop())
        self.p_proc = _GridPage([tool("refresh", "Refresh", lambda: self._load("proc"), "Refresh"),
                                 tool("x", "Kill selected process", self._kill, "Kill process"), "|", self.auto],
                                filterable=True)
        self.p_vars = _GridPage(filterable=True)
        self.p_status = _GridPage(filterable=True)

        users = QWidget()
        ul = QVBoxLayout(users)
        ul.setContentsMargins(0, 0, 0, 0)
        ul.setSpacing(0)
        ul.addWidget(bar(tool("plus", "Add user", self._add_user, "Add user"),
                         tool("edit", "Change password", self._change_password, "Change password"),
                         tool("trash", "Drop user", self._drop_user, "Drop user"), None,
                         tool("refresh", "Refresh", lambda: self._load("users"))))
        split = QSplitter()
        self.users_grid = DataGrid()
        self.users_grid.clicked.connect(self._show_grants)
        self.grants = QPlainTextEdit()
        self.grants.setReadOnly(True)
        self.grants.setFont(theme.code_font(10))
        self.grants.setPlaceholderText("Select a user to see its grants")
        split.addWidget(self.users_grid)
        split.addWidget(self.grants)
        split.setSizes([400, 600])
        ul.addWidget(split, 1)

        for page, title, key in ((self.p_dbs, "Databases", "dbs"), (self.p_proc, "Processes", "proc"),
                                 (self.p_vars, "Variables", "vars"), (self.p_status, "Status", "status"),
                                 (users, "Users", "users")):
            page.setProperty("key", key)
            self.tabs.addTab(page, title)
        self.tabs.currentChanged.connect(lambda *_: self._ensure_loaded())

        self.timer = QTimer(self)
        self.timer.timeout.connect(lambda: self.isVisible() and self._load("proc"))

    def set_session(self, session):
        if session is not self.session:
            self.session = session
            self.loaded.clear()
        if self.isVisible():
            self._ensure_loaded()

    def showEvent(self, e):
        super().showEvent(e)
        self._ensure_loaded()

    def refresh(self):
        self.loaded.clear()
        self._ensure_loaded()

    def _ensure_loaded(self):
        if not self.session:
            return
        key = self.tabs.currentWidget().property("key")
        if key not in self.loaded:
            self._load(key)
        if "info" not in self.loaded:
            self.loaded.add("info")
            conn = self.session.conn
            run_async(lambda: conn.rows("SHOW GLOBAL STATUS LIKE 'Uptime'"), self._show_info)

    def _show_info(self, rows):
        p = self.session.params
        up = int(rows[0][1]) if rows else 0
        self.info.setText(f"{p['user']}@{p['host']}:{p['port']}   ·   {self.session.conn.server_version}   ·   "
                          f"uptime {up // 86400}d {up % 86400 // 3600}h {up % 3600 // 60}m")

    def _load(self, key):
        if not self.session:
            return
        self.loaded.add(key)
        conn = self.session.conn
        queries = {
            "dbs": ("SELECT s.SCHEMA_NAME AS `Database`, COUNT(t.TABLE_NAME) AS `Tables`, "
                    "ROUND(SUM(COALESCE(t.DATA_LENGTH,0)+COALESCE(t.INDEX_LENGTH,0))/1048576, 2) AS `Size (MiB)`, "
                    "s.DEFAULT_COLLATION_NAME AS `Collation` FROM information_schema.SCHEMATA s "
                    "LEFT JOIN information_schema.TABLES t ON t.TABLE_SCHEMA=s.SCHEMA_NAME "
                    "GROUP BY s.SCHEMA_NAME, s.DEFAULT_COLLATION_NAME ORDER BY s.SCHEMA_NAME", self.p_dbs),
            "proc": ("SHOW FULL PROCESSLIST", self.p_proc),
            "vars": ("SHOW GLOBAL VARIABLES", self.p_vars),
            "status": ("SHOW GLOBAL STATUS", self.p_status),
            "users": ("SELECT User, Host FROM mysql.user ORDER BY User, Host", None),
        }
        sql, page = queries[key]

        def done(res):
            headers, rows, _ = res
            if page is None:
                self.users_grid.set_model(ResultModel(headers, rows))
            else:
                page.set_data(headers, rows)

        run_async(lambda: conn.query(sql, log=key != "proc"), done,
                  lambda e: self.main.status(e, error=True))

    def _open_db(self, index):
        db = self.p_dbs.grid.model().rows[index.row()][0]
        self.main.select_object(self.session, db)

    def _kill(self):
        idx = self.p_proc.grid.currentIndex()
        if not idx.isValid():
            return
        model = self.p_proc.grid.model()
        pid = model.rows[idx.row()][0]
        if pid == self.session.conn.thread_id():
            QMessageBox.warning(self, "Kill process", "That is this application's own connection.")
            return
        if confirm(self, "Kill process", f"Kill process {pid}?", danger=True):
            conn = self.session.conn
            run_async(lambda: conn.execute(f"KILL {int(pid)}"), lambda *_: self._load("proc"),
                      lambda e: QMessageBox.critical(self, "Kill failed", e))

    # ── users ──
    def _selected_user(self):
        idx = self.users_grid.currentIndex()
        if not idx.isValid():
            return None
        row = self.users_grid.model().rows[idx.row()]
        return row[0], row[1]

    def _show_grants(self, *_):
        u = self._selected_user()
        if not u:
            return
        conn = self.session.conn
        run_async(lambda: conn.rows(f"SHOW GRANTS FOR {literal(u[0])}@{literal(u[1])}"),
                  lambda rows: self.grants.setPlainText(";\n".join(r[0] for r in rows) + ";"),
                  lambda e: self.grants.setPlainText(e))

    def _run_user_sql(self, stmts):
        conn = self.session.conn

        def work():
            for s in stmts:
                conn.execute(s)
        run_async(work, lambda *_: (self._load("users"), self.main.status("User saved.")),
                  lambda e: QMessageBox.critical(self, "User management", e))

    def _add_user(self):
        dlg = UserDialog(self, self.main.database_names(self.session))
        if not dlg.exec():
            return
        who = f"{literal(dlg.user.text().strip())}@{literal(dlg.host.text().strip() or '%')}"
        stmts = [f"CREATE USER {who} IDENTIFIED BY {literal(dlg.password.text())}"]
        scope = dlg.scope.currentData()
        if scope == "*":
            stmts.append(f"GRANT ALL PRIVILEGES ON *.* TO {who}")
        elif scope:
            stmts.append(f"GRANT ALL PRIVILEGES ON `{scope.replace('`', '``')}`.* TO {who}")
        self._run_user_sql(stmts)

    def _change_password(self):
        u = self._selected_user()
        if not u:
            return
        dlg = UserDialog(self, [], "Change password", u[0], u[1])
        dlg.user.setReadOnly(True)
        dlg.host.setReadOnly(True)
        if dlg.exec():
            self._run_user_sql([f"ALTER USER {literal(u[0])}@{literal(u[1])} IDENTIFIED BY "
                                f"{literal(dlg.password.text())}"])

    def _drop_user(self):
        u = self._selected_user()
        if u and confirm(self, "Drop user", f"Drop user '{u[0]}'@'{u[1]}'?", danger=True):
            self._run_user_sql([f"DROP USER {literal(u[0])}@{literal(u[1])}"])

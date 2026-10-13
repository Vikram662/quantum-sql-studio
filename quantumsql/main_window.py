"""Main application window: object tree, work tabs, SQL log and history."""
import base64
import functools
import sys
import time

from PySide6.QtCore import QByteArray, QProcess, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QColor, QIcon, QKeySequence, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (QApplication, QDockWidget, QInputDialog, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QTabBar, QTabWidget,
                               QToolBar, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from . import APP_NAME, APP_VERSION, icons, theme
from .config import history, resource_path, settings
from .data_tab import DataTab
from .database_tab import DatabaseTab
from .db import human_size, qi, qt, query_log, run_async
from .dialogs import CreateDatabaseDialog, ExportDialog, import_file
from .host_tab import HostTab
from .monitor_tab import MonitorTab
from .query_tab import QueryTab
from .session_dialog import SessionDialog
from .table_tab import TableTab
from .theme import T
from .ui import bar, confirm, tool


class ServerSession:
    def __init__(self, params, conn):
        self.params = params
        self.conn = conn
        self.name = params.get("name") or params.get("host")
        self.env = params.get("env") or "development"
        self.color = params.get("color") or ("#e5534b" if self.env == "production" else "")
        self.item = None
        self.objects = {}  # db -> [object dicts]


class Node(QTreeWidgetItem):
    def __init__(self, kind, session, db=None, name=None):
        super().__init__()
        self.kind, self.session, self.db, self.name = kind, session, db, name
        self.loaded = False
        self.loading = False

    def add_placeholder(self):
        ph = QTreeWidgetItem(["Loading…"])
        ph.setForeground(0, QColor(T["faint"]))
        self.addChild(ph)
        self.setChildIndicatorPolicy(QTreeWidgetItem.ShowIndicator)


T_HOST, T_DB, T_TABLE, T_DATA, T_MONITOR = range(5)
FIXED_TABS = 5


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.sessions = []
        self._query_counter = 0
        self._prev_node = None
        self.setWindowTitle(f"{APP_NAME} {APP_VERSION}")
        self.setWindowIcon(QIcon(resource_path("app_icon.ico")))
        self.resize(1440, 900)
        self.setDockNestingEnabled(True)

        self._build_tree()
        self._build_tabs()
        self._build_bottom()
        self._build_actions()
        self._build_status()
        query_log.message.connect(self._append_log)
        from .grid import DataGrid
        DataGrid.status_hook = self.sb_sel.setText
        self.tabs.currentChanged.connect(lambda *_: self._update_actions())

        geo, state = settings.get("geometry"), settings.get("window_state")
        if geo:
            self.restoreGeometry(QByteArray(base64.b64decode(geo)))
        if state:
            self.restoreState(QByteArray(base64.b64decode(state)))
        self._update_actions()

    # ══ construction ══════════════════════════════════════════════════════
    def _build_tree(self):
        dock = QDockWidget("Databases", self)
        dock.setObjectName("dock_tree")
        dock.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetFloatable)
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.tree_filter = QLineEdit()
        self.tree_filter.setPlaceholderText("Filter databases and tables…")
        self.tree_filter.addAction(icons.icon("search"), QLineEdit.LeadingPosition)
        self.tree_filter.setClearButtonEnabled(True)
        self.tree_filter.textChanged.connect(self._filter_tree)
        lay.addWidget(bar(self.tree_filter, margins=(6, 6, 6, 6)))
        self.tree = QTreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setHeaderHidden(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setIndentation(14)
        self.tree.setAnimated(True)
        self.tree.setIconSize(QSize(16, 16))
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, self.tree.header().ResizeMode.Stretch)
        self.tree.header().resizeSection(1, 72)
        self.tree.itemExpanded.connect(self._on_expand)
        self.tree.currentItemChanged.connect(self._on_select)
        self.tree.itemDoubleClicked.connect(self._on_double)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._tree_menu)
        lay.addWidget(self.tree, 1)
        dock.setWidget(w)
        dock.setMinimumWidth(230)
        self.addDockWidget(Qt.LeftDockWidgetArea, dock)
        self.tree_dock = dock

    def _build_tabs(self):
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(False)
        self.tabs.tabCloseRequested.connect(self.close_query_tab)
        self.host_tab = HostTab(self)
        self.db_tab = DatabaseTab(self)
        self.table_tab = TableTab(self)
        self.data_tab = DataTab(self)
        self.monitor_tab = MonitorTab(self)
        for w, ic, title in ((self.host_tab, "server", "Host"), (self.db_tab, "database", "Database"),
                             (self.table_tab, "table", "Table"), (self.data_tab, "view", "Data"),
                             (self.monitor_tab, "activity", "Monitor")):
            i = self.tabs.addTab(w, icons.icon(ic), title)
            self.tabs.tabBar().setTabButton(i, QTabBar.RightSide, None)
        plus = tool("plus", "New query tab (Ctrl+T)", lambda: self.new_query())
        self.tabs.setCornerWidget(plus, Qt.TopRightCorner)
        self.setCentralWidget(self.tabs)
        self._set_fixed_tabs_enabled(False)

    def _build_bottom(self):
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setFont(theme.code_font(9))
        self.log.setMaximumBlockCount(5000)
        self.log.setLineWrapMode(QPlainTextEdit.NoWrap)
        log_dock = QDockWidget("SQL log", self)
        log_dock.setObjectName("dock_log")
        log_dock.setWidget(self.log)
        self.addDockWidget(Qt.BottomDockWidgetArea, log_dock)

        hw = QWidget()
        hl = QVBoxLayout(hw)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(0)
        self.hist_filter = QLineEdit()
        self.hist_filter.setPlaceholderText("Search history…")
        self.hist_filter.setClearButtonEnabled(True)
        self.hist_filter.textChanged.connect(self.history_changed)
        hl.addWidget(bar(self.hist_filter, tool("trash", "Clear history", self._clear_history),
                         margins=(6, 4, 6, 4)))
        self.hist_list = QListWidget()
        self.hist_list.setFont(theme.code_font(9))
        self.hist_list.itemDoubleClicked.connect(self._history_insert)
        hl.addWidget(self.hist_list, 1)
        hist_dock = QDockWidget("Query history", self)
        hist_dock.setObjectName("dock_history")
        hist_dock.setWidget(hw)
        self.addDockWidget(Qt.BottomDockWidgetArea, hist_dock)
        self.tabifyDockWidget(log_dock, hist_dock)
        from .saved_queries import SavedQueriesPanel
        self.saved_panel = SavedQueriesPanel(self)
        saved_dock = QDockWidget("Saved queries", self)
        saved_dock.setObjectName("dock_saved")
        saved_dock.setWidget(self.saved_panel)
        self.addDockWidget(Qt.BottomDockWidgetArea, saved_dock)
        self.tabifyDockWidget(hist_dock, saved_dock)
        self.saved_dock = saved_dock
        self.palette_providers = [self.saved_panel.palette_entries]
        log_dock.raise_()
        self.resizeDocks([log_dock], [150], Qt.Vertical)
        self.log_dock, self.hist_dock = log_dock, hist_dock
        self.history_changed()

    def _act(self, icon_name, text, slot, shortcut=None, tip=None):
        a = QAction(icons.icon(icon_name) if icon_name else QIcon(), text, self)
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
            a.setShortcutContext(Qt.WindowShortcut)
        a.setToolTip(f"{tip or text}" + (f" ({shortcut})" if shortcut else ""))
        a.triggered.connect(lambda *_: slot())
        self.addAction(a)
        return a

    def _build_actions(self):
        A = self._act
        self.a_sessions = A("plug", "Session manager…", self.open_session_manager, "Ctrl+N")
        self.a_disconnect = A("unplug", "Disconnect", self.disconnect_current)
        self.a_new_query = A("query", "New query tab", lambda: self.new_query(), "Ctrl+T")
        self.a_open = A("open", "Open SQL file…", self.open_sql_file, "Ctrl+O")
        self.a_save = A("save", "Save", self.save_current, "Ctrl+S")
        self.a_save_as = A(None, "Save SQL file as…", lambda: self._query_do(lambda q: q.save_file(True)),
                           "Ctrl+Shift+S")
        self.a_close_tab = A(None, "Close query tab", lambda: self.close_query_tab(self.tabs.currentIndex()), "Ctrl+W")
        self.a_run = A("play", "Execute SQL", lambda: self._query_do(lambda q: q.run_all()), "F9")
        self.a_run_cur = A("play-line", "Execute current statement",
                           lambda: self._query_do(lambda q: q.run_current()), "Ctrl+Return")
        self.a_explain = A("chart", "Explain current statement", lambda: self._query_do(lambda q: q.explain()))
        self.a_stop = A("stop", "Stop", lambda: self._query_do(lambda q: q.stop()))
        self.a_find = A("search", "Find in query…", lambda: self._query_do(lambda q: q.show_find()), "Ctrl+F")
        self.a_format = A("format", "Format SQL", lambda: self._query_do(lambda q: q.beautify()), "Ctrl+Shift+F")
        self.a_snippets = A("code", "Snippets…", self.edit_snippets)
        self.a_save_lib = A("star", "Save query to library…", self.save_query_to_library, "Ctrl+D",
                            "Save this query (Saved queries panel)")
        self.a_search = A("search", "Search in database…", self.global_search, "Ctrl+Alt+F",
                          "Find a value in every table of a database")
        self.a_diff = A("diff", "Compare schemas…", self.schema_diff, None,
                        "Compare two databases and generate a sync script")
        self.a_er = A("diagram", "ER diagram…", self.er_diagram, None, "Tables and foreign keys as a diagram")
        self.a_ai = A("sparkle", "AI assistant…", lambda: self._query_do(lambda q: q.ask_ai()), "Ctrl+I",
                      "Write, explain, fix or optimise SQL with Claude")
        self.a_ai_settings = A(None, "AI assistant settings…", self.ai_settings)
        self.a_refresh = A("refresh", "Refresh", self.refresh_current, "F5")
        self.a_import = A("import", "Import SQL file…", self.import_sql)
        self.a_export = A("export", "Export database as SQL…", self.export_db)
        self.a_create_db = A("database", "Create database…", lambda: self.create_database())
        self.a_create_table = A("table", "Create table…", lambda: self.create_table())
        self.a_monitor = A("activity", "Live monitor", self.open_monitor, "Ctrl+Shift+M",
                           "Live monitor — load, top SQL, connections per app, health")
        self.a_processes = A("server", "Server processes", lambda: self._host_page(1))
        self.a_users = A("users", "User manager", lambda: self._host_page(4))
        self.a_variables = A("settings", "Server variables", lambda: self._host_page(2))
        self.a_palette = A("command", "Command palette…", self.open_palette, "Ctrl+Shift+P",
                           "Search every command, database and table")
        self.a_quit = A(None, "Exit", self.close, "Alt+F4")

        mb = self.menuBar()
        m = mb.addMenu("&File")
        for a in (self.a_sessions, self.a_disconnect, None, self.a_new_query, self.a_open, self.a_save,
                  self.a_save_as, self.a_close_tab, None, self.a_import, self.a_export, None, self.a_quit):
            m.addSeparator() if a is None else m.addAction(a)
        m = mb.addMenu("&Query")
        for a in (self.a_run, self.a_run_cur, self.a_explain, self.a_stop, None, self.a_find, self.a_format,
                  self.a_snippets, None, self.a_save_lib, None, self.a_ai):
            m.addSeparator() if a is None else m.addAction(a)
        m = mb.addMenu("&Database")
        for a in (self.a_create_db, self.a_create_table, None, self.a_refresh):
            m.addSeparator() if a is None else m.addAction(a)
        m = mb.addMenu("&Tools")
        for a in (self.a_monitor, self.a_search, self.a_diff, self.a_er, self.a_ai_settings, None, self.a_processes, self.a_variables,
                  self.a_users):
            m.addSeparator() if a is None else m.addAction(a)
        m = mb.addMenu("&View")
        m.addAction(self.a_palette)
        m.addSeparator()
        group = QActionGroup(self)
        current = settings.get("theme", "dark")
        for key, label in (("dark", "Dark theme"), ("light", "Light theme")):
            a = QAction(label, self, checkable=True)
            a.setChecked(key == current)
            a.triggered.connect(lambda _=False, k=key: self.set_theme(k))
            group.addAction(a)
            m.addAction(a)
        m.addSeparator()
        for d in (self.tree_dock, self.log_dock, self.hist_dock, self.saved_dock):
            m.addAction(d.toggleViewAction())
        m = mb.addMenu("&Help")
        m.addAction(icons.icon("info"), "User guide", self.show_guide, QKeySequence("F1"))
        m.addAction("Keyboard shortcuts", self.show_shortcuts)
        m.addAction(f"About {APP_NAME}", self.show_about)

        tb = QToolBar("Main toolbar")
        tb.setObjectName("toolbar_main")
        tb.setMovable(False)
        tb.setIconSize(QSize(18, 18))
        tb.setToolButtonStyle(Qt.ToolButtonIconOnly)
        for a in (self.a_sessions, self.a_disconnect, None, self.a_refresh, None, self.a_new_query, self.a_open,
                  self.a_save, None, self.a_run, self.a_run_cur, self.a_explain, self.a_stop, None,
                  self.a_create_db, self.a_create_table, None, self.a_import, self.a_export, None,
                  self.a_monitor, self.a_processes, self.a_users):
            tb.addSeparator() if a is None else tb.addAction(a)
        self.addToolBar(tb)

    def _build_status(self):
        sb = self.statusBar()
        self.sb_msg = QLabel("Ready")
        self.sb_conn = QLabel("Not connected")
        self.sb_time = QLabel("")
        self.sb_sel = QLabel("")
        sb.addWidget(self.sb_msg, 1)
        sb.addPermanentWidget(self.sb_sel)
        sb.addPermanentWidget(self.sb_time)
        sb.addPermanentWidget(self.sb_conn)

    # ══ sessions ══════════════════════════════════════════════════════════
    def open_session_manager(self):
        dlg = SessionDialog(self)
        if dlg.exec() and dlg.connection:
            self.add_session(dlg.session, dlg.connection)
            return True
        return False

    def add_session(self, params, conn):
        s = ServerSession(params, conn)
        self.sessions.append(s)
        node = Node("server", s)
        node.setText(0, s.name + ("  [PROD]" if s.env == "production" else ""))
        if s.env == "production":
            node.setForeground(0, QColor(T["danger"]))
        node.setIcon(0, icons.icon("server", s.color or None))
        node.setToolTip(0, f"{params['user']}@{params['host']}:{params['port']}\n{conn.server_version}")
        node.add_placeholder()
        self.tree.addTopLevelItem(node)
        s.item = node
        self.tree.setCurrentItem(node)
        node.setExpanded(True)
        self._set_fixed_tabs_enabled(True)
        self.tabs.setCurrentIndex(T_HOST)
        if not any(isinstance(self.tabs.widget(i), QueryTab) for i in range(self.tabs.count())):
            self.new_query(s, params.get("database") or None, switch=False)
        self.status(f"Connected to {s.name} ({conn.server_version})")

    def disconnect_current(self):
        node = self.tree.currentItem()
        s = node.session if isinstance(node, Node) else None
        if not s:
            return
        for i in reversed(range(self.tabs.count())):
            w = self.tabs.widget(i)
            if isinstance(w, QueryTab) and w.session is s:
                if not w.maybe_close():
                    return
                w.close_connection()
                self.tabs.removeTab(i)
                w.deleteLater()
        for tab in (self.data_tab, self.table_tab, self.db_tab, self.host_tab):
            if tab.session is s:
                tab.session = None
        if self.monitor_tab.session is s:
            self.monitor_tab.set_session(None)
        self.tree.takeTopLevelItem(self.tree.indexOfTopLevelItem(s.item))
        self.sessions.remove(s)
        s.conn.close()
        query_log.message.emit(f"Disconnected from {s.name}", "info")
        if not self.sessions:
            self._set_fixed_tabs_enabled(False)
            self.sb_conn.setText("Not connected")
        self._update_actions()

    def _set_fixed_tabs_enabled(self, on):
        for i in range(FIXED_TABS):
            self.tabs.setTabEnabled(i, on)
        if on:
            self.tabs.setTabEnabled(T_TABLE, False)
            self.tabs.setTabEnabled(T_DATA, False)

    # ══ tree ══════════════════════════════════════════════════════════════
    def _on_expand(self, node):
        if not isinstance(node, Node) or node.loaded or node.loading:
            return
        if node.kind == "server":
            self._load_server(node)
        elif node.kind == "db":
            self._load_db(node)

    def _load_server(self, node, then=None):
        node.loading = True
        s = node.session
        only = [d.strip() for d in (s.params.get("database") or "").split(";") if d.strip()]

        def done(dbs):
            node.loading = False
            node.loaded = True
            expanded = {node.child(i).name for i in range(node.childCount())
                        if isinstance(node.child(i), Node) and node.child(i).isExpanded()}
            key = self._begin_rebuild(node)
            node.takeChildren()
            for db in dbs:
                if only and db not in only:
                    continue
                child = Node("db", s, db, db)
                child.setText(0, db)
                child.setIcon(0, icons.icon("database"))
                child.add_placeholder()
                node.addChild(child)
                if db in expanded:
                    child.setExpanded(True)
            self._end_rebuild(node, key)
            self._filter_tree(self.tree_filter.text())
            if then:
                then()

        def failed(e):
            node.loading = False
            self.status(e, error=True)
        run_async(s.conn.databases, done, failed)

    def _load_db(self, node, then=None):
        node.loading = True
        s, db = node.session, node.db

        def done(objs):
            node.loading = False
            node.loaded = True
            s.objects[db] = objs
            key = self._begin_rebuild(node)
            node.takeChildren()
            total = 0
            for o in objs:
                child = Node(o["kind"], s, db, o["name"])
                child.setText(0, o["name"])
                child.setIcon(0, icons.icon(o["kind"]))
                if o["kind"] == "table":
                    total += o["size"] or 0
                    child.setText(1, human_size(o["size"]))
                    child.setForeground(1, QColor(T["faint"]))
                    child.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
                    child.setToolTip(0, f"{o['name']}\n~{o['rows'] or 0:,} rows · {o['engine']}"
                                        + (f"\n{o['comment']}" if o["comment"] else ""))
                node.addChild(child)
            node.setText(1, human_size(total) if objs else "")
            node.setForeground(1, QColor(T["faint"]))
            node.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
            if not objs:
                node.setChildIndicatorPolicy(QTreeWidgetItem.DontShowIndicator)
            self._end_rebuild(node, key)
            self._filter_tree(self.tree_filter.text())
            if then:
                then()

        def failed(e):
            node.loading = False
            self.status(e, error=True)
        run_async(lambda: s.conn.objects(db), done, failed)

    def _begin_rebuild(self, node):
        """Remember the selected item below `node` and silence selection signals
        while its children are replaced."""
        cur = self.tree.currentItem()
        key = None
        p = cur
        while p is not None:
            if p is node:
                key = (cur.db, cur.name) if isinstance(cur, Node) and cur is not node else None
                break
            p = p.parent()
        self.tree.blockSignals(True)
        return key

    def _end_rebuild(self, node, key):
        if key:
            match = None
            for i in range(node.childCount()):
                c = node.child(i)
                if isinstance(c, Node) and (c.db, c.name) == key:
                    match = c
                    break
                if isinstance(c, Node) and c.kind == "db" and c.db == key[0]:
                    match = c
            match = match or node
            self.tree.setCurrentItem(match)
            self._prev_node = match
        self.tree.blockSignals(False)

    def _db_node(self, session, db):
        root = session.item
        for i in range(root.childCount()):
            c = root.child(i)
            if isinstance(c, Node) and c.db == db:
                return c
        return None

    def refresh_db(self, session, db, select=None):
        """Reload a database's objects in the tree (and the database tab)."""
        if not session or not session.item:
            return
        node = self._db_node(session, db) if db else None
        if node is None:
            self._load_server(session.item)
            return

        def after():
            if select:
                for i in range(node.childCount()):
                    c = node.child(i)
                    if isinstance(c, Node) and c.name == select:
                        self.tree.blockSignals(True)
                        self.tree.setCurrentItem(c)
                        self.tree.blockSignals(False)
                        self._prev_node = c
                        break
        was_expanded = node.isExpanded()
        self._load_db(node, after)
        node.setExpanded(was_expanded or bool(select))
        if self.db_tab.session is session and self.db_tab.db == db:
            self.db_tab._loaded_key = None
            if self.db_tab.isVisible():
                self.db_tab.refresh()

    def cached_objects(self, session, db):
        if db not in session.objects:
            session.objects[db] = session.conn.objects(db)
        return session.objects[db]

    def database_names(self, session):
        root = session.item
        return [root.child(i).db for i in range(root.childCount()) if isinstance(root.child(i), Node)]

    def _filter_tree(self, text):
        text = text.lower().strip()
        for i in range(self.tree.topLevelItemCount()):
            root = self.tree.topLevelItem(i)
            for j in range(root.childCount()):
                dbn = root.child(j)
                if not isinstance(dbn, Node):
                    continue
                db_match = not text or text in dbn.db.lower()
                any_child = False
                for k in range(dbn.childCount()):
                    obj = dbn.child(k)
                    if not isinstance(obj, Node):
                        continue
                    m = not text or db_match or text in obj.name.lower()
                    obj.setHidden(not m)
                    any_child = any_child or (text and text in obj.name.lower())
                dbn.setHidden(not (db_match or any_child))
                if text and any_child and not dbn.isExpanded():
                    dbn.setExpanded(True)

    def _on_select(self, node, prev=None):
        if not isinstance(node, Node):
            return
        leaving_table = (self._prev_node is not None and self._prev_node is not node)
        if leaving_table and (not self.data_tab.maybe_save() or not self.table_tab.maybe_save()):
            self.tree.blockSignals(True)
            self.tree.setCurrentItem(self._prev_node)
            self.tree.blockSignals(False)
            return
        self._prev_node = node
        s = node.session
        self.host_tab.set_session(s)
        self.monitor_tab.set_session(s)
        p = s.params
        self.sb_conn.setText(("PRODUCTION  " if s.env == "production" else "")
                             + f"● {p['user']}@{p['host']}:{p['port']}" + (f"  /  {node.db}" if node.db else ""))
        self.sb_conn.setStyleSheet(f"color: {T['danger']}; font-weight: 600;" if s.env == "production" else "")
        self.tabs.setTabText(T_HOST, f"Host: {s.name}")
        current = self.tabs.currentIndex()
        in_query = isinstance(self.tabs.currentWidget(), QueryTab)
        if node.kind == "server":
            if not in_query:
                self.tabs.setCurrentIndex(T_HOST)
        if node.db:
            self.db_tab.set_database(s, node.db)
            self.tabs.setTabText(T_DB, f"Database: {node.db}")
            self.tabs.setTabEnabled(T_DB, True)
        if node.kind == "db" and not in_query and current in (T_HOST, T_TABLE, T_DATA):
            self.tabs.setCurrentIndex(T_DB)
        if node.kind in ("table", "view"):
            is_table = node.kind == "table"
            self.tabs.setTabEnabled(T_TABLE, is_table)
            self.tabs.setTabEnabled(T_DATA, True)
            self.tabs.setTabText(T_TABLE, f"Table: {node.name}" if is_table else "Table")
            self.tabs.setTabText(T_DATA, f"Data: {node.name}")
            if is_table:
                self.table_tab.set_table(s, node.db, node.name)
            self.data_tab.set_table(s, node.db, node.name, node.kind)
            if not in_query and (current in (T_HOST, T_DB) or (current == T_TABLE and not is_table)):
                self.tabs.setCurrentIndex(T_DATA)
        self._update_actions()

    def _on_double(self, node, _col):
        if not isinstance(node, Node):
            return
        if node.kind in ("table", "view"):
            self.tabs.setCurrentIndex(T_DATA)
        elif node.kind in ("procedure", "function", "trigger"):
            self.show_create_code(node)

    def select_object(self, session, db, name=None, kind=None, open_data=False):
        """Select a database (and optionally an object in it) in the tree."""
        dbn = self._db_node(session, db)
        if dbn is None:
            return
        if not name:
            self.tree.setCurrentItem(dbn)
            self.tabs.setCurrentIndex(T_DB)
            return

        def pick():
            for i in range(dbn.childCount()):
                c = dbn.child(i)
                if isinstance(c, Node) and c.name == name:
                    self.tree.setCurrentItem(c)
                    self.tree.scrollToItem(c)
                    if open_data:
                        self.tabs.setCurrentIndex(T_DATA)
                    return
        dbn.setExpanded(True)
        if dbn.loaded:
            pick()
        else:
            self._load_db(dbn, pick)

    def _tree_menu(self, pos):
        node = self.tree.itemAt(pos)
        if not isinstance(node, Node):
            menu = QMenu(self)
            menu.addAction(self.a_sessions)
            menu.exec(self.tree.viewport().mapToGlobal(pos))
            return
        self.tree.setCurrentItem(node)
        s, db, name = node.session, node.db, node.name
        menu = QMenu(self)
        add = menu.addAction
        if node.kind == "server":
            add(icons.icon("query"), "New query tab", lambda: self.new_query(s))
            add(icons.icon("database"), "Create database…", lambda: self.create_database(s))
            add(icons.icon("import"), "Import SQL file…", lambda: self._import(s, None))
            menu.addSeparator()
            add(icons.icon("refresh"), "Refresh", lambda: self._load_server(node))
            add(icons.icon("unplug"), "Disconnect", self.disconnect_current)
        elif node.kind == "db":
            add(icons.icon("query"), "New query tab", lambda: self.new_query(s, db))
            add(icons.icon("table"), "Create table…", lambda: self.create_table(s, db))
            add(icons.icon("search"), "Search in database…", lambda: self.global_search(s, db))
            add(icons.icon("diff"), "Compare with another database…", lambda: self.schema_diff(s, db))
            add(icons.icon("diagram"), "ER diagram…", lambda: self.er_diagram(s, db))
            menu.addSeparator()
            add(icons.icon("export"), "Export database as SQL…", lambda: self.export_db(s, db))
            add(icons.icon("import"), "Import SQL file into database…", lambda: self._import(s, db))
            menu.addSeparator()
            add(icons.icon("copy"), "Copy name", lambda: QApplication.clipboard().setText(db))
            add(icons.icon("refresh"), "Refresh", lambda: self.refresh_db(s, db))
            menu.addSeparator()
            add(icons.icon("trash"), "Drop database…", lambda: self.drop_database(s, db))
        elif node.kind in ("table", "view"):
            add(icons.icon("view"), "Show data", lambda: self.tabs.setCurrentIndex(T_DATA))
            if node.kind == "table":
                add(icons.icon("edit"), "Edit structure", lambda: self.tabs.setCurrentIndex(T_TABLE))
            if node.kind == "table":
                add(icons.icon("bolt"), "Generate code (PHP, Laravel, Node, Python …)…",
                    lambda: self.generate_code(node))
                add(icons.icon("wand"), "Generate test data…", lambda: self.fake_data(node))
            gen = menu.addMenu(icons.icon("code"), "Generate SQL")
            for label, kind in (("SELECT", "select"), ("INSERT", "insert"), ("UPDATE", "update"),
                                ("DELETE", "delete"), ("CREATE code", "create")):
                gen.addAction(label, lambda k=kind: self.generate_sql(node, k))
            menu.addSeparator()
            add(icons.icon("export"), "Export…", lambda: self.export_db(s, db, [name]))
            add(icons.icon("copy"), "Copy name", lambda: QApplication.clipboard().setText(name))
            if node.kind == "table":
                add(icons.icon("edit"), "Rename…", lambda: self.rename_table(node))
                add(icons.icon("copy"), "Duplicate…", lambda: self.duplicate_table(node))
                menu.addSeparator()
                add(icons.icon("minus"), "Empty table (truncate)…", lambda: self.truncate_table(node))
            add(icons.icon("trash"), f"Drop {node.kind}…", lambda: self.drop_object(node))
        else:
            add(icons.icon("code"), "Show CREATE code", lambda: self.show_create_code(node))
            add(icons.icon("copy"), "Copy name", lambda: QApplication.clipboard().setText(name))
            add(icons.icon("trash"), f"Drop {node.kind}…", lambda: self.drop_object(node))
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    # ══ object actions ════════════════════════════════════════════════════
    def _ctx(self):
        node = self.tree.currentItem()
        if isinstance(node, Node):
            return node.session, node.db
        return (self.sessions[0], None) if self.sessions else (None, None)

    def _run_sql(self, session, sql, done=None, title="Error"):
        run_async(lambda: session.conn.execute(sql), done,
                  lambda e: QMessageBox.critical(self, title, e))

    def create_database(self, session=None):
        session = session or self._ctx()[0]
        if not session:
            return
        dlg = CreateDatabaseDialog(self, session.conn.collations())
        if dlg.exec() and dlg.name.text().strip():
            name = dlg.name.text().strip()
            self._run_sql(session, dlg.sql(),
                          lambda *_: (self.status(f"Database {name} created."),
                                      self._load_server(session.item,
                                                        lambda: self.select_object(session, name))),
                          "Create database")

    def drop_database(self, session, db):
        if confirm(self, "Drop database", f"Drop database “{db}” and ALL its tables?\n\nThis cannot be undone.",
                   danger=True):
            self._run_sql(session, f"DROP DATABASE {qi(db)}",
                          lambda *_: (self.status(f"Database {db} dropped."), self._load_server(session.item)),
                          "Drop database")

    def create_table(self, session=None, db=None):
        if session is None:
            session, db = self._ctx()
        if not session or not db:
            QMessageBox.information(self, "Create table", "Select a database in the tree first.")
            return
        if not self.table_tab.maybe_save():
            return
        self.tabs.setTabEnabled(T_TABLE, True)
        self.tabs.setTabText(T_TABLE, "Table: (new)")
        self.table_tab.new_table(session, db)
        self.tabs.setCurrentIndex(T_TABLE)

    def drop_object(self, node):
        if confirm(self, f"Drop {node.kind}", f"Drop {node.kind} “{node.db}.{node.name}”?\n\nThis cannot be undone.",
                   danger=True):
            self._run_sql(node.session, f"DROP {node.kind.upper()} {qt(node.db, node.name)}",
                          lambda *_: self.refresh_db(node.session, node.db), f"Drop {node.kind}")

    def truncate_table(self, node):
        if confirm(self, "Empty table", f"Delete ALL rows from “{node.name}”?\n\nThis cannot be undone.", danger=True):
            self._run_sql(node.session, f"TRUNCATE TABLE {qt(node.db, node.name)}",
                          lambda *_: (self.refresh_db(node.session, node.db), self.data_tab.refresh()),
                          "Empty table")

    def rename_table(self, node):
        new, ok = QInputDialog.getText(self, "Rename table", "New name:", text=node.name)
        if ok and new.strip() and new.strip() != node.name:
            self._run_sql(node.session, f"RENAME TABLE {qt(node.db, node.name)} TO {qt(node.db, new.strip())}",
                          lambda *_: self.refresh_db(node.session, node.db, select=new.strip()), "Rename table")

    def duplicate_table(self, node):
        new, ok = QInputDialog.getText(self, "Duplicate table", "Name of the copy:", text=f"{node.name}_copy")
        if not (ok and new.strip()):
            return
        with_data = QMessageBox.question(self, "Duplicate table", "Copy the data as well?") == QMessageBox.Yes
        s, src, dst = node.session, qt(node.db, node.name), qt(node.db, new.strip())

        def work():
            s.conn.execute(f"CREATE TABLE {dst} LIKE {src}")
            if with_data:
                s.conn.execute(f"INSERT INTO {dst} SELECT * FROM {src}")
        run_async(work, lambda *_: self.refresh_db(s, node.db, select=new.strip()),
                  lambda e: QMessageBox.critical(self, "Duplicate table", e))

    def show_create_code(self, node):
        s = node.session
        run_async(lambda: s.conn.create_code(node.db, node.name, node.kind),
                  lambda code: self.new_query(s, node.db, _wrap_delimiter(code, node.kind)),
                  lambda e: QMessageBox.critical(self, "CREATE code", e))

    def generate_sql(self, node, kind):
        s, db, name = node.session, node.db, node.name
        if kind == "create":
            return self.show_create_code(node)

        def build(cols):
            names = [c["Field"] for c in cols]
            t = qi(name)
            if kind == "select":
                return f"SELECT {', '.join(qi(n) for n in names)}\nFROM {t}\nWHERE 1\nLIMIT 1000;"
            if kind == "insert":
                return (f"INSERT INTO {t}\n  ({', '.join(qi(n) for n in names)})\nVALUES\n  "
                        f"({', '.join('?' for _ in names)});")
            if kind == "update":
                return f"UPDATE {t} SET\n  " + ",\n  ".join(f"{qi(n)} = ?" for n in names) + "\nWHERE ...;"
            return f"DELETE FROM {t}\nWHERE ...;"
        run_async(lambda: s.conn.columns(db, name), lambda cols: self.new_query(s, db, build(cols)))

    def generate_code(self, node):
        from .codegen import CodeGenDialog, Table
        s, db, name = node.session, node.db, node.name
        run_async(lambda: Table(db, name, s.conn.columns(db, name), s.conn.foreign_keys(db, name)),
                  lambda t: CodeGenDialog(self, t).exec(),
                  lambda e: QMessageBox.critical(self, "Generate code", e))

    def fake_data(self, node):
        from .fakedata import FakeDataDialog
        FakeDataDialog(self, node.session, node.db, node.name).exec()
        self.refresh_db(node.session, node.db)

    def _import(self, session, db):
        if import_file(self, session, db):
            if db:
                self.refresh_db(session, db)
            else:
                self._load_server(session.item)

    def import_sql(self):
        s, db = self._ctx()
        if s:
            self._import(s, db)

    def export_db(self, session=None, db=None, tables=None):
        if session is None:
            session, db = self._ctx()
        if not session or not db:
            QMessageBox.information(self, "Export", "Select a database in the tree first.")
            return
        try:
            objs = self.cached_objects(session, db)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Export", str(exc))
            return
        ExportDialog(self, session, db, [o["name"] for o in objs if o["kind"] == "table"], tables).exec()

    # ══ query tabs ════════════════════════════════════════════════════════
    def new_query(self, session=None, db=None, sql="", switch=True):
        if session is None:
            session, ctx_db = self._ctx()
            db = db or ctx_db
        if not session:
            QMessageBox.information(self, "New query", "Connect to a server first (File → Session manager).")
            return None
        self._query_counter += 1
        tab = QueryTab(self, session, db, sql, self._query_counter)
        tab.title_changed.connect(self._query_title)
        idx = self.tabs.addTab(tab, icons.icon("query"), tab.title())
        if switch:
            self.tabs.setCurrentIndex(idx)
            tab.editor.setFocus()
        return tab

    def _query_title(self, tab):
        i = self.tabs.indexOf(tab)
        if i >= 0:
            self.tabs.setTabText(i, tab.title())
            self.tabs.setTabToolTip(i, tab.path or f"{tab.session.name}")

    def close_query_tab(self, index):
        w = self.tabs.widget(index)
        if isinstance(w, QueryTab) and w.maybe_close():
            w.close_connection()
            self.tabs.removeTab(index)
            w.deleteLater()

    def current_query(self):
        w = self.tabs.currentWidget()
        return w if isinstance(w, QueryTab) else None

    def _query_do(self, fn):
        q = self.current_query()
        if q is None:
            q = self._last_query_tab()
            if q is None:
                return
            self.tabs.setCurrentWidget(q)
        fn(q)

    def _last_query_tab(self):
        for i in reversed(range(self.tabs.count())):
            if isinstance(self.tabs.widget(i), QueryTab):
                return self.tabs.widget(i)
        return None

    def open_sql_file(self):
        q = self.current_query()
        if q is None or q.editor.toPlainText().strip():
            q = self.new_query()
        if q:
            q.open_file()

    def save_current(self):
        w = self.tabs.currentWidget()
        if isinstance(w, QueryTab):
            w.save_file()
        elif w is self.data_tab:
            self.data_tab.save()
        elif w is self.table_tab:
            self.table_tab.save()

    def refresh_current(self):
        w = self.tabs.currentWidget()
        if w is self.host_tab:
            self.host_tab.refresh()
        elif w is self.monitor_tab:
            self.monitor_tab.run_health()
        elif w is self.db_tab:
            self.db_tab.refresh()
        elif w is self.table_tab:
            self.table_tab._loaded_key = None
            self.table_tab.reload()
        elif w is self.data_tab:
            if self.data_tab.maybe_save():
                self.data_tab.refresh()
        node = self.tree.currentItem()
        if isinstance(node, Node) and isinstance(w, QueryTab):
            if node.kind == "server":
                self._load_server(node)
            elif node.db:
                self.refresh_db(node.session, node.db)

    def query_state_changed(self, _tab):
        self._update_actions()

    def _update_actions(self):
        q = self.current_query()
        connected = bool(self.sessions)
        running = bool(q and q.running)
        for a in (self.a_run, self.a_run_cur, self.a_explain):
            a.setEnabled(connected and not running)
        self.a_stop.setEnabled(running)
        for a in (self.a_disconnect, self.a_new_query, self.a_open, self.a_import, self.a_export, self.a_create_db,
                  self.a_create_table, self.a_processes, self.a_users, self.a_variables, self.a_refresh,
                  self.a_monitor):
            a.setEnabled(connected)
        self.a_save_as.setEnabled(q is not None)
        self.a_close_tab.setEnabled(q is not None)
        self.a_find.setEnabled(q is not None)
        self.a_format.setEnabled(q is not None)
        self.a_save_lib.setEnabled(q is not None)
        self.a_search.setEnabled(connected)
        self.a_diff.setEnabled(connected)
        self.a_er.setEnabled(connected)
        self.a_ai.setEnabled(connected)

    def open_monitor(self):
        s = self._ctx()[0]
        if s:
            self.monitor_tab.set_session(s)
            self.tabs.setCurrentIndex(T_MONITOR)

    def open_palette(self):
        from .palette import CommandPalette
        entries = []
        for s in self.sessions:
            root = s.item
            for i in range(root.childCount()):
                dbn = root.child(i)
                if not isinstance(dbn, Node):
                    continue
                entries.append(("database", dbn.db, f"database · {s.name}",
                                lambda s=s, d=dbn.db: self.select_object(s, d)))
                objs = [(dbn.child(k).kind, dbn.child(k).name) for k in range(dbn.childCount())
                        if isinstance(dbn.child(k), Node)]
                if not objs and dbn.db in s.objects:
                    objs = [(o["kind"], o["name"]) for o in s.objects[dbn.db]]
                if not objs:
                    objs = getattr(s, "all_tables", {}).get(dbn.db, [])
                entries += self._object_entries(s, dbn.db, objs)
        seen = set()
        for a in self.actions():
            text = a.text().replace("&", "").rstrip("…").strip()
            if not text or not a.isEnabled() or text in seen or a is self.a_palette:
                continue
            seen.add(text)
            entries.append(("command", text, a.shortcut().toString(), a.trigger))
        for provider in getattr(self, "palette_providers", []):
            entries.extend(provider())
        entries.sort(key=lambda e: (e[0] != "command", e[1].lower()))
        palette = CommandPalette(self, entries)
        known = {(e[2], e[1]) for e in entries}
        for s in self.sessions:
            if time.time() - getattr(s, "all_tables_at", 0) < 60:
                continue

            def load(conn):
                out = {}
                for db, name, ttype in conn.rows(
                        "SELECT TABLE_SCHEMA, TABLE_NAME, TABLE_TYPE FROM information_schema.TABLES WHERE TABLE_SCHEMA "
                        "NOT IN ('information_schema','performance_schema','mysql','sys') ORDER BY 1, 2"):
                    out.setdefault(db, []).append(("view" if ttype == "VIEW" else "table", name))
                return out

            def loaded(tables, s=s):
                s.all_tables, s.all_tables_at = tables, time.time()
                if palette.isVisible():
                    new = [e for db, objs in tables.items() for e in self._object_entries(s, db, objs)
                           if (e[2], e[1]) not in known]
                    palette.add_entries(new)
            run_async(functools.partial(load, s.conn), loaded)
        palette.exec()

    def _object_entries(self, s, db, objs):
        return [(kind, name, f"{kind} in {db}",
                 lambda s=s, d=db, n=name, k=kind: self.select_object(s, d, n, k, open_data=k in ("table", "view")))
                for kind, name in objs]

    def save_query_to_library(self):
        from .saved_queries import SaveQueryDialog, library
        q = self.current_query()
        if q is None or not q.editor.toPlainText().strip():
            return
        existing = library.get(getattr(q, "saved_query_id", None) or "")
        default = existing["name"] if existing else " ".join(q.editor.toPlainText().split())[:50]
        dlg = SaveQueryDialog(self, default, existing.get("folder", "") if existing else "")
        if dlg.exec() and dlg.name.text().strip():
            item = library.add(dlg.name.text().strip(), q.editor.toPlainText(), dlg.folder.currentText().strip(),
                               q.current_db() or "")
            q.saved_query_id = item["id"]
            self.saved_panel.reload()
            self.saved_dock.show()
            self.saved_dock.raise_()
            self.status(f"Saved query “{item['name']}”.")

    def global_search(self, session=None, db=None):
        from .search_dialog import GlobalSearchDialog
        if session is None:
            session, db = self._ctx()
        if session:
            GlobalSearchDialog(self, session, db).show()

    def schema_diff(self, session=None, db=None):
        from .schema_diff import SchemaDiffDialog
        if session is None:
            session, db = self._ctx()
        if session:
            SchemaDiffDialog(self, session, db).exec()

    def er_diagram(self, session=None, db=None):
        from .er_diagram import ErDiagramDialog
        if session is None:
            session, db = self._ctx()
        if not session or not db:
            QMessageBox.information(self, "ER diagram", "Select a database in the tree first.")
            return
        ErDiagramDialog(self, session, db).show()

    def ai_settings(self):
        from .ai_assistant import AiSettingsDialog
        AiSettingsDialog(self).exec()

    def edit_snippets(self):
        from .editor_tools import SnippetDialog
        SnippetDialog(self).exec()

    def _host_page(self, index):
        if self._ctx()[0]:
            self.tabs.setCurrentIndex(T_HOST)
            self.host_tab.tabs.setCurrentIndex(index)

    # ══ log / history / status ════════════════════════════════════════════
    def _append_log(self, text, level):
        color = {"sql": T["text"], "info": T["muted"], "error": T["danger"]}.get(level, T["text"])
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(color))
        stamp = QTextCharFormat()
        stamp.setForeground(QColor(T["faint"]))
        cur = self.log.textCursor()
        cur.movePosition(QTextCursor.End)
        cur.insertText(time.strftime("%H:%M:%S  "), stamp)
        text = text if len(text) < 4000 else text[:4000] + " …"
        cur.insertText((text if level == "sql" else f"/* {text} */").replace("\r", "") + "\n", fmt)
        self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())

    def history_changed(self, *_):
        text = self.hist_filter.text().lower()
        self.hist_list.clear()
        for h in history.items[:400]:
            if text and text not in h["sql"].lower():
                continue
            first = " ".join(h["sql"].split())[:160]
            it = QListWidgetItem(f"{h['ts'][5:]}  {first}")
            it.setData(Qt.UserRole, h["sql"])
            it.setToolTip(f"{h['session']} / {h['db'] or '-'} · {h['ms']} ms\n\n{h['sql'][:1500]}")
            if not h["ok"]:
                it.setForeground(QColor(T["danger"]))
            self.hist_list.addItem(it)

    def _history_insert(self, item):
        sql = item.data(Qt.UserRole)
        q = self.current_query() or self._last_query_tab() or self.new_query()
        if q:
            self.tabs.setCurrentWidget(q)
            q.insert_text(sql + ";\n")

    def _clear_history(self):
        if confirm(self, "Query history", "Clear the whole query history?"):
            history.clear()
            self.history_changed()

    def status(self, msg, error=False):
        self.sb_msg.setText(msg.splitlines()[0][:300] if msg else "")
        self.sb_msg.setStyleSheet(f"color: {T['danger']};" if error else "")

    def set_query_time(self, ms):
        self.sb_time.setText(f"Last query: {ms:,.1f} ms")

    # ══ misc ══════════════════════════════════════════════════════════════
    def set_theme(self, name):
        if name == settings.get("theme", "dark"):
            return
        settings.set("theme", name)
        if confirm(self, "Theme", "The theme is applied fully after a restart.\nRestart Quantum SQL now?"):
            if getattr(sys, "frozen", False):
                args = sys.argv[1:]
            elif sys.argv[0].endswith("__main__.py"):
                args = ["-m", "quantumsql"]
            else:
                args = sys.argv
            if self.close():
                QProcess.startDetached(sys.executable, args)
        else:
            icons.clear_cache()
            theme.apply(QApplication.instance(), name)

    def show_guide(self):
        from PySide6.QtWidgets import QDialog, QTextBrowser
        path = resource_path("docs/USER_GUIDE.md")
        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
        except OSError:
            QMessageBox.warning(self, "User guide", f"The user guide was not found at {path}.")
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(f"{APP_NAME} — User guide")
        dlg.resize(980, 760)
        view = QTextBrowser(dlg)
        view.setOpenExternalLinks(True)
        view.document().setDefaultStyleSheet(f"table {{ border-collapse: collapse; }} td, th {{ padding: 4px 8px; }}")
        view.setMarkdown(text)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(view)
        dlg.show()

    def show_shortcuts(self):
        rows = [("F9", "Execute all (or the selection)"), ("Ctrl+Enter", "Execute current statement"),
                ("Ctrl+Space", "Autocomplete"), ("Ctrl+/", "Toggle comment"), ("Ctrl+F", "Find in query"),
                ("Ctrl+Shift+F", "Format SQL"), ("abbr + Tab", "Expand snippet (sel, ins, upd, ct, dup …)"),
                (":name", "Query parameter — asked for when you run"), ("Ctrl+Shift+M", "Live monitor"),
                ("Ctrl+Shift+P", "Command palette — jump to any command or table"),
                ("Ctrl+I", "AI assistant (write / explain / fix / optimise SQL)"), ("Ctrl+D", "Save query to library"),
                ("Ctrl+T / Ctrl+W", "New / close query tab"), ("Ctrl+O / Ctrl+S", "Open / save (or post data)"),
                ("F5", "Refresh"), ("Ctrl+N", "Session manager"), ("Ins", "Insert row (Data tab)"),
                ("Ctrl+Del", "Delete rows (Data tab)"), ("Ctrl+Shift+N", "Set cell to NULL"),
                ("Shift+Enter", "View / edit cell value"), ("Ctrl+Wheel", "Zoom editor")]
        html = "<table cellpadding='4'>" + "".join(f"<tr><td><b>{k}</b></td><td>{v}</td></tr>" for k, v in rows) \
               + "</table>"
        QMessageBox.information(self, "Keyboard shortcuts", html)

    def show_about(self):
        QMessageBox.about(self, f"About {APP_NAME}",
                          f"<h3>{APP_NAME} {APP_VERSION}</h3><p>A fast desktop client for MySQL and MariaDB.</p>"
                          "<p>Built with Python, Qt (PySide6) and PyMySQL.</p>")

    def closeEvent(self, e):
        if not self.data_tab.maybe_save() or not self.table_tab.maybe_save():
            e.ignore()
            return
        for i in range(self.tabs.count()):
            w = self.tabs.widget(i)
            if isinstance(w, QueryTab) and not w.maybe_close():
                e.ignore()
                return
        settings.set("geometry", base64.b64encode(bytes(self.saveGeometry())).decode())
        settings.set("window_state", base64.b64encode(bytes(self.saveState())).decode())
        for i in range(self.tabs.count()):
            w = self.tabs.widget(i)
            if isinstance(w, QueryTab):
                w.close_connection()
        self.monitor_tab.shutdown()
        for s in self.sessions:
            s.conn.close()
        from .ssh_tunnel import close_all
        close_all()
        e.accept()


def _wrap_delimiter(code, kind):
    if kind in ("procedure", "function", "trigger"):
        return f"DELIMITER $$\n{code}$$\nDELIMITER ;\n"
    return code + ";\n"

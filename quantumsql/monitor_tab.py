"""Monitor tab: live server load, top SQL, sessions per application and a health advisor.

Works on every MySQL/MariaDB server by sampling the process list once per interval
(the same idea as RDS Performance Insights' "average active sessions"). When
performance_schema is enabled, exact per-statement statistics are shown as well.
"""
import re
import time
import traceback
from collections import Counter, defaultdict, deque

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFrame, QGridLayout, QLabel, QMessageBox, QSplitter,
                               QStyledItemDelegate, QTabWidget, QTextBrowser, QVBoxLayout, QWidget)

from . import icons
from .charts import TimeSeriesChart
from .config import data_dir
from .db import human_size, run_async
from .grid import DataGrid, ResultModel
from .theme import T
from .ui import LoadingOverlay, bar, confirm, muted, tool

IDLE_COMMANDS = {"Sleep", "Daemon", "Binlog Dump", "Binlog Dump GTID", "Connect", "Slave_IO", "Slave_SQL",
                 "Slave_worker"}
INTERVALS = [("1 s", 1), ("2 s", 2), ("5 s", 5), ("10 s", 10)]
WINDOWS = [("1 min", 60), ("5 min", 300), ("15 min", 900), ("1 hour", 3600)]
RATE_KEYS = ("Questions", "Com_select", "Com_insert", "Com_update", "Com_delete", "Com_replace",
             "Bytes_received", "Bytes_sent", "Slow_queries", "Connections", "Aborted_connects",
             "Innodb_buffer_pool_read_requests", "Innodb_buffer_pool_reads", "Innodb_rows_read",
             "Handler_read_rnd_next", "Created_tmp_disk_tables", "Select_full_join")
ERROR_PRIOS = [("All messages", ()), ("Errors and warnings", ("Error", "Warning")), ("Errors only", ("Error",))]
SLICES = [("Wait state", 0), ("SQL", 1), ("Application (host)", 3), ("User", 2), ("Database", 4)]
LONG_QUERY_S = 5
MAX_INFO = 4000  # chars of each running statement kept per sample

_RX_COMMENT = re.compile(r"/\*.*?\*/|--[^\n]*|#[^\n]*", re.S)
_RX_STRING = re.compile(r"'(?:[^'\\]|\\.|'')*'|\"(?:[^\"\\]|\\.)*\"")
_RX_NUMBER = re.compile(r"(?<![\w$`])-?\d+(?:\.\d+)?(?:e[+-]?\d+)?\b|\b0x[0-9a-f]+\b", re.I)
_RX_LIST = re.compile(r"\(\s*\?(?:\s*,\s*\?)+\s*\)")
_RX_VALUES = re.compile(r"(VALUES\s*\(\.\.\.\))(?:\s*,\s*\(\.\.\.\))+", re.I)
_RX_TABLES = re.compile(r"\b(?:FROM|JOIN|UPDATE|INTO|TABLE)\s+((?:`[^`]+`|[\w$]+)(?:\.(?:`[^`]+`|[\w$]+))?)", re.I)


def normalize_sql(sql):
    """Turn a statement into a digest: literals become ?, lists collapse, whitespace squashes."""
    s = _RX_COMMENT.sub(" ", sql or "")
    s = _RX_STRING.sub("?", s)
    s = _RX_NUMBER.sub("?", s)
    s = _RX_LIST.sub("(...)", s)
    s = _RX_VALUES.sub(r"\1, ...", s)
    s = " ".join(s.split())
    return s[:1500]


def tables_in(sql, db):
    out = set()
    for m in _RX_TABLES.finditer(sql or ""):
        name = m.group(1).replace("`", "")
        if name.upper() in ("SELECT", "DUAL") or name.startswith("("):
            continue
        out.add(name if "." in name or not db else f"{db}.{name}")
    return out


def wait_class(command, state):
    st = (state or "").lower()
    if command == "Killed":
        return "Killed"
    if "lock" in st or st == "locked":
        return "Lock wait"
    if any(k in st for k in ("tmp table", "sort", "heap to ondisk", "temporary")):
        return "Temp table / sort"
    if any(k in st for k in ("commit", "query end", "updating", "update", "insert", "deleting", "end",
                             "waiting for handler")):
        return "Write / commit"
    if any(k in st for k in ("opening table", "closing table", "checking permissions", "init", "starting")):
        return "Table open / init"
    if any(k in st for k in ("net", "sending to client", "reading from", "writing to")):
        return "Network"
    if "statistics" in st or "optimiz" in st or "preparing" in st:
        return "Optimizer"
    return "CPU / executing"


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _parse_processlist(cols, rows, my_id, t):
    """PROCESSLIST rows -> (all sessions, active sessions). Runs in the sampling thread."""
    cols = [c.upper() for c in cols]
    proc, active = [], []
    for r in rows:
        p = dict(zip(cols, r))
        pid = p.get("ID")
        if pid == my_id:
            continue
        cmd = p.get("COMMAND") or ""
        elapsed = _num(p.get("TIME_MS")) / 1000 if p.get("TIME_MS") is not None else _num(p.get("TIME"))
        info = p.get("INFO")
        if isinstance(info, bytes):
            info = info.decode("utf-8", "replace")
        if info and len(info) > MAX_INFO:  # bulk INSERTs can be megabytes; samples keep up to an hour
            info = info[:MAX_INFO]
        rec = {"id": pid, "user": p.get("USER") or "", "host": _host_ip(p.get("HOST")), "db": p.get("DB") or "",
               "cmd": cmd, "time": elapsed, "state": p.get("STATE") or "", "info": info or ""}
        proc.append(rec)
        if cmd not in IDLE_COMMANDS and rec["user"] not in ("system user", "event_scheduler"):
            rec["wait"] = wait_class(cmd, rec["state"])
            rec["digest"] = normalize_sql(info) if info else f"({cmd})"
            rec["tables"] = tables_in(info, rec["db"])
            rec["start"] = round(t - elapsed, 0)
            active.append(rec)
    return proc, active


def _host_ip(host):
    host = host or ""
    if host.startswith("[") and "]" in host:
        return host[1:host.index("]")]
    if host.count(":") == 1:
        return host.rsplit(":", 1)[0]
    return host or "(local)"


class BarDelegate(QStyledItemDelegate):
    """Draws a translucent bar behind a numeric cell, scaled to the column's largest value."""

    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        model = index.model()
        top = model.col_max.get(index.column()) if hasattr(model, "col_max") else None
        value = model.rows[index.row()][index.column()] if top else None
        if not isinstance(value, (int, float)) or value <= 0:
            return
        r = QRectF(option.rect).adjusted(2, 3, -2, -3)
        r.setWidth(max(2.0, r.width() * min(1.0, value / top)))
        col = QColor(T["accent"])
        col.setAlpha(70)
        painter.save()
        painter.setPen(Qt.NoPen)
        painter.setBrush(col)
        painter.drawRoundedRect(r, 2, 2)
        painter.restore()


class LiveModel(ResultModel):
    """Read-only model whose rows are replaced every tick, keeping the user's sort."""

    def __init__(self, headers, rows=(), highlight=None, bar_cols=()):
        super().__init__(headers, rows)
        self._sort = None
        self.highlight = highlight  # fn(row) -> color token or None
        self.bar_cols = tuple(bar_cols)
        self.col_max = {}

    def set_rows(self, rows):
        self.beginResetModel()
        self.rows = [list(r) for r in rows]
        self.col_max = {c: max((r[c] for r in self.rows if isinstance(r[c], (int, float))), default=0)
                        for c in self.bar_cols}
        self.endResetModel()
        if self._sort:
            super().sort(*self._sort)

    def sort(self, column, order=Qt.AscendingOrder):
        self._sort = (column, order) if column >= 0 else None
        super().sort(column, order)

    def data(self, index, role=Qt.DisplayRole):
        if role == Qt.ForegroundRole and self.highlight and index.isValid():
            tok = self.highlight(self.rows[index.row()])
            if tok:
                return QColor(T[tok])
        return super().data(index, role)


class LiveGrid(DataGrid):
    """DataGrid that keeps column widths and the selected row (by key column) across refreshes."""

    def __init__(self, headers, key_col=0, highlight=None, bar_cols=()):
        super().__init__()
        self.key_col = key_col
        self.live = LiveModel(headers, [], highlight, bar_cols)
        self.setModel(self.live)
        if bar_cols:
            self._bars = BarDelegate(self)
            for c in bar_cols:
                self.setItemDelegateForColumn(c, self._bars)
        self.setSortingEnabled(True)
        self.horizontalHeader().setSortIndicator(-1, Qt.AscendingOrder)
        self._sized = False

    def set_rows(self, rows):
        key = None
        idx = self.currentIndex()
        if idx.isValid() and idx.row() < len(self.live.rows):
            key = self.live.rows[idx.row()][self.key_col]
            col = idx.column()
        self.live.set_rows(rows)
        if not self._sized and rows:
            self.autosize_columns()
            self._sized = True
        if key is not None:
            for r, row in enumerate(self.live.rows):
                if row[self.key_col] == key:
                    self.setCurrentIndex(self.live.index(r, col))
                    break

    def current_row(self):
        idx = self.currentIndex()
        return self.live.rows[idx.row()] if idx.isValid() and idx.row() < len(self.live.rows) else None


class Tile(QFrame):
    def __init__(self, caption):
        super().__init__()
        self.setObjectName("card")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(0)
        self.cap = muted(caption.upper())
        self.cap.setStyleSheet("font-size: 7.5pt; font-weight: 600;")
        self.val = QLabel("–")
        self.val.setStyleSheet("font-size: 16pt; font-weight: 600;")
        self.sub = muted("")
        self.sub.setStyleSheet("font-size: 8pt;")
        lay.addWidget(self.cap)
        lay.addWidget(self.val)
        lay.addWidget(self.sub)
        self.setMinimumWidth(120)

    def set(self, value, sub="", level=None):
        self.val.setText(value)
        self.sub.setText(sub)
        if level != getattr(self, "_level", None):  # restyling re-polishes the widget; skip when unchanged
            self._level = level
            self.val.setStyleSheet("font-size: 16pt; font-weight: 600;"
                                   + (f" color: {T[level]};" if level else ""))


class MonitorTab(QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        self.session = None
        self.conn = None
        self.samples = deque(maxlen=3600)
        self.prev = None
        self.vars = {}
        self.latest_proc = []
        self.digest_base = None
        self.digest_rows = []
        self.health_extra = {}
        self.table_io = []
        self._busy = False
        self._tick_no = 0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self.b_run = tool("play", "Start / pause live monitoring", self.toggle, "Start", checkable=True)
        self.interval = QComboBox()
        for label, sec in INTERVALS:
            self.interval.addItem(f"Every {label}", sec)
        self.interval.currentIndexChanged.connect(self._restart_timer)
        self.window = QComboBox()
        for label, sec in WINDOWS:
            self.window.addItem(f"Last {label}", sec)
        self.window.setCurrentIndex(1)
        self.window.currentIndexChanged.connect(self._window_changed)
        self.slice = QComboBox()
        for label, dim in SLICES:
            self.slice.addItem(f"Slice load by: {label}", dim)
        self.slice.currentIndexChanged.connect(lambda *_: self._render())
        self.ps_label = muted("")
        self.alert = QLabel("")
        self.alert.setStyleSheet(f"color: {T['danger']}; font-weight: 600;")
        lay.addWidget(bar(self.b_run, self.interval, self.window, "|", self.slice, "|", self.ps_label, None,
                          self.alert, tool("trash", "Clear collected samples", self.clear, "Clear")))

        body = QSplitter(Qt.Vertical)
        top = QWidget()
        tl = QVBoxLayout(top)
        tl.setContentsMargins(8, 8, 8, 4)
        tl.setSpacing(8)
        tiles = QGridLayout()
        tiles.setSpacing(8)
        self.t_conn = Tile("Connections")
        self.t_active = Tile("Active sessions")
        self.t_qps = Tile("Queries / sec")
        self.t_slow = Tile("Slow queries")
        self.t_bp = Tile("Buffer pool hit")
        self.t_net = Tile("Network")
        self.t_rows = Tile("Rows read / sec")
        self.t_up = Tile("Uptime")
        for i, t in enumerate((self.t_conn, self.t_active, self.t_qps, self.t_slow, self.t_bp, self.t_net,
                               self.t_rows, self.t_up)):
            tiles.addWidget(t, 0, i)
        tl.addLayout(tiles)
        charts = QGridLayout()
        charts.setSpacing(8)
        self.c_load = TimeSeriesChart("Database load — average active sessions", stacked=True)
        self.c_qps = TimeSeriesChart("Queries per second by type", "/s")
        self.c_conn = TimeSeriesChart("Connections")
        self.c_net = TimeSeriesChart("Network throughput", " KB/s")
        charts.addWidget(self.c_load, 0, 0)
        charts.addWidget(self.c_qps, 0, 1)
        charts.addWidget(self.c_conn, 1, 0)
        charts.addWidget(self.c_net, 1, 1)
        tl.addLayout(charts, 1)
        body.addWidget(top)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("subtabs")
        self.g_top = LiveGrid(["Load (AAS)", "Load %", "Executions seen", "Max time (s)", "Database", "User",
                               "Tables", "SQL digest", "Example"], key_col=7, bar_cols=(0, 3))
        self.g_top.doubleClicked.connect(lambda *_: self._open_sql(self.g_top, 8, 4))
        self.tabs.addTab(self._page(self.g_top, [
            muted("Sampled from live sessions — works on every server. Double-click to open in a query tab."),
            None, tool("chart", "EXPLAIN the selected query", lambda: self._open_sql(self.g_top, 8, 4, True),
                       "Explain")]), icons.icon("chart"), "Top SQL")

        self.g_sess = LiveGrid(["Id", "User", "Application (host)", "Database", "Command", "Time (s)", "Wait",
                                "State", "Query"], highlight=self._sess_color, bar_cols=(5,))
        self.g_sess.doubleClicked.connect(lambda *_: self._open_sql(self.g_sess, 8, 3))
        self.show_idle = QCheckBox("Show idle (Sleep) connections")
        self.show_idle.toggled.connect(lambda *_: self._render_sessions())
        self.tabs.addTab(self._page(self.g_sess, [
            tool("x", "Kill the running query (connection stays open)", lambda: self._kill(query_only=True),
                 "Kill query"),
            tool("unplug", "Kill the whole connection", lambda: self._kill(query_only=False), "Kill connection"),
            tool("chart", "EXPLAIN the selected query", lambda: self._open_sql(self.g_sess, 8, 3, True),
                 "Explain"), "|", self.show_idle, None,
            muted(f"Red = running longer than {LONG_QUERY_S} s")]), icons.icon("activity"), "Active sessions")

        self.g_apps = LiveGrid(["Application (host)", "User", "Databases", "Connections", "Active", "Idle",
                                "Longest idle (s)", "Longest query (s)", "Load share %", "Note"],
                               highlight=lambda r: "warning" if r[9] else None, bar_cols=(3, 8))
        self.tabs.addTab(self._page(self.g_apps, [
            muted("Every client (web app, API, cron, tool) grouped by host and user. "
                  "Many long-idle connections usually mean the app is not closing or pooling them.")]),
            icons.icon("users"), "Connections by app")

        self.g_tables = LiveGrid(["Table", "Load (AAS)", "Load %", "Statements seen", "Reads", "Writes",
                                  "Wait time (s)", "Source"], bar_cols=(1, 6))
        self.tabs.addTab(self._page(self.g_tables, [
            muted("Tables touched by active statements (sampled). With performance_schema: real read/write I/O.")]),
            icons.icon("table"), "Hot tables")

        self.g_digest = LiveGrid(["Database", "Statement digest", "Calls", "Total time (s)", "Avg (ms)",
                                  "Max (ms)", "Rows examined", "Rows sent", "Examined / sent", "No index used",
                                  "Tmp disk tables", "Last seen"], key_col=1,
                                 highlight=lambda r: "warning" if r[9] else None, bar_cols=(3, 4))
        self.g_digest.doubleClicked.connect(lambda *_: self._open_sql(self.g_digest, 1, 0))
        self.digest_since = QCheckBox("Only since monitoring started")
        self.digest_since.setChecked(True)
        self.digest_since.toggled.connect(lambda *_: self._render_digest())
        self.ps_note = muted("")
        self.tabs.addTab(self._page(self.g_digest, [self.digest_since, "|", self.ps_note]),
                         icons.icon("search"), "Statement statistics")

        self.health = QTextBrowser()
        self.health.setOpenLinks(False)
        self.health.anchorClicked.connect(self._health_action)
        self.health_loader = LoadingOverlay(self.health, "Running health checks…")
        self.tabs.addTab(self._page(self.health, [tool("refresh", "Run the health checks again",
                                                       self.run_health, "Run checks"), None,
                                                  muted("Recommendations based on server status and settings")]),
                         icons.icon("check"), "Health advisor")

        self.g_errors = LiveGrid(["Time", "Priority", "Code", "Subsystem", "Message"], key_col=0,
                                 highlight=lambda r: {"Error": "danger", "Warning": "warning"}.get(r[1]))
        self.err_prio = QComboBox()
        for label, prios in ERROR_PRIOS:
            self.err_prio.addItem(label, prios)
        self.err_prio.setCurrentIndex(1)
        self.err_prio.currentIndexChanged.connect(lambda *_: self.load_errors())
        self.err_note = muted("")
        self.err_note.setWordWrap(True)
        self.err_loader = LoadingOverlay(self.g_errors, "Reading the server error log…")
        self.tabs.addTab(self._page(self.g_errors, [
            self.err_prio, tool("refresh", "Read the error log again", self.load_errors, "Refresh"), "|",
            self.err_note]), icons.icon("log"), "Error log")
        self.tabs.currentChanged.connect(self._tab_changed)
        body.addWidget(self.tabs)
        body.setSizes([470, 330])
        lay.addWidget(body, 1)
        self._update_ps_label()

    def _page(self, widget, items):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(bar(*items))
        lay.addWidget(widget, 1)
        return w

    # ── lifecycle ──
    def set_session(self, session):
        if session is self.session:
            return
        was_running = self.timer.isActive()
        self.stop()
        self.session = session
        self.clear()
        self.vars = {}
        if self.conn:
            self.conn.close()
            self.conn = None
        self._update_ps_label()
        if was_running and session:
            self.start()

    def showEvent(self, e):
        super().showEvent(e)
        if self.session and not self.timer.isActive() and not self.samples:
            self.start()
        elif getattr(self, "_stale", False):
            self._render()

    def toggle(self):
        self.stop() if self.timer.isActive() else self.start()

    def start(self):
        if not self.session:
            return
        if self.conn is None:
            self.conn = self.session.conn.clone()
        self.alert.setText("")
        self.timer.start(self.interval.currentData() * 1000)
        self._sync_button()
        self._tick()

    def _restart_timer(self, *_):
        if self.timer.isActive():
            self.timer.start(self.interval.currentData() * 1000)

    def stop(self):
        self.timer.stop()
        self._sync_button()

    def _sync_button(self):
        on = self.timer.isActive()
        self.b_run.setChecked(on)
        self.b_run.setText("Pause" if on else "Start")
        self.b_run.setIcon(icons.icon("stop" if on else "play"))

    def shutdown(self):
        self.stop()
        if self.conn:
            self.conn.close()
            self.conn = None

    def _window_changed(self, *_):
        self._render()
        if self.tabs.currentWidget() is self.g_errors.parentWidget():
            self.load_errors()

    def _tab_changed(self, *_):
        if self.tabs.currentWidget() is self.g_errors.parentWidget():
            self.load_errors()
        else:
            self._render_tables()

    def clear(self):
        self.samples.clear()
        self.prev = None
        self.digest_base = None
        self.digest_rows = []
        self.latest_proc = []
        self._render()

    # ── sampling (background thread) ──
    def _tick(self):
        if self._busy or not self.session or self.conn is None:
            return
        self._busy = True
        conn = self.conn
        need_vars = not self.vars or self._tick_no % 60 == 0
        want_digest = self.vars.get("performance_schema") == "ON" and self._tick_no % 5 == 0
        self._tick_no += 1

        def work():
            with conn.lock:
                out = {"t": time.time()}
                out["status"] = {k: v for k, v in conn.rows("SHOW GLOBAL STATUS")}
                cols, rows, _ = conn.query("SELECT * FROM information_schema.PROCESSLIST", log=False)
                # Parse here, off the UI thread: the SQL normalising regexes are the costly part of a tick.
                out["proc"], out["active"] = _parse_processlist(cols, rows, conn.thread_id(), out["t"])
                if need_vars:
                    out["vars"] = {k: v for k, v in conn.rows("SHOW GLOBAL VARIABLES")}
                if want_digest:
                    out["digest"] = conn.rows(
                        "SELECT SCHEMA_NAME, DIGEST_TEXT, COUNT_STAR, SUM_TIMER_WAIT, MAX_TIMER_WAIT, "
                        "SUM_ROWS_EXAMINED, SUM_ROWS_SENT, SUM_NO_INDEX_USED + SUM_NO_GOOD_INDEX_USED, "
                        "SUM_CREATED_TMP_DISK_TABLES, LAST_SEEN, DIGEST "
                        "FROM performance_schema.events_statements_summary_by_digest "
                        "WHERE DIGEST_TEXT IS NOT NULL ORDER BY SUM_TIMER_WAIT DESC LIMIT 300")
                    out["table_io"] = conn.rows(
                        "SELECT OBJECT_SCHEMA, OBJECT_NAME, COUNT_READ, COUNT_WRITE, SUM_TIMER_WAIT "
                        "FROM performance_schema.table_io_waits_summary_by_table "
                        "WHERE OBJECT_SCHEMA NOT IN ('mysql','performance_schema','sys') "
                        "ORDER BY SUM_TIMER_WAIT DESC LIMIT 100")
                return out

        def failed(e):
            self._busy = False
            self.stop()
            self.alert.setText("Monitoring stopped: " + e.splitlines()[0][:120])
            if self.conn:
                self.conn.close()

        run_async(work, self._ingest, failed, quiet=True)

    def _ingest(self, raw):
        self._busy = False
        try:
            self._ingest_sample(raw)
        except Exception as exc:  # noqa: BLE001 - would otherwise repeat on every tick
            self.stop()
            self.alert.setText(f"Monitoring stopped: {type(exc).__name__}: {str(exc)[:100]}")
            try:
                with open(f"{data_dir()}/error.log", "a", encoding="utf-8") as f:
                    f.write("Monitor sample failed:\n" + traceback.format_exc() + "\n")
            except OSError:
                pass

    def _ingest_sample(self, raw):
        if raw.get("vars"):
            first = not self.vars
            self.vars = raw["vars"]
            self._update_ps_label()
            if first:
                QTimer.singleShot(0, self.run_health)
        st = raw["status"]
        t = raw["t"]
        active = raw["active"]
        self.latest_proc = raw["proc"]

        rates, dt = {}, 0.0
        if self.prev:
            dt = max(0.001, t - self.prev["t"])
            ps = self.prev["status"]
            for k in RATE_KEYS:
                rates[k] = max(0.0, (_num(st.get(k)) - _num(ps.get(k))) / dt)
        self.prev = {"t": t, "status": st}
        self.samples.append({"t": t, "dt": dt, "active": active, "rates": rates,
                             "connected": _num(st.get("Threads_connected")), "running": len(active)})
        if "digest" in raw:
            if self.digest_base is None:
                self.digest_base = {(r[0], r[10]): r for r in raw["digest"]}
            self.digest_rows = raw["digest"]
            self.table_io = raw.get("table_io", [])
        self._render()

    # ── rendering ──
    def _window(self):
        if not self.samples:
            return []
        cutoff = self.samples[-1]["t"] - self.window.currentData()
        return [s for s in self.samples if s["t"] >= cutoff]

    def _render(self):
        if not self.isVisible():  # keep sampling in the background, draw only when someone is looking
            self._stale = True
            return
        self._stale = False
        win = self._window()
        self._render_tiles(win)
        self._render_charts(win)
        self._render_tables()

    def _render_tiles(self, win):
        st = self.prev["status"] if self.prev else {}
        if not st:
            for tile in (self.t_conn, self.t_active, self.t_qps, self.t_slow, self.t_bp, self.t_net, self.t_rows,
                         self.t_up):
                tile.set("–")
            self.alert.setText("")
            return
        last = self.samples[-1]
        r = last["rates"]
        maxc = _num(self.vars.get("max_connections")) or 0
        conn = last["connected"]
        pct = conn / maxc * 100 if maxc else 0
        self.t_conn.set(f"{conn:,.0f}", f"of {maxc:,.0f} max · peak {_num(st.get('Max_used_connections')):,.0f}",
                        "danger" if pct > 85 else "warning" if pct > 65 else None)
        aas = sum(len(s["active"]) for s in win) / max(1, len(win))
        self.t_active.set(f"{len(last['active'])}", f"avg {aas:.2f} over window")
        self.t_qps.set(f"{r.get('Questions', 0):,.0f}" if r else "…",
                       f"sel {r.get('Com_select', 0):,.0f} · wr "
                       f"{r.get('Com_insert', 0) + r.get('Com_update', 0) + r.get('Com_delete', 0):,.0f}" if r else "")
        slow_win = sum(s["rates"].get("Slow_queries", 0) * s["dt"] for s in win)
        lqt = _num(self.vars.get("long_query_time"))
        self.t_slow.set(f"{slow_win:,.0f}", f"in window · > {lqt:g} s · total {_num(st.get('Slow_queries')):,.0f}",
                        "warning" if slow_win else None)
        req, reads = _num(st.get("Innodb_buffer_pool_read_requests")), _num(st.get("Innodb_buffer_pool_reads"))
        if r.get("Innodb_buffer_pool_read_requests", 0) > 2000:
            hit = 100 * (1 - r.get("Innodb_buffer_pool_reads", 0) / r["Innodb_buffer_pool_read_requests"])
        else:
            hit = 100 * (1 - reads / req) if req else 100
        self.t_bp.set(f"{hit:.2f}%", "reads served from memory", "danger" if hit < 95 else "warning" if hit < 99
                      else "success")
        self.t_net.set(f"{r.get('Bytes_sent', 0) / 1024:,.0f} KB/s" if r else "…",
                       f"out · in {r.get('Bytes_received', 0) / 1024:,.0f} KB/s" if r else "")
        self.t_rows.set(f"{r.get('Innodb_rows_read', 0):,.0f}" if r else "…",
                        f"full-scan reads {r.get('Handler_read_rnd_next', 0):,.0f}/s" if r else "")
        up = int(_num(st.get("Uptime")))
        self.t_up.set(f"{up // 86400}d {up % 86400 // 3600}h", f"{up % 3600 // 60}m · {self.session.conn.server_version}"
                      if self.session else "")
        long_q = [a for a in last["active"] if a["time"] >= LONG_QUERY_S and a["cmd"] == "Query"]
        self.alert.setText(f"⚠ {len(long_q)} quer{'y' if len(long_q) == 1 else 'ies'} running > {LONG_QUERY_S} s"
                           if long_q else "")

    def _slice_key(self, a, dim):
        return (a["wait"], a["digest"], a["user"], a["host"], a["db"] or "(none)")[dim]

    def _render_charts(self, win):
        times = [s["t"] for s in win]
        dim = self.slice.currentData()
        totals = Counter()
        for s in win:
            for a in s["active"]:
                totals[self._slice_key(a, dim)] += 1
        top = [k for k, _ in totals.most_common(6)]
        series = []
        for k in top:
            label = k if len(k) <= 48 else k[:46] + "…"
            series.append((label, [sum(1 for a in s["active"] if self._slice_key(a, dim) == k) for s in win]))
        if len(totals) > len(top):
            series.append(("Other", [sum(1 for a in s["active"] if self._slice_key(a, dim) not in top) for s in win]))
        if not series:
            series = [("Idle", [0] * len(win))]
        self.c_load.set_data(times, series)
        rate = lambda k: [s["rates"].get(k, 0) for s in win]  # noqa: E731
        self.c_qps.set_data(times, [("SELECT", rate("Com_select")), ("INSERT", rate("Com_insert")),
                                    ("UPDATE", rate("Com_update")), ("DELETE", rate("Com_delete")),
                                    ("All", rate("Questions"))])
        maxc = _num(self.vars.get("max_connections"))
        ref = (maxc, f"max_connections = {maxc:,.0f}") if maxc and win and \
            max(s["connected"] for s in win) > maxc * 0.5 else None
        self.c_conn.set_data(times, [("Connected", [s["connected"] for s in win]),
                                     ("Active", [s["running"] for s in win]),
                                     ("New / s", rate("Connections"))], ref)
        self.c_net.set_data(times, [("Out", [v / 1024 for v in rate("Bytes_sent")]),
                                    ("In", [v / 1024 for v in rate("Bytes_received")])])

    def _render_tables(self):
        w = self.tabs.currentWidget()
        if not self.isVisible() or w is None:
            return
        page = self.tabs.currentIndex()
        if page == 0:
            self._render_top()
        elif page == 1:
            self._render_sessions()
        elif page == 2:
            self._render_apps()
        elif page == 3:
            self._render_hot_tables()
        elif page == 4:
            self._render_digest()

    def _render_top(self):
        win = self._window()
        n = max(1, len(win))
        agg = {}
        total = 0
        for s in win:
            for a in s["active"]:
                total += 1
                d = agg.setdefault(a["digest"], {"n": 0, "execs": set(), "max": 0.0, "db": Counter(),
                                                 "user": Counter(), "ex": a["info"], "tables": a["tables"]})
                d["n"] += 1
                d["execs"].add((a["id"], a["start"]))
                d["max"] = max(d["max"], a["time"])
                d["db"][a["db"]] += 1
                d["user"][a["user"]] += 1
        rows = []
        for dig, d in sorted(agg.items(), key=lambda kv: -kv[1]["n"]):
            rows.append([round(d["n"] / n, 3), round(100 * d["n"] / max(1, total), 1), len(d["execs"]),
                         round(d["max"], 1), d["db"].most_common(1)[0][0], d["user"].most_common(1)[0][0],
                         ", ".join(sorted(d["tables"])[:4]), dig, d["ex"]])
        self.g_top.set_rows(rows[:200])

    def _sess_color(self, row):
        if row[4] == "Sleep":
            return "faint"
        if row[5] >= LONG_QUERY_S and row[4] == "Query":
            return "danger"
        return None

    def _render_sessions(self):
        idle = self.show_idle.isChecked()
        rows = []
        for p in self.latest_proc:
            if not idle and p["cmd"] in IDLE_COMMANDS:
                continue
            rows.append([p["id"], p["user"], p["host"], p["db"], p["cmd"], round(p["time"], 1),
                         wait_class(p["cmd"], p["state"]) if p["cmd"] not in IDLE_COMMANDS else "",
                         p["state"], p["info"]])
        rows.sort(key=lambda r: -r[5])
        self.g_sess.set_rows(rows)

    def _render_apps(self):
        win = self._window()
        load = Counter()
        total = 0
        for s in win:
            for a in s["active"]:
                load[(a["host"], a["user"])] += 1
                total += 1
        groups = defaultdict(lambda: {"dbs": set(), "n": 0, "active": 0, "idle": 0, "max_idle": 0.0,
                                      "max_q": 0.0})
        for p in self.latest_proc:
            if p["user"] in ("system user", "event_scheduler"):
                continue
            g = groups[(p["host"], p["user"])]
            g["n"] += 1
            if p["db"]:
                g["dbs"].add(p["db"])
            if p["cmd"] == "Sleep":
                g["idle"] += 1
                g["max_idle"] = max(g["max_idle"], p["time"])
            elif p["cmd"] not in IDLE_COMMANDS:
                g["active"] += 1
                g["max_q"] = max(g["max_q"], p["time"])
        wait_timeout = _num(self.vars.get("wait_timeout")) or 28800
        rows = []
        for (host, user), g in groups.items():
            note = []
            if g["idle"] >= 10 and g["max_idle"] > min(300, wait_timeout / 2):
                note.append("many long-idle connections — possible leak / no pooling")
            if g["max_q"] >= LONG_QUERY_S:
                note.append(f"query running {g['max_q']:.0f} s")
            rows.append([host, user, ", ".join(sorted(g["dbs"])), g["n"], g["active"], g["idle"],
                         round(g["max_idle"]), round(g["max_q"], 1),
                         round(100 * load[(host, user)] / total, 1) if total else 0.0, "; ".join(note)])
        rows.sort(key=lambda r: (-r[8], -r[3]))
        self.g_apps.set_rows(rows)

    def _render_hot_tables(self):
        win = self._window()
        n = max(1, len(win))
        load, stmts = Counter(), defaultdict(set)
        total = 0
        for s in win:
            for a in s["active"]:
                for tb in a["tables"]:
                    load[tb] += 1
                    stmts[tb].add((a["id"], a["start"]))
                    total += 1
        rows = {}
        for tb, c in load.items():
            rows[tb] = [tb, round(c / n, 3), round(100 * c / max(1, total), 1), len(stmts[tb]), "", "", "",
                        "sampled"]
        for schema, name, reads, writes, wait in self.table_io:
            key = f"{schema}.{name}"
            row = rows.setdefault(key, [key, 0.0, 0.0, 0, "", "", "", ""])
            row[4], row[5], row[6] = int(reads or 0), int(writes or 0), round(_num(wait) / 1e12, 3)
            row[7] = "sampled + perf_schema" if row[7] == "sampled" else "perf_schema"
        self.g_tables.set_rows(sorted(rows.values(), key=lambda r: (-r[1], -_num(r[6] or 0)))[:200])

    def _render_digest(self):
        if self.vars and self.vars.get("performance_schema") != "ON":
            self.ps_note.setText("performance_schema is OFF on this server — enable it (my.ini: "
                                 "performance_schema=ON, then restart) for exact per-query timings. "
                                 "The Top SQL tab works without it.")
            self.g_digest.set_rows([])
            return
        self.ps_note.setText("Exact statistics from performance_schema (refreshed every 5 samples)")
        base = (self.digest_base or {}) if self.digest_since.isChecked() else {}
        rows = []
        for r in self.digest_rows:
            schema, text, calls, total, mx, exam, sent, noidx, tmpd, last, dig = r
            b = base.get((schema, dig))
            if b:
                calls, total, exam, sent, noidx, tmpd = (calls - b[2], total - b[3], exam - b[5], sent - b[6],
                                                         noidx - b[7], tmpd - b[8])
            if not calls:
                continue
            rows.append([schema or "", text, int(calls), round(_num(total) / 1e12, 3),
                         round(_num(total) / 1e9 / calls, 2), round(_num(mx) / 1e9, 2), int(exam), int(sent),
                         round(exam / sent, 1) if sent else (int(exam) if exam else 0), int(noidx), int(tmpd),
                         str(last or "")])
        rows.sort(key=lambda x: -x[3])
        self.g_digest.set_rows(rows)

    def load_errors(self):
        if not self.session:
            return
        conn = self.conn or self.session.conn
        seconds = int(self.window.currentData())
        prios = self.err_prio.currentData()
        sql = ("SELECT LOGGED, PRIO, ERROR_CODE, SUBSYSTEM, DATA FROM performance_schema.error_log "
               "WHERE LOGGED >= NOW(6) - INTERVAL %s SECOND"
               + (f" AND PRIO IN ({', '.join(['%s'] * len(prios))})" if prios else "")
               + " ORDER BY LOGGED DESC LIMIT 2000")

        def work():
            with conn.lock:
                try:
                    return "ok", conn.rows(sql, (seconds, *prios))
                except Exception as exc:  # noqa: BLE001 - explained below instead of raised
                    path = conn.rows("SELECT @@log_error")
                    return "unavailable", (str(exc), path[0][0] if path else "")

        def done(res):
            self.err_loader.stop()
            kind, data = res
            label = self.window.currentText().lower()
            if kind == "ok":
                self.g_errors.set_rows([[str(t), p, c, sub, msg] for t, p, c, sub, msg in data])
                self.err_note.setText(f"{len(data):,} message(s) in the {label}"
                                      + (" (newest 2,000 shown)" if len(data) >= 2000 else "")
                                      + " · read from performance_schema.error_log")
            else:
                self.g_errors.set_rows([])
                reason, path = data
                self.err_note.setText(
                    "This server can't show its error log over SQL (needs MySQL 8.0.22+ and SELECT on "
                    "performance_schema.error_log; MariaDB doesn't have it)."
                    + (f" The log file is {path} on the server." if path else "") + f"  [{reason[:120]}]")

        def failed(e):
            self.err_loader.stop()
            self.err_note.setText(e.splitlines()[0][:200])

        self.err_loader.start()
        run_async(work, done, failed)

    def _update_ps_label(self):
        if not self.session:
            self.ps_label.setText("Not connected")
        elif not self.vars:
            self.ps_label.setText(f"{self.session.name}")
        else:
            on = self.vars.get("performance_schema") == "ON"
            self.ps_label.setText(f"{self.session.name}  ·  performance_schema "
                                  f"{'ON — exact statistics' if on else 'OFF — sampling mode'}")

    # ── actions ──
    def _open_sql(self, grid, sql_col, db_col, explain=False):
        row = grid.current_row()
        if not row or not self.session:
            return
        sql = str(row[sql_col] or "").strip()
        if not sql or sql.startswith("("):
            return
        db = row[db_col] or None
        if explain and "?" in sql and grid is not self.g_sess:
            QMessageBox.information(self, "Explain", "This is a digest with ? placeholders; replace them with "
                                                     "real values in the query tab, then press Explain.")
        tab = self.main.new_query(self.session, db, sql.rstrip(";") + ";\n")
        if tab and explain and "?" not in sql:
            tab.explain()

    def _kill(self, query_only):
        row = self.g_sess.current_row()
        if not row or not self.session:
            return
        pid = int(row[0])
        what = "the query of" if query_only else "connection"
        if not confirm(self, "Kill", f"Kill {what} {pid} ({row[1]}@{row[2]})?\n\n{str(row[8])[:300]}", danger=True):
            return
        conn = self.session.conn
        run_async(lambda: conn.execute(f"KILL {'QUERY ' if query_only else ''}{pid}"),
                  lambda *_: self.main.status(f"Killed {what} {pid}."),
                  lambda e: QMessageBox.critical(self, "Kill failed", e))

    # ── health advisor ──
    def run_health(self):
        if not self.session or self.conn is None:
            return
        conn = self.conn

        def work():
            with conn.lock:
                extra = {}
                try:
                    extra["innodb_data"] = _num(conn.rows(
                        "SELECT SUM(DATA_LENGTH + INDEX_LENGTH) FROM information_schema.TABLES "
                        "WHERE ENGINE='InnoDB'")[0][0])
                    extra["no_pk"] = conn.rows(
                        "SELECT t.TABLE_SCHEMA, t.TABLE_NAME FROM information_schema.TABLES t "
                        "LEFT JOIN information_schema.TABLE_CONSTRAINTS c ON c.TABLE_SCHEMA=t.TABLE_SCHEMA "
                        "AND c.TABLE_NAME=t.TABLE_NAME AND c.CONSTRAINT_TYPE='PRIMARY KEY' "
                        "WHERE t.TABLE_TYPE='BASE TABLE' AND c.CONSTRAINT_NAME IS NULL AND t.TABLE_SCHEMA NOT IN "
                        "('mysql','information_schema','performance_schema','sys') LIMIT 50")
                    extra["myisam"] = _num(conn.rows(
                        "SELECT COUNT(*) FROM information_schema.TABLES WHERE ENGINE='MyISAM' AND TABLE_SCHEMA "
                        "NOT IN ('mysql','information_schema','performance_schema','sys')")[0][0])
                except Exception as exc:  # noqa: BLE001 - partial results are fine
                    extra["error"] = str(exc)
                return extra

        def done(extra):
            self.health_loader.stop()
            self.health_extra = extra
            self._render_health()
        self.health_loader.start()
        run_async(work, done, lambda e: (self.health_loader.stop(),
                                         self.health.setHtml(f"<p>Could not run checks: {e}</p>")))

    def _render_health(self):
        v = self.vars
        st = self.prev["status"] if self.prev else {}
        if not v or not st:
            self.health.setHtml("<p>Collecting data…</p>")
            return
        items = []  # (level, title, detail, action)

        def add(level, title, detail, action=None):
            items.append((level, title, detail, action))

        maxc = _num(v.get("max_connections"))
        used = _num(st.get("Max_used_connections"))
        if maxc and used / maxc > 0.85:
            add("danger", "Connection limit almost reached",
                f"Peak {used:,.0f} of max_connections {maxc:,.0f}. Raise max_connections or use connection pooling "
                "in the application.")
        elif maxc:
            add("ok", "Connection headroom", f"Peak {used:,.0f} of {maxc:,.0f} connections ({used / maxc:.0%}).")
        sleeping = [p for p in self.latest_proc if p["cmd"] == "Sleep"]
        if len(sleeping) >= 20 and len(sleeping) > 0.6 * max(1, len(self.latest_proc)):
            add("warning", "Many idle connections",
                f"{len(sleeping)} of {len(self.latest_proc)} connections are sleeping. Check the "
                "“Connections by app” tab — apps that open a connection per request and never close it "
                "eventually exhaust max_connections. wait_timeout is "
                f"{_num(v.get('wait_timeout')):,.0f} s.")
        req, reads = _num(st.get("Innodb_buffer_pool_read_requests")), _num(st.get("Innodb_buffer_pool_reads"))
        bp = _num(v.get("innodb_buffer_pool_size"))
        data = self.health_extra.get("innodb_data", 0)
        if req:
            hit = 1 - reads / req
            lvl = "ok" if hit >= 0.99 else "warning" if hit >= 0.95 else "danger"
            add(lvl, "InnoDB buffer pool hit ratio",
                f"{hit:.2%} of page reads come from memory. Buffer pool {human_size(bp)}, InnoDB data + indexes "
                f"{human_size(data)}." + ("" if lvl == "ok" else " Increase innodb_buffer_pool_size "
                                                                 "(about 70% of RAM on a dedicated server)."))
        elif data and bp < data * 0.5:
            add("warning", "Buffer pool smaller than the data",
                f"innodb_buffer_pool_size {human_size(bp)} vs {human_size(data)} of InnoDB data.")
        if v.get("slow_query_log") != "ON":
            add("warning", "Slow query log is off",
                "Turn it on to record every query slower than long_query_time.",
                ("slowlog", "Enable now (long_query_time = 1 s)"))
        elif _num(v.get("long_query_time")) > 2:
            add("info", "long_query_time is high",
                f"Only queries slower than {_num(v.get('long_query_time')):g} s are logged; 1 s is a common choice.",
                ("slowlog", "Set long_query_time = 1 s"))
        if v.get("performance_schema") != "ON":
            add("info", "performance_schema is off",
                "Enable it in my.ini / the RDS parameter group (performance_schema = ON, restart required) to get "
                "exact per-statement timings, rows examined and missing-index flags in “Statement statistics”.")
        tmp, tmp_disk = _num(st.get("Created_tmp_tables")), _num(st.get("Created_tmp_disk_tables"))
        if tmp > 100 and tmp_disk / tmp > 0.25:
            add("warning", "Temporary tables spill to disk",
                f"{tmp_disk / tmp:.0%} of implicit temp tables went to disk. Raise tmp_table_size and "
                "max_heap_table_size together, or avoid TEXT/BLOB columns in GROUP BY / ORDER BY queries.")
        up = max(1, _num(st.get("Uptime")))
        fj = _num(st.get("Select_full_join"))
        if fj / up * 3600 > 10:
            add("warning", "Joins without an index",
                f"{fj:,.0f} joins since start scanned a whole table ({fj / up * 3600:,.1f}/hour). Find them in "
                "Top SQL and add indexes on the join columns.")
        conns = _num(st.get("Connections"))
        ab = _num(st.get("Aborted_connects"))
        if conns > 100 and ab / conns > 0.05:
            add("warning", "Failed connection attempts",
                f"{ab:,.0f} of {conns:,.0f} connection attempts failed ({ab / conns:.1%}) — wrong passwords, "
                "blocked hosts or network timeouts.")
        tc = _num(st.get("Threads_created"))
        if conns > 1000 and tc / conns > 0.1:
            add("info", "Thread cache too small",
                f"{tc / conns:.0%} of connections created a new thread. Raise thread_cache_size "
                f"(now {_num(v.get('thread_cache_size')):,.0f}).")
        ot = _num(st.get("Opened_tables"))
        if ot / up * 3600 > 600:
            add("info", "Table cache churn",
                f"Tables are opened {ot / up * 3600:,.0f} times per hour. Raise table_open_cache "
                f"(now {_num(v.get('table_open_cache')):,.0f}).")
        lw = _num(st.get("Innodb_row_lock_waits"))
        if lw:
            avg = _num(st.get("Innodb_row_lock_time_avg"))
            add("warning" if avg > 500 else "info", "Row lock waits",
                f"{lw:,.0f} row lock waits since start, average {avg:,.0f} ms. Keep transactions short and "
                "update rows in a consistent order.")
        no_pk = self.health_extra.get("no_pk") or []
        if no_pk:
            names = ", ".join(f"{a}.{b}" for a, b in no_pk[:12])
            add("warning", f"{len(no_pk)}{'+' if len(no_pk) >= 50 else ''} tables without a primary key",
                f"InnoDB uses a hidden key and replication / row edits get slow: {names}"
                + ("…" if len(no_pk) > 12 else ""))
        if self.health_extra.get("myisam"):
            add("info", "MyISAM tables in use",
                f"{self.health_extra['myisam']:,.0f} MyISAM tables lock the whole table on writes and are not "
                "crash-safe. Convert with ALTER TABLE … ENGINE=InnoDB.")
        if self.health_extra.get("error"):
            add("info", "Some checks were skipped", self.health_extra["error"])

        order = {"danger": 0, "warning": 1, "info": 2, "ok": 3}
        items.sort(key=lambda x: order[x[0]])
        colors = {"danger": T["danger"], "warning": T["warning"], "info": T["accent"], "ok": T["success"]}
        labels = {"danger": "Critical", "warning": "Warning", "info": "Tip", "ok": "OK"}
        html = [f"<div style='color:{T['text']}'>"]
        for level, title, detail, action in items:
            link = (f" &nbsp; <a href='{action[0]}' style='color:{T['accent']}'>{action[1]}</a>" if action else "")
            html.append(f"<p style='margin:8px 6px'><span style='color:{colors[level]}; font-weight:600'>"
                        f"● {labels[level]}</span> &nbsp;<b>{title}</b><br>"
                        f"<span style='color:{T['muted']}'>{detail}</span>{link}</p>")
        html.append("</div>")
        self.health.setHtml("".join(html))

    def _health_action(self, url):
        if url.toString() == "slowlog" and self.session:
            conn = self.session.conn

            def work():
                conn.execute("SET GLOBAL slow_query_log = 1")
                conn.execute("SET GLOBAL long_query_time = 1")

            def done(_):
                self.vars = {}
                self.main.status("Slow query log enabled (long_query_time = 1 s).")
                self._tick_no = 0
            run_async(work, done, lambda e: QMessageBox.critical(
                self, "Slow query log", f"{e}\n\nOn AWS RDS change slow_query_log and long_query_time in the "
                                        "DB parameter group instead."))

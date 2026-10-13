"""MySQL/MariaDB connection wrapper, SQL helpers and background workers."""
import datetime
import decimal
import inspect
import re
import ssl
import threading
import time

import pymysql
from pymysql.constants import CLIENT
from pymysql.converters import escape_string
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal


# ── Quoting ────────────────────────────────────────────────────────────────
def qi(name):
    """Quote an identifier."""
    return "`" + str(name).replace("`", "``") + "`"


def qt(db, table):
    return f"{qi(db)}.{qi(table)}" if db else qi(table)


def literal(value):
    """Render a Python value as a SQL literal."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float, decimal.Decimal)):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        value = bytes(value)
        return "0x" + value.hex() if value else "''"
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time, datetime.timedelta)):
        return f"'{value}'"
    return "'" + escape_string(str(value)) + "'"


def to_text(value):
    """Text form of a cell value, used for editing and export."""
    if value is None:
        return ""
    if isinstance(value, (bytes, bytearray)):
        try:
            return bytes(value).decode("utf-8")
        except UnicodeDecodeError:
            return "0x" + bytes(value).hex()
    return str(value)


# ── Statement splitter (handles quotes, comments and DELIMITER) ────────────
def split_sql(sql):
    """Return a list of (statement, start, end) tuples."""
    out = []
    n = len(sql)
    delim = ";"
    i = start = 0
    has_code = False
    quote = None

    def flush(end):
        text = sql[start:end].strip()
        if has_code and text:
            out.append((text, start, end))

    while i < n:
        c = sql[i]
        if quote:
            if c == "\\" and quote != "`":
                i += 2
                continue
            if c == quote:
                if i + 1 < n and sql[i + 1] == quote:
                    i += 2
                    continue
                quote = None
            i += 1
            continue

        if c in "dD" and sql[i:i + 10].upper() in ("DELIMITER ", "DELIMITER\t"):
            line_start = sql.rfind("\n", 0, i) + 1
            if not sql[line_start:i].strip():
                flush(line_start)
                eol = sql.find("\n", i)
                eol = n if eol < 0 else eol
                parts = sql[i + 10:eol].split()
                if parts:
                    delim = parts[0]
                i = start = eol
                has_code = False
                continue
        if sql.startswith(delim, i):
            flush(i)
            i += len(delim)
            start = i
            has_code = False
            continue
        if c in "'\"`":
            quote = c
            has_code = True
        elif c == "#" or (c == "-" and sql.startswith("--", i) and (i + 2 >= n or sql[i + 2] in " \t\r\n")):
            eol = sql.find("\n", i)
            i = n if eol < 0 else eol + 1
            continue
        elif sql.startswith("/*", i):
            end = sql.find("*/", i + 2)
            if sql.startswith("/*!", i):
                has_code = True
            i = n if end < 0 else end + 2
            continue
        elif not c.isspace():
            has_code = True
        i += 1
    flush(n)
    return out


_WRITE_KINDS = {"INSERT", "UPDATE", "DELETE", "REPLACE", "MERGE", "LOAD", "CALL"}
_DDL_KINDS = {"CREATE", "ALTER", "DROP", "TRUNCATE", "RENAME", "GRANT", "REVOKE"}


def code_only(sql):
    """The statement with comments removed and string literals blanked, for keyword checks."""
    out, i, n = [], 0, len(sql)
    while i < n:
        c = sql[i]
        if c in "'\"`":
            j = i + 1
            while j < n:
                if sql[j] == "\\" and c != "`":
                    j += 2
                    continue
                if sql[j] == c:
                    if j + 1 < n and sql[j + 1] == c:
                        j += 2
                        continue
                    break
                j += 1
            out.append(c + " " * max(0, min(j, n) - i - 1) + (c if j < n else ""))
            i = j + 1
        elif c == "#" or (sql.startswith("--", i) and (i + 2 >= n or sql[i + 2] in " \t\r\n")):
            j = sql.find("\n", i)
            i = n if j < 0 else j
        elif sql.startswith("/*", i) and not sql.startswith("/*!", i):
            j = sql.find("*/", i + 2)
            i = n if j < 0 else j + 2
            out.append(" ")
        else:
            out.append(c)
            i += 1
    return "".join(out)


def statement_kind(sql):
    """First keyword of a statement (WITH … resolves to the main verb)."""
    words = code_only(sql).split()
    if not words:
        return ""
    kind = words[0].upper().lstrip("(")
    if kind == "WITH":
        m = re.search(r"\)\s*(SELECT|UPDATE|DELETE|INSERT)\b", code_only(sql), re.I)
        return m.group(1).upper() if m else "SELECT"
    return kind


def is_write(sql):
    return statement_kind(sql) in _WRITE_KINDS


def is_ddl(sql):
    return statement_kind(sql) in _DDL_KINDS


def missing_where(sql):
    """UPDATE / DELETE that would touch every row."""
    kind = statement_kind(sql)
    if kind not in ("UPDATE", "DELETE"):
        return False
    code = code_only(sql)
    return not re.search(r"\bWHERE\b", code, re.I) and not re.search(r"\bLIMIT\s+\d", code, re.I)


def statement_at(sql, pos):
    """The statement under the cursor (or the one right before it)."""
    stmts = split_sql(sql)
    if not stmts:
        return None
    best = stmts[0]
    for st in stmts:
        if st[1] <= pos:
            best = st
        if st[1] <= pos <= st[2] + 1:
            return st
    return best


# ── Connection ─────────────────────────────────────────────────────────────
class QueryLog(QObject):
    """Application-wide SQL log; emitted from any thread."""
    message = Signal(str, str)  # text, level (sql|info|error)


query_log = QueryLog()


class DbConnection:
    def __init__(self, params):
        self.params = dict(params)
        self.conn = None
        self.lock = threading.RLock()
        self.server_version = ""

    def clone(self):
        return DbConnection(self.params)

    def connect(self):
        p = self.params
        with self.lock:
            self.close()
            host, port = p.get("host") or "127.0.0.1", int(p.get("port") or 3306)
            if p.get("ssh_enabled"):
                from .ssh_tunnel import tunnel_for
                host, port = tunnel_for(p)
            self.conn = pymysql.connect(
                host=host, port=port,
                user=p.get("user") or "", password=p.get("password") or "",
                database=p.get("database") or None, charset="utf8mb4",
                autocommit=True, connect_timeout=int(p.get("timeout") or 10),
                local_infile=False, client_flag=CLIENT.FOUND_ROWS)
            self.server_version = self.conn.get_server_info()
            via = f" via SSH {p.get('ssh_user')}@{p.get('ssh_host')}" if p.get("ssh_enabled") else ""
            if isinstance(self.conn._sock, ssl.SSLSocket):
                secure = ", TLS encrypted"
            elif p.get("ssh_enabled"):
                secure = ", encrypted by the SSH tunnel"
            else:
                secure = ", NOT encrypted — the server has no TLS; use an SSH tunnel over untrusted networks"
            query_log.message.emit(f"Connected to {p.get('user')}@{p.get('host')}:{p.get('port')}{via} "
                                   f"(server {self.server_version}{secure})", "info")

    def close(self):
        if self.conn:
            try:
                self.conn.close()
            except Exception:
                pass
            self.conn = None

    @property
    def is_mariadb(self):
        return "mariadb" in self.server_version.lower()

    def _ensure(self):
        if self.conn is None:
            self.connect()
        else:
            try:
                self.conn.ping(reconnect=True)
            except Exception:
                self.connect()

    def thread_id(self):
        return self.conn.thread_id() if self.conn else None

    def query(self, sql, args=None, log=True):
        """Run a statement; returns (columns, rows, rowcount)."""
        with self.lock:
            self._ensure()
            if log:
                query_log.message.emit(sql if args is None else self.conn.cursor().mogrify(sql, args), "sql")
            with self.conn.cursor() as cur:
                cur.execute(sql, args)
                if cur.description:
                    return [d[0] for d in cur.description], list(cur.fetchall()), cur.rowcount
                return [], [], cur.rowcount

    def rows(self, sql, args=None, log=False):
        return self.query(sql, args, log)[1]

    def execute(self, sql, args=None):
        return self.query(sql, args)[2]

    def use(self, db):
        if db:
            self.query(f"USE {qi(db)}", log=False)

    # ── metadata ──
    def databases(self):
        return [r[0] for r in self.rows("SHOW DATABASES")]

    def objects(self, db):
        """Tables, views, routines and triggers of a database."""
        tables = self.rows(
            "SELECT TABLE_NAME, TABLE_TYPE, ENGINE, TABLE_ROWS, "
            "COALESCE(DATA_LENGTH,0)+COALESCE(INDEX_LENGTH,0), TABLE_COLLATION, "
            "CREATE_TIME, UPDATE_TIME, TABLE_COMMENT "
            "FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s ORDER BY TABLE_NAME", (db,))
        out = []
        for name, ttype, engine, nrows, size, coll, created, updated, comment in tables:
            kind = "view" if ttype == "VIEW" else "table"
            out.append({"name": name, "kind": kind, "engine": engine or "", "rows": nrows,
                        "size": size or 0, "collation": coll or "", "created": created,
                        "updated": updated, "comment": comment or ""})
        for name, rtype in self.rows(
                "SELECT ROUTINE_NAME, ROUTINE_TYPE FROM information_schema.ROUTINES "
                "WHERE ROUTINE_SCHEMA=%s ORDER BY ROUTINE_NAME", (db,)):
            out.append({"name": name, "kind": "procedure" if rtype == "PROCEDURE" else "function"})
        for (name,) in self.rows(
                "SELECT TRIGGER_NAME FROM information_schema.TRIGGERS "
                "WHERE TRIGGER_SCHEMA=%s ORDER BY TRIGGER_NAME", (db,)):
            out.append({"name": name, "kind": "trigger"})
        return out

    def columns(self, db, table):
        cols, rows, _ = self.query(f"SHOW FULL COLUMNS FROM {qt(db, table)}", log=False)
        return [dict(zip(cols, r)) for r in rows]

    def indexes(self, db, table):
        cols, rows, _ = self.query(f"SHOW INDEX FROM {qt(db, table)}", log=False)
        result = {}
        for r in rows:
            d = dict(zip(cols, r))
            name = d["Key_name"]
            if name not in result:
                if name == "PRIMARY":
                    itype = "PRIMARY"
                elif d.get("Index_type") == "FULLTEXT":
                    itype = "FULLTEXT"
                elif d.get("Index_type") == "SPATIAL":
                    itype = "SPATIAL"
                else:
                    itype = "INDEX" if d["Non_unique"] else "UNIQUE"
                result[name] = {"name": name, "type": itype, "columns": []}
            col = d["Column_name"]
            if d.get("Sub_part"):
                col += f"({d['Sub_part']})"
            result[name]["columns"].append(col)
        return list(result.values())

    def foreign_keys(self, db, table):
        rows = self.rows(
            "SELECT k.CONSTRAINT_NAME, k.COLUMN_NAME, k.REFERENCED_TABLE_SCHEMA, k.REFERENCED_TABLE_NAME, "
            "k.REFERENCED_COLUMN_NAME, r.UPDATE_RULE, r.DELETE_RULE "
            "FROM information_schema.KEY_COLUMN_USAGE k "
            "JOIN information_schema.REFERENTIAL_CONSTRAINTS r "
            "  ON r.CONSTRAINT_SCHEMA=k.CONSTRAINT_SCHEMA AND r.CONSTRAINT_NAME=k.CONSTRAINT_NAME "
            "WHERE k.TABLE_SCHEMA=%s AND k.TABLE_NAME=%s AND k.REFERENCED_TABLE_NAME IS NOT NULL "
            "ORDER BY k.CONSTRAINT_NAME, k.ORDINAL_POSITION", (db, table))
        result = {}
        for name, col, rdb, rtable, rcol, upd, dele in rows:
            fk = result.setdefault(name, {"name": name, "columns": [], "ref_db": rdb, "ref_table": rtable,
                                          "ref_columns": [], "on_update": upd, "on_delete": dele})
            fk["columns"].append(col)
            fk["ref_columns"].append(rcol)
        return list(result.values())

    def primary_key(self, db, table):
        rows = self.rows(
            "SELECT COLUMN_NAME FROM information_schema.KEY_COLUMN_USAGE "
            "WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s AND CONSTRAINT_NAME='PRIMARY' "
            "ORDER BY ORDINAL_POSITION", (db, table))
        return [r[0] for r in rows]

    def create_code(self, db, name, kind):
        stmt = {"table": "TABLE", "view": "VIEW", "procedure": "PROCEDURE",
                "function": "FUNCTION", "trigger": "TRIGGER"}[kind]
        cols, rows, _ = self.query(f"SHOW CREATE {stmt} {qt(db, name)}", log=False)
        if not rows:
            return ""
        row = dict(zip(cols, rows[0]))
        for key in ("Create Table", "Create View", "Create Procedure", "Create Function",
                    "SQL Original Statement"):
            if row.get(key):
                return row[key]
        return ""

    def column_names(self, db):
        """{table: [columns]} for autocomplete."""
        result = {}
        for table, col in self.rows(
                "SELECT TABLE_NAME, COLUMN_NAME FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA=%s ORDER BY TABLE_NAME, ORDINAL_POSITION", (db,)):
            result.setdefault(table, []).append(col)
        return result

    def collations(self):
        return [r[0] for r in self.rows("SELECT COLLATION_NAME FROM information_schema.COLLATIONS "
                                        "ORDER BY COLLATION_NAME")]

    def engines(self):
        return [r[0] for r in self.rows("SHOW ENGINES") if str(r[1]).upper() in ("YES", "DEFAULT")]


# ── Background workers ─────────────────────────────────────────────────────
class _Signals(QObject):
    done = Signal(object)
    error = Signal(str)
    progress = Signal(int, str)


class Task:
    """Handed to long-running functions so they can report progress and check cancel."""

    def __init__(self, signals):
        self._signals = signals
        self.cancelled = False
        self._last = 0.0

    def progress(self, percent, message=""):
        now = time.monotonic()
        if now - self._last > 0.08 or percent >= 100:
            self._last = now
            self._signals.progress.emit(int(percent), message)


class _Worker(QRunnable):
    def __init__(self, fn, signals, task):
        super().__init__()
        self.fn, self.signals, self.task = fn, signals, task

    def run(self):
        try:
            result = self.fn(self.task) if self.task else self.fn()
        except Exception as exc:  # noqa: BLE001 - reported to the UI
            msg = exc.args[1] if isinstance(exc, pymysql.MySQLError) and len(exc.args) > 1 else str(exc)
            if isinstance(exc, pymysql.MySQLError) and exc.args and isinstance(exc.args[0], int):
                msg = f"SQL Error ({exc.args[0]}): {msg}"
            query_log.message.emit(msg, "error")
            self.signals.error.emit(msg)
        else:
            self.signals.done.emit(result)


class _Activity(QObject):
    """Number of background tasks in flight, for the status-bar busy indicator."""
    changed = Signal(int)

    def __init__(self):
        super().__init__()
        self.count = 0

    def add(self, n):
        self.count = max(0, self.count + n)
        self.changed.emit(self.count)


activity = _Activity()
_alive = set()


def run_async(fn, on_done=None, on_error=None, on_progress=None, quiet=False):
    """Run fn in the thread pool. If fn takes an argument it receives a Task.
    Returns the Task (or None) so callers can cancel. quiet=True keeps periodic
    polling out of the global busy indicator."""
    signals = _Signals()
    try:
        wants_task = len(inspect.signature(fn).parameters) >= 1
    except (TypeError, ValueError):
        wants_task = False
    task = Task(signals) if wants_task else None
    if on_done:
        signals.done.connect(on_done)
    if on_error:
        signals.error.connect(on_error)
    if on_progress:
        signals.progress.connect(on_progress)
    _alive.add(signals)
    signals.done.connect(lambda *_: _alive.discard(signals))
    signals.error.connect(lambda *_: _alive.discard(signals))
    if not quiet:
        activity.add(1)
        signals.done.connect(lambda *_: activity.add(-1))
        signals.error.connect(lambda *_: activity.add(-1))
    QThreadPool.globalInstance().start(_Worker(fn, signals, task))
    return task


def human_size(n):
    n = float(n or 0)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024 or unit == "TiB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024

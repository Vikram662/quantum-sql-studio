"""Database dump (export) and SQL file import."""
import gzip
import io
import os
import re
import time
import zipfile

import pymysql

from . import APP_NAME, APP_VERSION
from .db import format_duration, iter_sql, literal, qi, qt


class Cancelled(Exception):
    pass


def _open_output(path):
    lower = path.lower()
    if lower.endswith(".gz"):
        return gzip.open(path, "wt", encoding="utf-8", newline="\n"), None
    if lower.endswith(".zip"):
        zf = zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED)
        inner = os.path.basename(path)[:-4] or "dump"
        if not inner.lower().endswith(".sql"):
            inner += ".sql"
        raw = zf.open(inner, "w", force_zip64=True)
        return io.TextIOWrapper(raw, encoding="utf-8", newline="\n"), zf
    return open(path, "w", encoding="utf-8", newline="\n"), None


def dump_database(task, conn, db, path, tables=None, options=None):
    """Write an SQL dump. `conn` must be a dedicated DbConnection."""
    opt = {"create_db": False, "drop": True, "structure": True, "data": True, "views": True,
           "routines": True, "triggers": True, "batch_rows": 500}
    opt.update(options or {})
    conn.connect()
    objects = conn.objects(db)
    all_tables = [o["name"] for o in objects if o["kind"] == "table"]
    tables = [t for t in (tables or all_tables) if t in all_tables]
    views = [o["name"] for o in objects if o["kind"] == "view"] if opt["views"] else []
    routines = [o for o in objects if o["kind"] in ("procedure", "function")] if opt["routines"] else []
    triggers = [o["name"] for o in objects if o["kind"] == "trigger"] if opt["triggers"] else []

    counts = {t: next((o["rows"] for o in objects if o["name"] == t), 0) or 0 for t in tables}
    total_rows = max(1, sum(counts.values()) if opt["data"] else 0) + len(tables) + len(views) + 1
    done = 0

    out, zf = _open_output(path)
    try:
        w = out.write
        w(f"-- {APP_NAME} {APP_VERSION} SQL dump\n-- Server: {conn.server_version}\n-- Database: {db}\n"
          f"-- Date: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
          "/*!40101 SET NAMES utf8mb4 */;\n/*!40014 SET @OLD_FOREIGN_KEY_CHECKS=@@FOREIGN_KEY_CHECKS, "
          "FOREIGN_KEY_CHECKS=0 */;\n/*!40101 SET @OLD_SQL_MODE=@@SQL_MODE, SQL_MODE='NO_AUTO_VALUE_ON_ZERO' */;\n"
          "/*!40111 SET @OLD_SQL_NOTES=@@SQL_NOTES, SQL_NOTES=0 */;\n\n")
        if opt["create_db"]:
            _, rows, _ = conn.query(f"SHOW CREATE DATABASE {qi(db)}", log=False)
            create = rows[0][1].replace("CREATE DATABASE", "CREATE DATABASE IF NOT EXISTS", 1)
            w(f"{create};\nUSE {qi(db)};\n\n")

        for table in tables:
            if task.cancelled:
                raise Cancelled()
            task.progress(done * 100 // total_rows, f"Table {table}")
            if opt["structure"]:
                w(f"-- Table structure for {qi(table)}\n")
                if opt["drop"]:
                    w(f"DROP TABLE IF EXISTS {qi(table)};\n")
                create = conn.create_code(db, table, "table")
                if not opt["drop"]:
                    create = create.replace("CREATE TABLE", "CREATE TABLE IF NOT EXISTS", 1)
                w(create + ";\n\n")
            done += 1
            if opt["data"]:
                done = _dump_rows(task, conn, db, table, w, opt["batch_rows"], done, total_rows)

        for view in views:
            task.progress(done * 100 // total_rows, f"View {view}")
            w(f"-- View structure for {qi(view)}\nDROP VIEW IF EXISTS {qi(view)};\n")
            # drop same-database qualifiers so the dump can be restored into another database
            w(conn.create_code(db, view, "view").replace(f"{qi(db)}.", "") + ";\n\n")
            done += 1

        if routines or triggers:
            w("DELIMITER //\n")
            for r in routines:
                kind = r["kind"].upper()
                w(f"DROP {kind} IF EXISTS {qi(r['name'])}//\n{conn.create_code(db, r['name'], r['kind'])}//\n\n")
            for t in triggers:
                w(f"DROP TRIGGER IF EXISTS {qi(t)}//\n{conn.create_code(db, t, 'trigger')}//\n\n")
            w("DELIMITER ;\n\n")

        w("/*!40101 SET SQL_MODE=IFNULL(@OLD_SQL_MODE, '') */;\n"
          "/*!40014 SET FOREIGN_KEY_CHECKS=IFNULL(@OLD_FOREIGN_KEY_CHECKS, 1) */;\n"
          "/*!40111 SET SQL_NOTES=IFNULL(@OLD_SQL_NOTES, 1) */;\n")
    finally:
        out.close()
        if zf:
            zf.close()
        conn.close()
    task.progress(100, "Done")
    return {"tables": len(tables), "views": len(views), "path": path}


def _dump_rows(task, conn, db, table, w, batch_rows, done, total):
    with conn.lock:
        cur = conn.conn.cursor(pymysql.cursors.SSCursor)
        try:
            cur.execute(f"SELECT * FROM {qt(db, table)}")
            cols = ", ".join(qi(d[0]) for d in cur.description)
            header = f"INSERT INTO {qi(table)} ({cols}) VALUES\n"
            batch = []
            wrote_any = False
            while True:
                rows = cur.fetchmany(batch_rows)
                if not rows:
                    break
                if task.cancelled:
                    raise Cancelled()
                if not wrote_any:
                    w(f"-- Data for {qi(table)}\n")
                    wrote_any = True
                batch = ["\t(" + ", ".join(literal(v) for v in r) + ")" for r in rows]
                w(header + ",\n".join(batch) + ";\n")
                done += len(rows)
                task.progress(min(99, done * 100 // total), f"Table {table} — {done:,} rows")
            if wrote_any:
                w("\n")
        finally:
            cur.close()
    return done


def read_sql_file(path):
    lower = path.lower()
    if lower.endswith(".gz"):
        with gzip.open(path, "rb") as f:
            raw = f.read()
    elif lower.endswith(".zip"):
        with zipfile.ZipFile(path) as zf:
            names = [n for n in zf.namelist() if n.lower().endswith(".sql")] or zf.namelist()
            raw = zf.read(names[0])
    else:
        with open(path, "rb") as f:
            raw = f.read()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


COMMIT_EVERY_S = 2.0  # import runs in transactions committed this often, not one commit per statement


_SPACES = re.compile(r"\s+")
_NON_SPACE = re.compile(r"\S")
_ROW_VERB = re.compile(r"\s*(INSERT|REPLACE|UPDATE|DELETE|SELECT|LOAD)\b", re.I)  # rowcount means rows


def _size(n_chars):
    mb = n_chars / 1048576
    return f"{mb:,.1f} MB" if mb >= 1 else f"{n_chars / 1024:,.0f} KB"


def _took(seconds):
    if seconds < 1:
        return f"{seconds * 1000:.0f} ms"
    return f"{seconds:.1f} s" if seconds < 60 else format_duration(seconds)


def _brief(sql, width=90):
    """First words of a statement on one line, for the log."""
    head = _SPACES.sub(" ", sql[:width * 3]).strip()
    return head if len(head) <= width and len(sql) <= width * 3 else head[:width].rstrip() + " …"


def import_sql(task, conn, path, database=None, stop_on_error=True):
    """Execute every statement of an SQL file. Returns a summary dict.

    Statements are split while the import runs (not all up front), so the first
    statement starts within moments even on a huge file, and progress follows the
    position in the file rather than the statement count (dumps mix tiny and huge
    statements). Autocommit is off and work is committed every few seconds, which
    saves the server a disk flush per statement. Each statement is written to
    task.log with its rows, size and time, so the import window can show it live."""
    t0 = time.perf_counter()
    task.progress(0, "Reading file…")
    task.log(f"Reading {os.path.basename(path)} ({_size(os.path.getsize(path))} on disk)…")
    text = read_sql_file(path)
    size = max(1, len(text))
    task.log(f"Read {_size(len(text))} of SQL in {_took(time.perf_counter() - t0)}")
    conn.connect()
    p = conn.params
    task.log(f"Connected to {p.get('user')}@{p.get('host')}:{p.get('port')} (server {conn.server_version})")
    if database:
        conn.use(database)
        task.log(f"Using database {database}")
    task.log(f"Running statements; changes are committed every {COMMIT_EVERY_S:g} s")
    ok, errors, count = 0, [], 0
    line, line_pos = 1, 0  # line number of the current statement, counted incrementally
    try:
        with conn.lock:
            cur = conn.conn.cursor()
            cur.execute("SET autocommit=0")
            last_commit = time.monotonic()
            for sql, start, end in iter_sql(text):
                if task.cancelled:
                    task.log(f"Cancel requested; stopping before statement {count + 1:,}", "warn")
                    raise Cancelled()
                count += 1
                first = _NON_SPACE.search(text, start, end)  # start still holds the previous line's newline
                first = first.start() if first else start
                line += text.count("\n", line_pos, first)
                line_pos = first
                task.progress(start * 100 // size,
                              f"Statement {count:,} · {_size(start)} of {_size(size)}")
                s0 = time.perf_counter()
                try:
                    cur.execute(sql)
                    if cur.description:
                        cur.fetchall()
                    ok += 1
                except pymysql.MySQLError as exc:
                    msg = exc.args[1] if len(exc.args) > 1 else str(exc)
                    errors.append(f"Line {line}: {msg}")
                    task.log(f"#{count:,} line {line:,}: {_brief(sql, 60)}", "error")
                    task.log(f"    {msg}", "error")
                    if stop_on_error:
                        task.log("Stopped at the first error (\"Stop on first error\" is on)", "warn")
                        break
                    continue
                parts = [f"#{count:,}", _brief(sql)]
                if cur.rowcount and cur.rowcount > 0 and _ROW_VERB.match(sql):
                    parts.append(f"{cur.rowcount:,} row{'s' if cur.rowcount != 1 else ''}")
                if len(sql) >= 65536:
                    parts.append(_size(len(sql)))
                parts.append(_took(time.perf_counter() - s0))
                warnings = getattr(getattr(cur, "_result", None), "warning_count", 0) or 0
                if warnings:
                    parts.append(f"{warnings:,} warning{'s' if warnings != 1 else ''}")
                task.log(" · ".join(parts), "warn" if warnings else "info")
                if time.monotonic() - last_commit >= COMMIT_EVERY_S:
                    conn.conn.commit()
                    last_commit = time.monotonic()
            cur.close()
    finally:
        # What ran before an error or Cancel stays applied, as it did with autocommit.
        if conn.conn:
            try:
                conn.conn.commit()
            except pymysql.MySQLError:
                pass
        conn.close()
    seconds = time.perf_counter() - t0
    summary = f"{ok:,} of {count:,} statements executed in {format_duration(seconds)}"
    if errors:
        summary += f" · {len(errors):,} error{'s' if len(errors) != 1 else ''}"
    task.log(f"Finished: {summary}", "warn" if errors else "ok")
    for e in errors[:50]:  # repeated at the end so they are not lost far up the log
        task.log(f"    {e}", "error")
    if len(errors) > 50:
        task.log(f"    … and {len(errors) - 50:,} more errors above", "error")
    task.progress(100, summary)
    return {"ok": ok, "errors": errors, "total": count, "seconds": seconds}

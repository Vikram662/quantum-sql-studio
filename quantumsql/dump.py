"""Database dump (export) and SQL file import."""
import gzip
import io
import os
import time
import zipfile

import pymysql

from . import APP_NAME, APP_VERSION
from .db import literal, qi, qt, split_sql


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


def import_sql(task, conn, path, database=None, stop_on_error=True):
    """Execute every statement of an SQL file. Returns a summary dict."""
    task.progress(0, "Reading file…")
    text = read_sql_file(path)
    task.progress(1, "Parsing statements…")
    stmts = split_sql(text)
    conn.connect()
    if database:
        conn.use(database)
    ok, errors = 0, []
    total = max(1, len(stmts))
    t0 = time.perf_counter()
    try:
        with conn.lock:
            cur = conn.conn.cursor()
            for i, (sql, start, _end) in enumerate(stmts):
                if task.cancelled:
                    raise Cancelled()
                try:
                    cur.execute(sql)
                    if cur.description:
                        cur.fetchall()
                    ok += 1
                except pymysql.MySQLError as exc:
                    line = text.count("\n", 0, start) + 1
                    msg = exc.args[1] if len(exc.args) > 1 else str(exc)
                    errors.append(f"Line {line}: {msg}")
                    if stop_on_error:
                        break
                task.progress(1 + i * 99 // total, f"Statement {i + 1:,} of {len(stmts):,}")
            cur.close()
    finally:
        conn.close()
    task.progress(100, "Done")
    return {"ok": ok, "errors": errors, "total": len(stmts), "seconds": time.perf_counter() - t0}

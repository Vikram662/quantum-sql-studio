"""Code generator: turn a table definition into application code (PHP, Laravel, Node, Python, TS, Go …)."""
import json
import re

from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QFileDialog, QHBoxLayout, QPlainTextEdit,
                               QPushButton, QVBoxLayout)

from . import icons, theme
from .ui import muted


def _base(col_type):
    t = col_type.lower()
    m = re.match(r"([a-z]+)", t)
    return (m.group(1) if m else t), "unsigned" in t, t


def _enum_values(col_type):
    return re.findall(r"'((?:[^']|'')*)'", col_type)


def studly(name):
    return "".join(p[:1].upper() + p[1:] for p in re.split(r"[^0-9a-zA-Z]+", name) if p)


def camel(name):
    s = studly(name)
    return s[:1].lower() + s[1:]


def singular(name):
    for suf, rep in (("ies", "y"), ("sses", "ss"), ("ses", "s"), ("s", "")):
        if name.endswith(suf) and len(name) > len(suf) + 1:
            return name[: -len(suf)] + rep
    return name


class Table:
    def __init__(self, db, name, columns, fks=()):
        self.db, self.name = db, name
        self.cols = []
        for c in columns:
            base, unsigned, full = _base(c["Type"])
            self.cols.append({"name": c["Field"], "type": full, "base": base, "unsigned": unsigned,
                              "null": c.get("Null") == "YES", "key": c.get("Key") or "", "default": c.get("Default"),
                              "extra": (c.get("Extra") or "").lower(), "comment": c.get("Comment") or ""})
        self.fks = list(fks)
        self.pk = [c["name"] for c in self.cols if c["key"] == "PRI"] or [self.cols[0]["name"]]
        self.class_name = studly(singular(name))

    def auto_inc(self):
        return next((c["name"] for c in self.cols if "auto_increment" in c["extra"]), None)

    def writable(self):
        return [c for c in self.cols if "auto_increment" not in c["extra"] and "generated" not in c["extra"]]


def _kind(c):
    b = c["base"]
    if b in ("tinyint",) and c["type"].startswith("tinyint(1)"):
        return "bool"
    if b in ("int", "integer", "smallint", "mediumint", "tinyint", "bigint", "year", "bit"):
        return "int"
    if b in ("decimal", "numeric"):
        return "decimal"
    if b in ("float", "double", "real"):
        return "float"
    if b in ("date",):
        return "date"
    if b in ("datetime", "timestamp"):
        return "datetime"
    if b in ("time",):
        return "time"
    if b == "json":
        return "json"
    if b in ("blob", "tinyblob", "mediumblob", "longblob", "binary", "varbinary"):
        return "bytes"
    if b == "enum":
        return "enum"
    return "string"


# ── generators ───────────────────────────────────────────────────────────────
def gen_php_pdo(t):
    cls = t.class_name + "Repository"
    pk = t.pk[0]
    cols = t.writable()
    names = [c["name"] for c in cols]
    placeholders = ", ".join(f":{n}" for n in names)
    sets = ",\n            ".join(f"`{n}` = :{n}" for n in names if n != pk)
    php_type = {"int": "int", "bool": "bool", "float": "float", "decimal": "string"}
    props = "\n".join(f"    public {'?' if c['null'] else ''}{php_type.get(_kind(c), 'string')} ${camel(c['name'])}"
                      f"{' = null' if c['null'] else ''};" for c in t.cols)
    return f"""<?php
declare(strict_types=1);

/** Row of `{t.name}`. */
final class {t.class_name}
{{
{props}
}}

/** CRUD for `{t.db}`.`{t.name}` using PDO prepared statements. */
final class {cls}
{{
    public function __construct(private PDO $db)
    {{
        $this->db->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);
    }}

    public function find(int|string ${camel(pk)}): ?array
    {{
        $stmt = $this->db->prepare('SELECT * FROM `{t.name}` WHERE `{pk}` = :id LIMIT 1');
        $stmt->execute(['id' => ${camel(pk)}]);
        return $stmt->fetch(PDO::FETCH_ASSOC) ?: null;
    }}

    /** @return array<int, array<string, mixed>> */
    public function all(int $limit = 100, int $offset = 0): array
    {{
        $stmt = $this->db->prepare('SELECT * FROM `{t.name}` ORDER BY `{pk}` LIMIT :limit OFFSET :offset');
        $stmt->bindValue(':limit', $limit, PDO::PARAM_INT);
        $stmt->bindValue(':offset', $offset, PDO::PARAM_INT);
        $stmt->execute();
        return $stmt->fetchAll(PDO::FETCH_ASSOC);
    }}

    public function create(array $data): string
    {{
        $sql = 'INSERT INTO `{t.name}` ({", ".join(f"`{n}`" for n in names)})
                VALUES ({placeholders})';
        $this->db->prepare($sql)->execute(array_intersect_key($data, array_flip({json.dumps(names)})));
        return $this->db->lastInsertId();
    }}

    public function update(int|string ${camel(pk)}, array $data): int
    {{
        $sql = 'UPDATE `{t.name}` SET
            {sets}
            WHERE `{pk}` = :__id';
        $stmt = $this->db->prepare($sql);
        $stmt->execute(array_intersect_key($data, array_flip({json.dumps([n for n in names if n != pk])})) + ['__id' => ${camel(pk)}]);
        return $stmt->rowCount();
    }}

    public function delete(int|string ${camel(pk)}): int
    {{
        $stmt = $this->db->prepare('DELETE FROM `{t.name}` WHERE `{pk}` = :id');
        $stmt->execute(['id' => ${camel(pk)}]);
        return $stmt->rowCount();
    }}
}}
"""


def gen_laravel_model(t):
    fillable = [c["name"] for c in t.writable() if c["name"] not in ("created_at", "updated_at", "deleted_at")]
    casts = {}
    for c in t.cols:
        k = _kind(c)
        if k in ("bool", "json", "datetime", "date") or (k == "decimal"):
            casts[c["name"]] = {"bool": "boolean", "json": "array", "datetime": "datetime", "date": "date",
                                "decimal": "decimal:" + (re.search(r",(\d+)", c["type"]).group(1)
                                                         if re.search(r",(\d+)", c["type"]) else "2")}[k]
    names = {c["name"] for c in t.cols}
    has_ts = {"created_at", "updated_at"} <= names
    soft = "deleted_at" in names
    rels = []
    for fk in t.fks:
        if len(fk["columns"]) == 1:
            rel = camel(singular(fk["ref_table"]))
            rels.append(f"""
    public function {rel}(): BelongsTo
    {{
        return $this->belongsTo({studly(singular(fk['ref_table']))}::class, '{fk['columns'][0]}', '{fk['ref_columns'][0]}');
    }}""")
    uses = ["use Illuminate\\Database\\Eloquent\\Model;"]
    if rels:
        uses.append("use Illuminate\\Database\\Eloquent\\Relations\\BelongsTo;")
    if soft:
        uses.append("use Illuminate\\Database\\Eloquent\\SoftDeletes;")
    casts_php = "".join(f"\n            '{k}' => '{v}'," for k, v in casts.items())
    return f"""<?php

namespace App\\Models;

{chr(10).join(uses)}

class {t.class_name} extends Model
{{{chr(10) + '    use SoftDeletes;' + chr(10) if soft else ''}
    protected $table = '{t.name}';
{"" if t.pk == ["id"] else f"    protected $primaryKey = '{t.pk[0]}';" + chr(10)}{"" if t.auto_inc() else "    public $incrementing = false;" + chr(10)}{"" if has_ts else "    public $timestamps = false;" + chr(10)}
    protected $fillable = [
{chr(10).join(f"        '{n}'," for n in fillable)}
    ];

    protected function casts(): array
    {{
        return [{casts_php}
        ];
    }}
{''.join(rels)}
}}
"""


def gen_laravel_migration(t):
    lines = []
    names = {c["name"] for c in t.cols}
    for c in t.cols:
        n, b, k = c["name"], c["base"], _kind(c)
        if "auto_increment" in c["extra"]:
            line = f"$table->{'id' if n == 'id' and b == 'bigint' else ('increments' if b == 'int' else 'bigIncrements')}"\
                   f"({'' if n == 'id' and b == 'bigint' else repr(n)})"
        elif n in ("created_at", "updated_at") and {"created_at", "updated_at"} <= names:
            if n == "created_at":
                lines.append("$table->timestamps();")
            continue
        elif n == "deleted_at":
            lines.append("$table->softDeletes();")
            continue
        else:
            length = re.search(r"\((\d+)(?:,(\d+))?\)", c["type"])
            method = {"varchar": "string", "char": "char", "text": "text", "mediumtext": "mediumText",
                      "longtext": "longText", "tinytext": "tinyText", "int": "integer", "bigint": "bigInteger",
                      "smallint": "smallInteger", "mediumint": "mediumInteger", "tinyint": "tinyInteger",
                      "decimal": "decimal", "float": "float", "double": "double", "date": "date",
                      "datetime": "dateTime", "timestamp": "timestamp", "time": "time", "year": "year",
                      "json": "json", "blob": "binary", "longblob": "binary", "enum": "enum"}.get(b, "string")
            if k == "bool":
                method = "boolean"
            if c["unsigned"] and method in ("integer", "bigInteger", "smallInteger", "tinyInteger",
                                            "mediumInteger"):
                method = "unsigned" + method[0].upper() + method[1:]
            args = [repr(n)]
            if method == "enum":
                args.append("[" + ", ".join(repr(v) for v in _enum_values(c["type"])) + "]")
            elif method in ("string", "char") and length and length.group(1) != "255":
                args.append(length.group(1))
            elif method == "decimal" and length:
                args += [length.group(1), length.group(2) or "0"]
            line = f"$table->{method}({', '.join(args)})"
            if c["null"]:
                line += "->nullable()"
            if c["default"] is not None:
                d = c["default"]
                if str(d).upper().startswith("CURRENT_TIMESTAMP"):
                    line += "->useCurrent()"
                else:
                    line += f"->default({d if re.fullmatch(r'-?[0-9.]+', str(d)) else repr(str(d))})"
            if c["key"] == "UNI":
                line += "->unique()"
            if c["comment"]:
                line += f"->comment({c['comment']!r})"
        lines.append(line.replace("'", "'") + ";")
    for fk in t.fks:
        lines.append(f"$table->foreign({fk['columns'][0]!r})->references({fk['ref_columns'][0]!r})"
                     f"->on({fk['ref_table']!r})"
                     + (f"->cascadeOnDelete()" if fk.get("on_delete") == "CASCADE" else "") + ";")
    body = "\n            ".join(lines)
    return f"""<?php

use Illuminate\\Database\\Migrations\\Migration;
use Illuminate\\Database\\Schema\\Blueprint;
use Illuminate\\Support\\Facades\\Schema;

return new class extends Migration
{{
    public function up(): void
    {{
        Schema::create('{t.name}', function (Blueprint $table) {{
            {body}
        }});
    }}

    public function down(): void
    {{
        Schema::dropIfExists('{t.name}');
    }}
}};
"""


def gen_node(t):
    pk = t.pk[0]
    names = [c["name"] for c in t.writable()]
    return f"""// npm install mysql2
import mysql from 'mysql2/promise';

export const pool = mysql.createPool({{
  host: process.env.DB_HOST ?? '127.0.0.1',
  user: process.env.DB_USER ?? 'root',
  password: process.env.DB_PASSWORD ?? '',
  database: process.env.DB_NAME ?? '{t.db}',
  connectionLimit: 10,
}});

const COLUMNS = {json.dumps(names)};
const pick = (data) => Object.fromEntries(Object.entries(data).filter(([k]) => COLUMNS.includes(k)));

export const {camel(t.name)} = {{
  async find({camel(pk)}) {{
    const [rows] = await pool.execute('SELECT * FROM `{t.name}` WHERE `{pk}` = ? LIMIT 1', [{camel(pk)}]);
    return rows[0] ?? null;
  }},

  async list(limit = 100, offset = 0) {{
    const [rows] = await pool.query('SELECT * FROM `{t.name}` ORDER BY `{pk}` LIMIT ? OFFSET ?', [limit, offset]);
    return rows;
  }},

  async create(data) {{
    const [res] = await pool.query('INSERT INTO `{t.name}` SET ?', [pick(data)]);
    return res.insertId;
  }},

  async update({camel(pk)}, data) {{
    const [res] = await pool.query('UPDATE `{t.name}` SET ? WHERE `{pk}` = ?', [pick(data), {camel(pk)}]);
    return res.affectedRows;
  }},

  async remove({camel(pk)}) {{
    const [res] = await pool.execute('DELETE FROM `{t.name}` WHERE `{pk}` = ?', [{camel(pk)}]);
    return res.affectedRows;
  }},
}};
"""


def gen_python(t):
    pymap = {"int": "int", "bool": "bool", "float": "float", "decimal": "Decimal", "date": "date",
             "datetime": "datetime", "time": "timedelta", "json": "Any", "bytes": "bytes"}
    fields = []
    for c in sorted(t.cols, key=lambda c: c["null"] or c["default"] is not None or "auto_increment" in c["extra"]):
        typ = pymap.get(_kind(c), "str")
        optional = c["null"] or c["default"] is not None or "auto_increment" in c["extra"]
        fields.append(f"    {c['name']}: {'Optional[' + typ + ']' if optional else typ}"
                      f"{' = None' if optional else ''}")
    names = [c["name"] for c in t.writable()]
    pk = t.pk[0]
    return f'''"""`{t.db}`.`{t.name}` — dataclass and repository (pip install pymysql)."""
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Optional

import pymysql
import pymysql.cursors


@dataclass
class {t.class_name}:
{chr(10).join(fields)}


class {t.class_name}Repository:
    COLUMNS = {names!r}

    def __init__(self, conn: pymysql.connections.Connection):
        self.conn = conn

    def find(self, {pk}) -> Optional[{t.class_name}]:
        with self.conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute("SELECT * FROM `{t.name}` WHERE `{pk}` = %s LIMIT 1", ({pk},))
            row = cur.fetchone()
        return {t.class_name}(**row) if row else None

    def create(self, item: {t.class_name}) -> int:
        data = {{k: v for k, v in asdict(item).items() if k in self.COLUMNS}}
        cols = ", ".join(f"`{{k}}`" for k in data)
        with self.conn.cursor() as cur:
            cur.execute(f"INSERT INTO `{t.name}` ({{cols}}) VALUES ({{', '.join(['%s'] * len(data))}})",
                        tuple(data.values()))
            self.conn.commit()
            return cur.lastrowid

    def update(self, {pk}, **changes) -> int:
        data = {{k: v for k, v in changes.items() if k in self.COLUMNS}}
        sets = ", ".join(f"`{{k}}` = %s" for k in data)
        with self.conn.cursor() as cur:
            n = cur.execute(f"UPDATE `{t.name}` SET {{sets}} WHERE `{pk}` = %s", (*data.values(), {pk}))
            self.conn.commit()
            return n

    def delete(self, {pk}) -> int:
        with self.conn.cursor() as cur:
            n = cur.execute("DELETE FROM `{t.name}` WHERE `{pk}` = %s", ({pk},))
            self.conn.commit()
            return n
'''


def gen_typescript(t):
    tsmap = {"int": "number", "bool": "boolean", "float": "number", "decimal": "string", "date": "string",
             "datetime": "string", "time": "string", "json": "unknown", "bytes": "Buffer"}
    lines = []
    for c in t.cols:
        typ = " | ".join(f"'{v}'" for v in _enum_values(c["type"])) if _kind(c) == "enum" else \
            tsmap.get(_kind(c), "string")
        doc = f"  /** {c['comment']} */\n" if c["comment"] else ""
        lines.append(f"{doc}  {c['name']}: {typ}{' | null' if c['null'] else ''};")
    insert = [c["name"] for c in t.cols if c["null"] or c["default"] is not None or "auto_increment" in c["extra"]]
    return (f"/** Row of `{t.db}`.`{t.name}` */\nexport interface {t.class_name} {{\n" + "\n".join(lines) + "\n}\n\n"
            f"/** Shape accepted when inserting (columns with defaults are optional). */\n"
            f"export type New{t.class_name} = Omit<{t.class_name}, {' | '.join(repr(n) for n in insert) or 'never'}>"
            f" & Partial<Pick<{t.class_name}, {' | '.join(repr(n) for n in insert) or 'never'}>>;\n")


def gen_go(t):
    gomap = {"int": "int64", "bool": "bool", "float": "float64", "decimal": "string", "date": "time.Time",
             "datetime": "time.Time", "time": "string", "json": "json.RawMessage", "bytes": "[]byte"}
    nullable = {"int64": "sql.NullInt64", "bool": "sql.NullBool", "float64": "sql.NullFloat64",
                "string": "sql.NullString", "time.Time": "sql.NullTime"}
    lines = []
    for c in t.cols:
        typ = gomap.get(_kind(c), "string")
        if c["null"]:
            typ = nullable.get(typ, "*" + typ)
        lines.append(f"\t{studly(c['name'])} {typ} `db:\"{c['name']}\" json:\"{c['name']}\"`")
    body = "\n".join(lines)
    imports = sorted({"database/sql" if "sql." in body else "", "time" if "time." in body else "",
                      "encoding/json" if "json.Raw" in body else ""} - {""})
    imp = ("import (\n" + "\n".join(f'\t"{i}"' for i in imports) + "\n)\n\n") if imports else ""
    return f"package models\n\n{imp}// {t.class_name} is a row of `{t.name}`.\ntype {t.class_name} struct {{\n{body}\n}}\n"


def gen_json_schema(t):
    jmap = {"int": "integer", "bool": "boolean", "float": "number", "decimal": "string", "json": "object"}
    props = {}
    for c in t.cols:
        p = {"type": [jmap.get(_kind(c), "string")] + (["null"] if c["null"] else [])}
        if _kind(c) == "enum":
            p["enum"] = _enum_values(c["type"])
        if _kind(c) in ("date", "datetime"):
            p["format"] = "date" if _kind(c) == "date" else "date-time"
        m = re.search(r"char\((\d+)\)", c["type"])
        if m:
            p["maxLength"] = int(m.group(1))
        if c["comment"]:
            p["description"] = c["comment"]
        if len(p["type"]) == 1:
            p["type"] = p["type"][0]
        props[c["name"]] = p
    required = [c["name"] for c in t.cols if not c["null"] and c["default"] is None and
                "auto_increment" not in c["extra"]]
    return json.dumps({"$schema": "https://json-schema.org/draft/2020-12/schema", "title": t.class_name,
                       "type": "object", "properties": props, "required": required,
                       "additionalProperties": False}, indent=2) + "\n"


TARGETS = [
    ("PHP — PDO repository", gen_php_pdo, "php"),
    ("Laravel — Eloquent model", gen_laravel_model, "php"),
    ("Laravel — migration", gen_laravel_migration, "php"),
    ("Node.js — mysql2 CRUD", gen_node, "js"),
    ("Python — dataclass + PyMySQL", gen_python, "py"),
    ("TypeScript — interface", gen_typescript, "ts"),
    ("Go — struct", gen_go, "go"),
    ("JSON Schema", gen_json_schema, "json"),
]


class CodeGenDialog(QDialog):
    def __init__(self, parent, table):
        super().__init__(parent)
        self.table = table
        self.setWindowTitle(f"Generate code — {table.name}")
        self.resize(900, 640)
        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        self.target = QComboBox()
        for label, _fn, _ext in TARGETS:
            self.target.addItem(label)
        self.target.currentIndexChanged.connect(self._render)
        row.addWidget(muted("Generate"))
        row.addWidget(self.target, 1)
        b_copy = QPushButton(icons.icon("copy"), "Copy")
        b_copy.clicked.connect(lambda: QApplication.clipboard().setText(self.code.toPlainText()))
        b_save = QPushButton(icons.icon("save"), "Save as…")
        b_save.clicked.connect(self._save)
        row.addWidget(b_copy)
        row.addWidget(b_save)
        lay.addLayout(row)
        self.code = QPlainTextEdit()
        self.code.setFont(theme.code_font(10))
        self.code.setLineWrapMode(QPlainTextEdit.NoWrap)
        lay.addWidget(self.code, 1)
        self._render()

    def _render(self):
        _label, fn, _ext = TARGETS[self.target.currentIndex()]
        try:
            self.code.setPlainText(fn(self.table))
        except Exception as exc:  # noqa: BLE001 - show generator problems instead of crashing
            self.code.setPlainText(f"// could not generate: {exc}")

    def _save(self):
        label, _fn, ext = TARGETS[self.target.currentIndex()]
        default = {"php": f"{self.table.class_name}.php", "js": f"{camel(self.table.name)}.js",
                   "py": f"{singular(self.table.name)}.py", "ts": f"{camel(singular(self.table.name))}.ts",
                   "go": f"{singular(self.table.name)}.go", "json": f"{self.table.name}.schema.json"}[ext]
        path, _ = QFileDialog.getSaveFileName(self, "Save generated code", default)
        if path:
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(self.code.toPlainText())

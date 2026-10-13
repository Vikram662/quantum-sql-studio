"""SQL beautifier: clause-per-line layout, indented lists, subqueries and boolean conditions."""
import re

from .editor import FUNCTIONS, KEYWORDS

_TOKEN = re.compile(r"""
 (?P<ws>\s+)
|(?P<comment>--[^\n]*|\#[^\n]*|/\*.*?\*/)
|(?P<str>'(?:[^'\\]|\\.|'')*'?|"(?:[^"\\]|\\.|"")*"?)
|(?P<ident>`(?:[^`]|``)*`?)
|(?P<num>0x[0-9a-fA-F]+|\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)
|(?P<var>@@?[\w$.]+)
|(?P<word>[A-Za-z_$][\w$]*)
|(?P<op><=>|<=|>=|<>|!=|:=|\|\||&&|->>|->|[(),;.*+\-/%=<>!~^&|?:])
|(?P<other>.)
""", re.S | re.X)

_KW = set(KEYWORDS) | {"OFFSET", "OVER", "PARTITION", "WINDOW", "RECURSIVE", "LATERAL", "RETURNING", "DUPLICATE",
                       "END", "ASC", "DESC", "NULLS", "FIRST", "LAST", "ALL", "ANY", "SOME", "EXISTS"}
_FN = set(FUNCTIONS)
# Only reserved words are upper-cased: non-reserved ones (USER, STATUS, NAME, DATE …) are often table or
# column names, and table names are case-sensitive on Linux servers.
_UPPER = set("""
ADD ALL ALTER AND AS ASC AUTO_INCREMENT BETWEEN BIGINT BINARY BLOB BY CALL CASCADE CASE CHANGE CHAR CHARACTER
CHECK COLLATE COLUMN CONSTRAINT CREATE CROSS CURRENT_DATE CURRENT_TIME CURRENT_TIMESTAMP DATABASE DECIMAL
DECLARE DEFAULT DELETE DESC DESCRIBE DISTINCT DIV DOUBLE DROP DUPLICATE EACH ELSE ELSEIF END ENGINE EXISTS
EXPLAIN FALSE FLOAT FOR FOREIGN FROM FULLTEXT GRANT GROUP HAVING IF IGNORE IN INDEX INNER INSERT INT INTEGER
INTERVAL INTO IS JOIN KEY LEFT LIKE LIMIT LONGBLOB LONGTEXT MEDIUMBLOB MEDIUMINT MEDIUMTEXT MODIFY NATURAL NOT
NULL NUMERIC OFFSET ON OR ORDER OUTER OVER PARTITION PRIMARY PROCEDURE REFERENCES REGEXP RENAME REPLACE
RESTRICT RIGHT RLIKE ROLLBACK SELECT SET SHOW SMALLINT STRAIGHT_JOIN TABLE THEN TINYBLOB TINYINT TO TRIGGER
TRUE TRUNCATE UNION UNIQUE UNSIGNED UPDATE USE USING VALUES VARBINARY VARCHAR WHEN WHERE WITH XOR ZEROFILL
COMMIT START TRANSACTION BEGIN RECURSIVE ROWS UNBOUNDED PRECEDING FOLLOWING CURRENT
""".split())
_CLAUSES = {"SELECT", "FROM", "WHERE", "HAVING", "LIMIT", "VALUES", "SET", "UPDATE", "INSERT", "DELETE", "REPLACE",
            "WITH", "RETURNING", "WINDOW"}
_BY_CLAUSES = {"GROUP", "ORDER"}
_JOIN_WORDS = {"LEFT", "RIGHT", "INNER", "OUTER", "CROSS", "NATURAL", "FULL", "STRAIGHT_JOIN", "JOIN"}
_LIST_CLAUSES = {"SELECT", "SET", "GROUP BY", "ORDER BY", "VALUES"}
_BOOL_CLAUSES = {"WHERE", "HAVING", "ON"}
_TYPES = {"VARCHAR", "CHAR", "DECIMAL", "NUMERIC", "FLOAT", "DOUBLE", "INT", "INTEGER", "BIGINT", "SMALLINT",
          "TINYINT", "MEDIUMINT", "BINARY", "VARBINARY", "ENUM", "SET", "BIT", "DATETIME", "TIMESTAMP", "TIME",
          "YEAR", "CHARACTER", "VALUES", "IF", "LEFT", "RIGHT", "REPLACE", "INSERT", "DATABASE", "USER", "MOD"}


def _tokens(sql):
    out = []
    for m in _TOKEN.finditer(sql):
        kind = m.lastgroup
        out.append((kind, m.group()))
    return out


class _Frame:
    def __init__(self, base, kind):
        self.base = base        # indent level of clause keywords in this frame
        self.kind = kind        # "top" | "sub" (subquery) | "block" (CREATE TABLE columns) | "inline"
        self.clause = ""
        self.multi = False      # current list clause is laid out one item per line


def format_sql(sql, upper=True, indent="    "):
    toks = _tokens(sql)
    sig = [i for i, (k, _) in enumerate(toks) if k not in ("ws",)]
    lines, cur = [], [""]
    frames = [_Frame(0, "top")]
    prev = ("", "")  # last significant (kind, upper value)
    stmt_first = None

    def flush():
        text = cur[0].rstrip()
        if text.strip():
            lines.append(text)
        cur[0] = ""

    def nl(level):
        flush()
        cur[0] = indent * level

    def put(text, space=True):
        if cur[0].strip() and space and not cur[0].endswith((" ", "(")):
            cur[0] += " "
        cur[0] += text

    def nxt(pos, n=1):
        """Next n significant non-comment token values (upper) after token index pos."""
        vals = []
        for j in sig:
            if j > pos and toks[j][0] != "comment":
                vals.append(toks[j][1].upper())
                if len(vals) == n:
                    break
        return vals

    def list_has_comma(pos):
        """Does the list clause starting after pos contain a top-level comma?"""
        depth = 0
        for j in sig:
            if j <= pos:
                continue
            k, v = toks[j]
            u = v.upper()
            if v == "(":
                depth += 1
            elif v == ")":
                depth -= 1
                if depth < 0:
                    return False
            elif depth == 0:
                if v == ",":
                    return True
                if v == ";" or (k == "word" and (u in _CLAUSES or u in _BY_CLAUSES or u in ("UNION", "JOIN", "ON"))):
                    return False
        return False

    i = 0
    n = len(toks)
    while i < n:
        kind, val = toks[i]
        up = val.upper()
        f = frames[-1]
        breaking = f.kind in ("top", "sub")
        if kind == "ws":
            if "\n" in val and prev[0] == "comment":
                pass
            i += 1
            continue
        if kind == "comment":
            if val.startswith(("--", "#")):
                put(val)
                nl(f.base + (1 if f.clause and breaking else 0))
            else:
                put(val)
            prev = ("comment", up)
            i += 1
            continue
        if stmt_first is None and kind == "word":
            stmt_first = up
        is_call = kind == "word" and up in _FN and nxt(i, 1) == ["("] and _next_index(toks, i) == i + 1
        out = up if (kind == "word" and upper and (up in _UPPER or is_call) and prev[1] != "."
                     and nxt(i, 1) != ["."]) else val
        if kind == "word" and prev[1] == ".":
            put(val, space=False)
            prev = ("ident", up)
            i += 1
            continue

        if kind == "word" and breaking:
            two = nxt(i, 3)
            if up in _BY_CLAUSES and two[:1] == ["BY"]:
                nl(f.base)
                put(f"{out} {'BY' if upper else toks[_next_index(toks, i)][1]}")
                i = _next_index(toks, i) + 1
                f.clause = f"{up} BY"
                f.multi = list_has_comma(i - 1)
                if f.multi:
                    nl(f.base + 1)
                prev = ("word", "BY")
                continue
            if up in _JOIN_WORDS and (up == "JOIN" or "JOIN" in two or up == "STRAIGHT_JOIN"):
                words = [out]
                j = i
                while up != "JOIN" and up != "STRAIGHT_JOIN":
                    j = _next_index(toks, j)
                    up = toks[j][1].upper()
                    words.append(up if upper else toks[j][1])
                nl(f.base)
                put(" ".join(words))
                f.clause = "JOIN"
                f.multi = False
                prev = ("word", "JOIN")
                i = j + 1
                continue
            if up == "UNION":
                nl(f.base)
                words = [out]
                if two[:1] in (["ALL"], ["DISTINCT"]):
                    i = _next_index(toks, i)
                    words.append(toks[i][1].upper() if upper else toks[i][1])
                put(" ".join(words))
                nl(f.base)
                f.clause = ""
                prev = ("word", "UNION")
                i += 1
                continue
            if up == "ON" and f.clause == "JOIN":
                put(out)
                f.clause = "ON"
                prev = ("word", "ON")
                i += 1
                continue
            if up == "ON" and two[:2] == ["DUPLICATE", "KEY"]:
                nl(f.base)
                put(out)
                f.clause = "ON DUPLICATE"
                prev = ("word", "ON")
                i += 1
                continue
            if up in _CLAUSES and not (up == "SET" and prev[1] in ("CHARACTER", "CHARSET")) \
                    and not (up == "UPDATE" and f.clause == "ON DUPLICATE") \
                    and not (up in ("VALUES", "REPLACE", "INSERT", "LEFT", "RIGHT") and two[:1] == ["("]
                             and f.clause not in ("", "INSERT", "REPLACE")) \
                    and not (up == "VALUES" and prev[1] == "(") \
                    and not (up in ("REPLACE", "INSERT") and prev[1] == "("):
                nl(f.base)
                put(out)
                f.clause = up
                f.multi = up in _LIST_CLAUSES and list_has_comma(i)
                if f.multi:
                    nl(f.base + 1)
                prev = ("word", up)
                i += 1
                continue
            if up in ("AND", "OR", "XOR") and f.clause in _BOOL_CLAUSES and prev[1] != "BETWEEN" \
                    and not _in_between(toks, sig, i):
                nl(f.base + 1)
                put(out)
                prev = ("word", up)
                i += 1
                continue

        if val == "(":
            nxt1 = nxt(i, 1)
            if nxt1 and nxt1[0] in ("SELECT", "WITH") and breaking:
                put("(", space=prev[1] not in ("(",))
                frames.append(_Frame(f.base + 1, "sub"))
                nl(frames[-1].base)
            elif breaking and stmt_first == "CREATE" and f.kind == "top" and prev[0] in ("word", "ident") \
                    and f.clause == "" and not cur[0].lstrip().upper().startswith("CREATE INDEX"):
                put("(")
                frames.append(_Frame(f.base + 1, "block"))
                nl(f.base + 1)
            else:
                fn_call = (prev[0] in ("word", "ident")
                           and (prev[1] in _FN or prev[1] in _TYPES or prev[1] not in _KW)
                           and f.clause not in ("INSERT", "REPLACE")
                           and not (f.kind == "block" and prev[1] not in _TYPES))
                put("(", space=not fn_call and prev[1] != "(")
                frames.append(_Frame(f.base, "inline"))
            prev = ("op", "(")
            i += 1
            continue
        if val == ")":
            closing = frames.pop() if len(frames) > 1 else f
            if closing.kind in ("sub", "block"):
                nl(closing.base - 1)
                put(")", space=False)
            else:
                cur[0] = cur[0].rstrip()
                cur[0] += ")"
            prev = ("op", ")")
            i += 1
            continue
        if val == ",":
            cur[0] = cur[0].rstrip() + ","
            if f.kind == "block" or (breaking and f.multi):
                nl(f.base if f.kind == "block" else f.base + 1)
            prev = ("op", ",")
            i += 1
            continue
        if val == ";":
            cur[0] = cur[0].rstrip() + ";"
            flush()
            lines.append("")
            frames = [_Frame(0, "top")]
            stmt_first = None
            prev = ("op", ";")
            i += 1
            continue
        if val == ".":
            cur[0] = cur[0].rstrip() + "."
            prev = ("op", ".")
            i += 1
            continue
        space = prev[1] not in (".", "(", ":") and not (kind == "op" and val == "*" and prev[1] == ".")
        if prev[0] == "op" and prev[1] in ("@",):
            space = False
        put(out, space=space)
        prev = (kind, up if kind == "word" else val)
        i += 1
    flush()
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines) + "\n"


def _next_index(toks, i):
    j = i + 1
    while j < len(toks) and toks[j][0] in ("ws", "comment"):
        j += 1
    return min(j, len(toks) - 1)


def _in_between(toks, sig, i):
    """True when the AND at i belongs to `x BETWEEN a AND b`."""
    depth = 0
    for j in reversed([s for s in sig if s < i]):
        k, v = toks[j]
        u = v.upper()
        if v == ")":
            depth += 1
        elif v == "(":
            if depth == 0:
                return False
            depth -= 1
        elif depth == 0 and k == "word":
            if u == "BETWEEN":
                return True
            if u in ("AND", "OR", "WHERE", "ON", "HAVING", "WHEN", "THEN", "ELSE"):
                return False
    return False


_ROUTINE = re.compile(r"^\s*CREATE\s+(?:OR\s+REPLACE\s+)?(?:DEFINER\s*=\s*\S+\s+)?"
                      r"(?:PROCEDURE|FUNCTION|TRIGGER|EVENT)\b", re.I)


def format_script(text, upper=True):
    """Format a whole editor script. DELIMITER blocks and stored-routine bodies are kept verbatim."""
    out, chunk, delim = [], [], ";"

    def flush():
        body = "\n".join(chunk).strip()
        chunk.clear()
        if not body:
            return
        if delim != ";" or _ROUTINE.match(body):
            out.append(body)
        else:
            out.append(format_sql(body, upper).rstrip())
    for line in text.splitlines():
        m = re.match(r"^\s*DELIMITER\s+(\S+)\s*$", line, re.I)
        if m:
            flush()
            out.append(line.strip())
            delim = m.group(1)
            continue
        chunk.append(line)
    flush()
    return "\n\n".join(out) + "\n"

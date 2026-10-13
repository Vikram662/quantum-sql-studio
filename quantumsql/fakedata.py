"""Fake data generator: fill a table with realistic rows (names, emails, phones, dates, FK ids …)."""
import datetime
import json
import random
import re
import string
import uuid

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDialog, QHBoxLayout, QHeaderView, QLineEdit, QMessageBox, QPushButton,
                               QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout)

from . import icons
from .db import qi, qt, run_async
from .dialogs import run_with_progress
from .grid import DataGrid, ResultModel
from .ui import muted

FIRST = ("Aarav Vivaan Aditya Arjun Sai Reyansh Krishna Ishaan Rohan Kabir Ananya Diya Aadhya Saanvi Myra Kiara "
         "Priya Neha Pooja Riya Rahul Amit Vikram Karan Sneha Kavya Meera Isha Tanvi Nikhil Yash Dev Om Ravi "
         "James Olivia Liam Emma Noah Sophia Lucas Mia Ethan Ava").split()
LAST = ("Sharma Verma Gupta Singh Patel Mehta Jain Agarwal Kumar Reddy Iyer Nair Joshi Chauhan Rathore Shekhawat "
        "Malhotra Kapoor Bansal Saxena Mishra Pandey Yadav Das Bose Smith Johnson Brown Wilson Taylor").split()
CITIES = ("Jaipur Delhi Mumbai Pune Bengaluru Hyderabad Chennai Kolkata Ahmedabad Indore Lucknow Surat Udaipur "
          "Jodhpur Kota Chandigarh Bhopal Nagpur Noida Gurugram").split()
STATES = ("Rajasthan;Delhi;Maharashtra;Karnataka;Telangana;Tamil Nadu;West Bengal;Gujarat;Madhya Pradesh;"
          "Uttar Pradesh;Punjab;Haryana;Kerala").split(";")
COMPANIES = ("Infotech;Solutions;Traders;Enterprises;Technologies;Industries;Labs;Systems;Retail;Foods;"
             "Logistics;Finance").split(";")
WORDS = ("lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod tempor incididunt ut labore et "
         "dolore magna aliqua quick brown fox jumps over lazy dog order product service customer payment "
         "delivery report update review").split()
DOMAINS = ["gmail.com", "yahoo.in", "outlook.com", "example.com", "rediffmail.com", "company.in"]

GENERATORS = ["(skip — use default)", "NULL", "Full name", "First name", "Last name", "Email", "Username",
              "Mobile (India)", "City", "State", "Country", "Address", "Pincode", "Company", "Title (words)",
              "Paragraph", "URL", "IPv4", "UUID", "Integer range", "Decimal range", "Boolean", "Date (range)",
              "Datetime (last 2 years)", "Pick from list", "Foreign key (existing ids)", "Sequence", "JSON object",
              "Password hash", "Slug", "Hex color", "Constant"]


def guess(col, fk_cols):
    name = col["Field"].lower()
    ctype = col["Type"].lower()
    extra = (col.get("Extra") or "").lower()
    if "auto_increment" in extra or "generated" in extra:
        return "(skip — use default)", ""
    if col["Field"] in fk_cols:
        return "Foreign key (existing ids)", ""
    if ctype.startswith("enum") or ctype.startswith("set"):
        return "Pick from list", ",".join(re.findall(r"'((?:[^']|'')*)'", col["Type"]))
    checks = [
        (("email",), "Email"), (("first_name", "firstname", "fname"), "First name"),
        (("last_name", "lastname", "lname", "surname"), "Last name"), (("username", "user_name", "login"), "Username"),
        (("name", "full_name", "fullname", "customer", "contact"), "Full name"),
        (("phone", "mobile", "contact_no", "whatsapp"), "Mobile (India)"), (("city",), "City"),
        (("ip", "ip_address", "ipaddr"), "IPv4"),
        (("state", "province"), "State"), (("country",), "Country"), (("address", "street"), "Address"),
        (("pincode", "pin_code", "zip", "postal"), "Pincode"), (("company", "organisation", "organization"), "Company"),
        (("url", "website", "link"), "URL"), (("uuid", "guid"), "UUID"),
        (("password", "passwd", "pwd_hash"), "Password hash"), (("slug",), "Slug"), (("color", "colour"), "Hex color"),
        (("title", "subject", "headline"), "Title (words)"),
        (("description", "comment", "note", "body", "message", "content", "bio", "remarks"), "Paragraph"),
    ]
    is_text = re.match(r"(var)?char|(tiny|medium|long)?text", ctype)
    for keys, gen in checks:
        if is_text and any(name == k or name.endswith("_" + k) or name.startswith(k + "_") or k in name.split("_")
                           for k in keys):
            return gen, ""
    if ctype.startswith("tinyint(1)") or name.startswith(("is_", "has_")):
        return "Boolean", ""
    if re.match(r"(tiny|small|medium|big)?int", ctype):
        if name in ("age",):
            return "Integer range", "18-70"
        if any(k in name for k in ("phone", "mobile")) and "bigint" in ctype:
            return "Integer range", "6000000000-9999999999"
        return "Integer range", "1-1000"
    if re.match(r"(decimal|float|double|numeric)", ctype):
        return "Decimal range", "10-50000" if any(k in name for k in ("price", "amount", "total", "salary",
                                                                     "cost", "balance", "fee")) else "0-1000"
    if ctype.startswith(("datetime", "timestamp")):
        return "Datetime (last 2 years)", ""
    if ctype.startswith("date"):
        return "Date (range)", "2020-01-01..2026-12-31"
    if ctype.startswith("json") or ctype.startswith("longtext") and "json" in (col.get("Comment") or ""):
        return "JSON object", ""
    if ctype.startswith("year"):
        return "Integer range", "2000-2026"
    if re.match(r"(var)?char|text|tinytext|mediumtext|longtext", ctype):
        return "Title (words)", ""
    if re.match(r"(var)?binary|blob", ctype):
        return "(skip — use default)", ""
    return "(skip — use default)", ""


def _range(opt, default, cast):
    try:
        a, b = re.split(r"\s*(?:-|\.\.|,)\s*", opt.strip(), maxsplit=1)
        return cast(a), cast(b)
    except (ValueError, AttributeError):
        return default


def make_value(gen, opt, col, i, rnd, ctx):
    ctype = col["Type"].lower()
    first, last = rnd.choice(FIRST), rnd.choice(LAST)
    if gen == "NULL":
        return None
    if gen == "Full name":
        return f"{first} {last}"
    if gen == "First name":
        return first
    if gen == "Last name":
        return last
    if gen == "Email":
        return f"{first.lower()}.{last.lower()}{i}@{rnd.choice(DOMAINS)}"
    if gen == "Username":
        return f"{first.lower()}{last.lower()[:3]}{i}"
    if gen == "Mobile (India)":
        return f"+91 {rnd.choice('6789')}{rnd.randint(100000000, 999999999)}"
    if gen == "City":
        return rnd.choice(CITIES)
    if gen == "State":
        return rnd.choice(STATES)
    if gen == "Country":
        return rnd.choice(["India"] * 6 + ["United States", "United Kingdom", "UAE", "Singapore"])
    if gen == "Address":
        return f"{rnd.randint(1, 999)}, {rnd.choice(['MG Road', 'Station Road', 'Civil Lines', 'Sector 21', 'Park Street', 'Malviya Nagar'])}, {rnd.choice(CITIES)}"
    if gen == "Pincode":
        return str(rnd.randint(110001, 855999))
    if gen == "Company":
        return f"{last} {rnd.choice(COMPANIES)}"
    if gen == "Title (words)":
        return " ".join(rnd.choice(WORDS) for _ in range(rnd.randint(2, 5))).capitalize()
    if gen == "Paragraph":
        return " ".join(rnd.choice(WORDS) for _ in range(rnd.randint(12, 40))).capitalize() + "."
    if gen == "URL":
        return f"https://www.{last.lower()}{rnd.choice(COMPANIES).lower()}.com/{rnd.choice(WORDS)}"
    if gen == "IPv4":
        return ".".join(str(rnd.randint(1, 254)) for _ in range(4))
    if gen == "UUID":
        return str(uuid.UUID(int=rnd.getrandbits(128), version=4))
    if gen == "Integer range":
        lo, hi = _range(opt, (1, 1000), int)
        return rnd.randint(min(lo, hi), max(lo, hi))
    if gen == "Decimal range":
        lo, hi = _range(opt, (0.0, 1000.0), float)
        scale = re.search(r",(\d+)\)", ctype)
        return round(rnd.uniform(lo, hi), int(scale.group(1)) if scale else 2)
    if gen == "Boolean":
        return rnd.randint(0, 1)
    if gen == "Date (range)":
        a, b = _range(opt, ("2020-01-01", "2026-12-31"), str)
        try:
            d0, d1 = datetime.date.fromisoformat(a), datetime.date.fromisoformat(b)
        except ValueError:
            d0, d1 = datetime.date(2020, 1, 1), datetime.date(2026, 12, 31)
        return d0 + datetime.timedelta(days=rnd.randint(0, max(0, (d1 - d0).days)))
    if gen == "Datetime (last 2 years)":
        return (datetime.datetime.now() - datetime.timedelta(seconds=rnd.randint(0, 730 * 86400))).replace(
            microsecond=0)
    if gen == "Pick from list":
        items = [x.strip() for x in opt.split(",") if x.strip()] or ["a", "b", "c"]
        return rnd.choice(items)
    if gen == "Foreign key (existing ids)":
        ids = ctx.get(col["Field"]) or [None]
        return rnd.choice(ids)
    if gen == "Sequence":
        start = int(opt) if opt.strip().lstrip("-").isdigit() else 1
        return start + i
    if gen == "JSON object":
        return json.dumps({"source": rnd.choice(["web", "app", "api"]), "score": rnd.randint(1, 100)})
    if gen == "Password hash":
        return "$2y$10$" + "".join(rnd.choice(string.ascii_letters + string.digits + "./") for _ in range(53))
    if gen == "Slug":
        return "-".join(rnd.choice(WORDS) for _ in range(3)) + f"-{i}"
    if gen == "Hex color":
        return f"#{rnd.randint(0, 0xFFFFFF):06x}"
    if gen == "Constant":
        return opt
    return None


def _fit(value, col, unique, i):
    if isinstance(value, str):
        m = re.search(r"char\((\d+)\)", col["Type"].lower())
        if unique and col.get("Key") in ("UNI", "PRI"):
            value = f"{value}" if str(i) in value else f"{value}-{i}"
        if m:
            value = value[: int(m.group(1))]
    return value


class FakeDataDialog(QDialog):
    def __init__(self, main, session, db, table):
        super().__init__(main)
        self.main, self.session, self.db, self.table = main, session, db, table
        self.setWindowTitle(f"Generate test data — {db}.{table}")
        self.resize(980, 640)
        self.cols, self.fks = [], []
        lay = QVBoxLayout(self)
        lay.addWidget(muted("Each column gets a generator guessed from its name and type — change any of them. "
                            "Foreign keys pick random existing ids from the referenced table."))
        self.grid = QTableWidget(0, 4)
        self.grid.setHorizontalHeaderLabels(["Column", "Type", "Generator", "Options (range a-b, list a,b,c …)"])
        self.grid.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.grid.verticalHeader().setVisible(False)
        lay.addWidget(self.grid, 3)
        self.preview = DataGrid()
        lay.addWidget(self.preview, 2)
        row = QHBoxLayout()
        self.count = QSpinBox()
        self.count.setRange(1, 5_000_000)
        self.count.setValue(1000)
        self.count.setSingleStep(1000)
        self.count.setSuffix(" rows")
        b_prev = QPushButton(icons.icon("view"), "Preview")
        b_prev.clicked.connect(self._preview)
        self.b_go = QPushButton(icons.icon("bolt"), "Insert rows")
        self.b_go.setProperty("primary", True)
        self.b_go.clicked.connect(self._insert)
        b_close = QPushButton("Close")
        b_close.clicked.connect(self.reject)
        row.addWidget(muted("Insert"))
        row.addWidget(self.count)
        row.addStretch(1)
        row.addWidget(b_prev)
        row.addWidget(b_close)
        row.addWidget(self.b_go)
        lay.addLayout(row)
        conn = session.conn
        run_async(lambda: (conn.columns(db, table), conn.foreign_keys(db, table)), self._loaded,
                  lambda e: QMessageBox.critical(self, "Test data", e))

    def _loaded(self, res):
        self.cols, self.fks = res
        fk_cols = {fk["columns"][0]: fk for fk in self.fks if len(fk["columns"]) == 1}
        self.grid.setRowCount(len(self.cols))
        for r, c in enumerate(self.cols):
            gen, opt = guess(c, fk_cols)
            name = QTableWidgetItem(c["Field"])
            name.setFlags(name.flags() & ~Qt.ItemIsEditable)
            typ = QTableWidgetItem(c["Type"] + (" · " + c["Extra"] if c.get("Extra") else ""))
            typ.setFlags(typ.flags() & ~Qt.ItemIsEditable)
            combo = QComboBox()
            combo.addItems(GENERATORS)
            combo.setCurrentText(gen)
            self.grid.setItem(r, 0, name)
            self.grid.setItem(r, 1, typ)
            self.grid.setCellWidget(r, 2, combo)
            self.grid.setCellWidget(r, 3, QLineEdit(opt))
        self.grid.resizeColumnsToContents()
        self.grid.setColumnWidth(2, 220)
        self._preview()

    def _plan(self):
        plan = []
        for r, c in enumerate(self.cols):
            gen = self.grid.cellWidget(r, 2).currentText()
            if gen.startswith("(skip"):
                continue
            plan.append((c, gen, self.grid.cellWidget(r, 3).text()))
        return plan

    def _fk_values(self, conn, plan):
        ctx = {}
        fk_cols = {fk["columns"][0]: fk for fk in self.fks if len(fk["columns"]) == 1}
        for c, gen, _opt in plan:
            if gen == "Foreign key (existing ids)" and c["Field"] in fk_cols:
                fk = fk_cols[c["Field"]]
                ctx[c["Field"]] = [r[0] for r in conn.rows(
                    f"SELECT {qi(fk['ref_columns'][0])} FROM {qt(fk['ref_db'] or self.db, fk['ref_table'])} "
                    f"LIMIT 20000")]
        return ctx

    def _rows(self, plan, n, ctx, start=0, seed=None):
        rnd = random.Random(seed)
        return [[_fit(make_value(gen, opt, c, i, rnd, ctx), c, True, i) for c, gen, opt in plan]
                for i in range(start, start + n)]

    def _preview(self):
        plan = self._plan()
        if not plan:
            return
        conn, ctx_fn = self.session.conn, self._fk_values
        run_async(lambda: ctx_fn(conn, plan),
                  lambda ctx: self.preview.set_model(ResultModel([c["Field"] for c, _, _ in plan],
                                                                 self._rows(plan, 10, ctx))),
                  lambda e: QMessageBox.critical(self, "Preview", e))

    def _insert(self):
        plan = self._plan()
        if not plan:
            return
        total = self.count.value()
        conn = self.session.conn.clone()
        target = qt(self.db, self.table)
        cols = ", ".join(qi(c["Field"]) for c, _, _ in plan)
        sql = f"INSERT INTO {target} ({cols}) VALUES ({', '.join(['%s'] * len(plan))})"
        offset_seed = random.randint(0, 10 ** 9)

        def work(task):
            try:
                conn.connect()
                ctx = self._fk_values(conn, plan)
                done = 0
                base = conn.rows(f"SELECT COUNT(*) FROM {target}")[0][0]
                with conn.lock:
                    cur = conn.conn.cursor()
                    while done < total:
                        if task.cancelled:
                            raise RuntimeError(f"Cancelled after {done:,} rows (those rows were kept).")
                        n = min(1000, total - done)
                        rows = self._rows(plan, n, ctx, start=base + done + 1, seed=offset_seed + done)
                        conn.conn.begin()
                        cur.executemany(sql, rows)
                        conn.conn.commit()
                        done += n
                        task.progress(done * 100 // total, f"Inserted {done:,} of {total:,} rows")
                    cur.close()
                return done
            finally:
                conn.close()

        n, err = run_with_progress(self, "Generating test data", work)
        if err:
            QMessageBox.warning(self, "Test data", err)
        else:
            self.main.status(f"Inserted {n:,} generated rows into {self.table}.")
            QMessageBox.information(self, "Test data", f"Inserted {n:,} rows into {self.table}.")
            if self.main.data_tab.table == self.table:
                self.main.data_tab.refresh()

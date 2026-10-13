"""ER diagram: tables as draggable boxes with foreign-key connectors; export to PNG / SVG."""
from collections import defaultdict

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QImage, QPainter, QPainterPath, QPen
from PySide6.QtSvg import QSvgGenerator
from PySide6.QtWidgets import (QCheckBox, QDialog, QFileDialog, QGraphicsItem, QGraphicsPathItem, QGraphicsScene,
                               QGraphicsView, QLineEdit, QVBoxLayout)

from .db import run_async
from .theme import T
from .ui import bar, muted, tool

ROW_H = 18
HEAD_H = 26
WIDTH_MIN = 170


def load_model(conn, db):
    cols = defaultdict(list)
    for table, col, ctype, key, nullable in conn.rows(
            "SELECT c.TABLE_NAME, c.COLUMN_NAME, c.COLUMN_TYPE, c.COLUMN_KEY, c.IS_NULLABLE "
            "FROM information_schema.COLUMNS c JOIN information_schema.TABLES t ON t.TABLE_SCHEMA=c.TABLE_SCHEMA "
            "AND t.TABLE_NAME=c.TABLE_NAME WHERE c.TABLE_SCHEMA=%s AND t.TABLE_TYPE='BASE TABLE' "
            "ORDER BY c.TABLE_NAME, c.ORDINAL_POSITION", (db,)):
        cols[table].append({"name": col, "type": ctype, "key": key, "null": nullable == "YES"})
    fks = []
    for table, col, rtable, rcol, name in conn.rows(
            "SELECT TABLE_NAME, COLUMN_NAME, REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME, CONSTRAINT_NAME "
            "FROM information_schema.KEY_COLUMN_USAGE WHERE TABLE_SCHEMA=%s AND REFERENCED_TABLE_NAME IS NOT NULL "
            "AND REFERENCED_TABLE_SCHEMA=%s", (db, db)):
        fks.append({"table": table, "column": col, "ref_table": rtable, "ref_column": rcol, "name": name})
    return dict(cols), fks


class TableItem(QGraphicsItem):
    def __init__(self, name, columns, fk_cols, font):
        super().__init__()
        self.name, self.columns, self.fk_cols = name, columns, fk_cols
        self.font = font
        self.bold = QFont(font)
        self.bold.setBold(True)
        fm, bm = QFontMetrics(font), QFontMetrics(self.bold)
        w = max([bm.horizontalAdvance(name) + 30] +
                [fm.horizontalAdvance(c["name"]) + fm.horizontalAdvance(c["type"]) + 50 for c in columns])
        self.w = max(WIDTH_MIN, min(w, 380))
        self.h = HEAD_H + ROW_H * len(columns) + 6
        self.edges = []
        self.setFlags(QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemIsSelectable |
                      QGraphicsItem.ItemSendsGeometryChanges)
        self.setCursor(Qt.OpenHandCursor)
        self.setZValue(1)

    def boundingRect(self):
        return QRectF(-1, -1, self.w + 2, self.h + 2)

    def column_y(self, col):
        for i, c in enumerate(self.columns):
            if c["name"] == col:
                return HEAD_H + ROW_H * i + ROW_H / 2
        return HEAD_H / 2

    def paint(self, p, _opt, _w=None):
        p.setRenderHint(QPainter.Antialiasing)
        sel = self.isSelected()
        p.setPen(QPen(QColor(T["accent"] if sel else T["border_strong"]), 2 if sel else 1))
        p.setBrush(QColor(T["panel"]))
        p.drawRoundedRect(QRectF(0, 0, self.w, self.h), 6, 6)
        head = QPainterPath()
        head.addRoundedRect(QRectF(0, 0, self.w, HEAD_H), 6, 6)
        head.addRect(QRectF(0, HEAD_H - 6, self.w, 6))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(T["ic_table"]))
        p.drawPath(head.simplified())
        p.setFont(self.bold)
        p.setPen(QColor("#ffffff"))
        p.drawText(QRectF(10, 0, self.w - 20, HEAD_H), Qt.AlignVCenter | Qt.AlignLeft, self.name)
        p.setFont(self.font)
        fm = QFontMetrics(self.font)
        for i, c in enumerate(self.columns):
            y = HEAD_H + ROW_H * i + 3
            if c["key"] == "PRI":
                p.setPen(QColor(T["ic_key"]))
                p.drawText(QRectF(6, y, 16, ROW_H), Qt.AlignVCenter, "PK")
            elif c["name"] in self.fk_cols:
                p.setPen(QColor(T["ic_view"]))
                p.drawText(QRectF(6, y, 16, ROW_H), Qt.AlignVCenter, "FK")
            p.setPen(QColor(T["text"] if c["key"] == "PRI" or not c["null"] else T["muted"]))
            name_w = self.w - 30 - min(fm.horizontalAdvance(c["type"]), self.w * 0.45) - 10
            p.drawText(QRectF(28, y, name_w, ROW_H), Qt.AlignVCenter,
                       fm.elidedText(c["name"], Qt.ElideRight, int(name_w)))
            p.setPen(QColor(T["faint"]))
            p.drawText(QRectF(self.w * 0.55 - 10, y, self.w * 0.45, ROW_H), Qt.AlignVCenter | Qt.AlignRight,
                       fm.elidedText(c["type"], Qt.ElideRight, int(self.w * 0.45)))

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionHasChanged:
            for e in self.edges:
                e.adjust()
        return super().itemChange(change, value)


class EdgeItem(QGraphicsPathItem):
    def __init__(self, src, src_col, dst, dst_col):
        super().__init__()
        self.src, self.src_col, self.dst, self.dst_col = src, src_col, dst, dst_col
        self.setPen(QPen(QColor(T["muted"]), 1.4))
        self.setZValue(0)
        self.setToolTip(f"{src.name}.{src_col} → {dst.name}.{dst_col}")
        src.edges.append(self)
        dst.edges.append(self)
        self.adjust()

    def adjust(self):
        s, d = self.src, self.dst
        sy = s.pos().y() + s.column_y(self.src_col)
        dy = d.pos().y() + d.column_y(self.dst_col)
        if s is d:
            x = s.pos().x() + s.w
            path = QPainterPath(QPointF(x, sy))
            path.cubicTo(x + 40, sy, x + 40, dy, x, dy)
            self.setPath(path)
            return
        if s.pos().x() + s.w / 2 < d.pos().x() + d.w / 2:
            sx, dx, k = s.pos().x() + s.w, d.pos().x(), 1
        else:
            sx, dx, k = s.pos().x(), d.pos().x() + d.w, -1
        bend = max(40, abs(dx - sx) / 2)
        path = QPainterPath(QPointF(sx, sy))
        path.cubicTo(sx + k * bend, sy, dx - k * bend, dy, dx, dy)
        # crow's foot on the "many" side (src), bar on the "one" side (dst)
        path.moveTo(sx, sy - 5)
        path.lineTo(sx + k * 10, sy)
        path.lineTo(sx, sy + 5)
        path.moveTo(dx - k * 8, dy - 5)
        path.lineTo(dx - k * 8, dy + 5)
        self.setPath(path)


class ErView(QGraphicsView):
    def __init__(self, scene):
        super().__init__(scene)
        self.setRenderHint(QPainter.Antialiasing)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setBackgroundBrush(QColor(T["window"]))

    def wheelEvent(self, e):
        f = 1.15 if e.angleDelta().y() > 0 else 1 / 1.15
        self.scale(f, f)


def layout(items, fks):
    """Layer tables by FK depth (referenced tables to the left), stack each layer vertically."""
    deps = defaultdict(set)
    for fk in fks:
        if fk["table"] in items and fk["ref_table"] in items and fk["table"] != fk["ref_table"]:
            deps[fk["table"]].add(fk["ref_table"])
    level = {}

    def depth(t, seen=()):
        if t in level:
            return level[t]
        if t in seen:
            return 0
        level[t] = 0 if not deps[t] else 1 + max(depth(r, seen + (t,)) for r in deps[t])
        return level[t]
    linked = {fk["table"] for fk in fks} | {fk["ref_table"] for fk in fks}
    for t in items:
        if t in linked:
            depth(t)
    cols = defaultdict(list)
    for t in sorted(items):
        if t in level:
            cols[level[t]].append(t)
    x = 0.0
    for lv in sorted(cols):
        y = 0.0
        width = max(items[t].w for t in cols[lv])
        for t in cols[lv]:
            items[t].setPos(x, y)
            y += items[t].h + 30
        x += width + 90
    # unrelated tables in a grid to the right
    loose = [t for t in sorted(items) if t not in level]
    if loose:
        per_col = max(1, int(len(loose) ** 0.5))
        start_x = x
        for i, t in enumerate(loose):
            c, r = divmod(i, per_col)
            if r == 0:
                y = 0.0
                if c:
                    start_x += max(items[loose[j]].w for j in range((c - 1) * per_col,
                                                                    min(len(loose), c * per_col))) + 60
            items[t].setPos(start_x, y)
            y += items[t].h + 30


class ErDiagramDialog(QDialog):
    def __init__(self, main, session, db):
        super().__init__(main)
        self.setWindowTitle(f"ER diagram — {db}")
        self.resize(1300, 820)
        self.db = db
        self.scene = QGraphicsScene(self)
        self.view = ErView(self.scene)
        self.items, self.fks, self.cols = {}, [], {}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Show tables matching…")
        self.filter.setClearButtonEnabled(True)
        self.filter.textChanged.connect(lambda *_: self._build())
        self.only_linked = QCheckBox("Only tables with relations")
        self.only_linked.toggled.connect(lambda *_: self._build())
        self.info = muted("Loading…")
        lay.addWidget(bar(self.filter, self.only_linked, "|",
                          tool("refresh", "Re-arrange tables", lambda: (layout(self.items, self.fks),
                                                                        self._edges_adjust()), "Auto layout"),
                          tool("search", "Fit everything in view", self.fit, "Fit"),
                          tool("export", "Export as PNG", lambda: self.export("png"), "PNG"),
                          tool("export", "Export as SVG", lambda: self.export("svg"), "SVG"),
                          None, self.info))
        self.filter.setMaximumWidth(260)
        lay.addWidget(self.view, 1)
        run_async(lambda: load_model(session.conn, db), self._loaded,
                  lambda e: self.info.setText(f"Could not load: {e}"))

    def _loaded(self, res):
        self.cols, self.fks = res
        self._build()

    def _build(self):
        self.scene.clear()
        self.items = {}
        text = self.filter.text().lower().strip()
        linked = {fk["table"] for fk in self.fks} | {fk["ref_table"] for fk in self.fks}
        font = QFont(self.font())
        font.setPointSizeF(8.5)
        fk_cols = defaultdict(set)
        for fk in self.fks:
            fk_cols[fk["table"]].add(fk["column"])
        for t, cols in self.cols.items():
            if text and text not in t.lower():
                continue
            if self.only_linked.isChecked() and t not in linked:
                continue
            item = TableItem(t, cols, fk_cols[t], font)
            self.scene.addItem(item)
            self.items[t] = item
        layout(self.items, self.fks)
        n_edges = 0
        for fk in self.fks:
            if fk["table"] in self.items and fk["ref_table"] in self.items:
                self.scene.addItem(EdgeItem(self.items[fk["table"]], fk["column"], self.items[fk["ref_table"]],
                                            fk["ref_column"]))
                n_edges += 1
        self.info.setText(f"{len(self.items)} tables · {n_edges} relations · drag tables, wheel to zoom")
        self.fit()

    def _edges_adjust(self):
        for it in self.scene.items():
            if isinstance(it, EdgeItem):
                it.adjust()
        self.fit()

    def fit(self):
        r = self.scene.itemsBoundingRect().adjusted(-30, -30, 30, 30)
        self.scene.setSceneRect(r)
        self.view.fitInView(r, Qt.KeepAspectRatio)
        if self.view.transform().m11() > 1.2:
            self.view.resetTransform()

    def export(self, fmt):
        path, _ = QFileDialog.getSaveFileName(self, "Export ER diagram", f"{self.db}_er.{fmt}",
                                              "PNG image (*.png)" if fmt == "png" else "SVG image (*.svg)")
        if path:
            self.render_to(path, fmt)

    def render_to(self, path, fmt):
        r = self.scene.itemsBoundingRect().adjusted(-20, -20, 20, 20)
        self.scene.clearSelection()
        if fmt == "svg":
            gen = QSvgGenerator()
            gen.setFileName(path)
            gen.setSize(QSize(int(r.width()), int(r.height())))
            gen.setViewBox(QRectF(0, 0, r.width(), r.height()))
            gen.setTitle(f"{self.db} ER diagram")
            p = QPainter(gen)
        else:
            scale = 2 if r.width() * r.height() < 40_000_000 else 1
            img = QImage(int(r.width() * scale), int(r.height() * scale), QImage.Format_ARGB32)
            img.fill(QColor(T["window"]))
            p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing)
        self.scene.render(p, QRectF(), r)
        p.end()
        if fmt == "png":
            img.save(path)

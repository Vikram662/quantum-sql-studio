"""Saved queries: a library of named queries in folders, shareable as JSON."""
import json
import os
import time
import uuid

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
                               QLineEdit, QMenu, QMessageBox, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from . import icons
from .config import _read_json, _write_json, data_dir
from .ui import bar, confirm, tool


class SavedQueries:
    def __init__(self):
        self.path = os.path.join(data_dir(), "saved_queries.json")
        self.items = _read_json(self.path, [])

    def save(self):
        try:
            _write_json(self.path, self.items)
        except OSError:
            pass

    def folders(self):
        return sorted({q.get("folder") or "" for q in self.items} - {""}, key=str.lower)

    def add(self, name, sql, folder="", db=""):
        now = time.strftime("%Y-%m-%d %H:%M")
        for q in self.items:
            if q["name"] == name and (q.get("folder") or "") == folder:
                q.update(sql=sql, db=db, updated=now)
                self.save()
                return q
        q = {"id": uuid.uuid4().hex, "name": name, "folder": folder, "sql": sql, "db": db, "created": now,
             "updated": now}
        self.items.append(q)
        self.save()
        return q

    def remove(self, qid):
        self.items = [q for q in self.items if q["id"] != qid]
        self.save()

    def get(self, qid):
        return next((q for q in self.items if q["id"] == qid), None)


library = SavedQueries()


class SaveQueryDialog(QDialog):
    def __init__(self, parent, name="", folder=""):
        super().__init__(parent)
        self.setWindowTitle("Save query")
        self.resize(460, 140)
        form = QFormLayout(self)
        self.name = QLineEdit(name)
        self.name.setPlaceholderText("e.g. Daily sales report")
        self.folder = QComboBox()
        self.folder.setEditable(True)
        self.folder.addItem("")
        self.folder.addItems(library.folders())
        self.folder.setCurrentText(folder)
        self.folder.lineEdit().setPlaceholderText("optional folder, e.g. Reports")
        form.addRow("Name", self.name)
        form.addRow("Folder", self.folder)
        box = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        box.accepted.connect(self.accept)
        box.rejected.connect(self.reject)
        form.addRow(box)


class SavedQueriesPanel(QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Search saved queries…")
        self.filter.setClearButtonEnabled(True)
        self.filter.textChanged.connect(self.reload)
        lay.addWidget(bar(self.filter,
                          tool("save", "Save the current query tab (Ctrl+D)", main.save_query_to_library),
                          tool("import", "Import queries from a JSON file (shared by your team)", self.import_json),
                          tool("export", "Export all saved queries to a JSON file", self.export_json),
                          margins=(6, 4, 6, 4)))
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.itemDoubleClicked.connect(lambda it, _c: self.open(it))
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._menu)
        lay.addWidget(self.tree, 1)
        self.reload()

    def reload(self, *_):
        text = self.filter.text().lower().strip()
        self.tree.clear()
        folders = {}
        for q in sorted(library.items, key=lambda q: ((q.get("folder") or "").lower(), q["name"].lower())):
            if text and text not in q["name"].lower() and text not in q["sql"].lower():
                continue
            f = q.get("folder") or ""
            parent = self.tree.invisibleRootItem()
            if f:
                if f not in folders:
                    folders[f] = QTreeWidgetItem([f])
                    folders[f].setIcon(0, icons.icon("folder"))
                    self.tree.addTopLevelItem(folders[f])
                    folders[f].setExpanded(True)
                parent = folders[f]
            it = QTreeWidgetItem([q["name"]])
            it.setIcon(0, icons.icon("star"))
            it.setData(0, Qt.UserRole, q["id"])
            it.setToolTip(0, f"{q.get('db') or ''}  ·  updated {q.get('updated', '')}\n\n{q['sql'][:1500]}")
            parent.addChild(it)

    def _query(self, item):
        return library.get(item.data(0, Qt.UserRole)) if item else None

    def open(self, item, run=False):
        q = self._query(item)
        if not q:
            return
        tab = self.main.new_query(None, q.get("db") or None, q["sql"])
        if tab:
            tab.saved_query_id = q["id"]
            if run:
                tab.run_all()

    def _menu(self, pos):
        item = self.tree.itemAt(pos)
        q = self._query(item)
        if not q:
            return
        menu = QMenu(self)
        menu.addAction(icons.icon("query"), "Open in new tab", lambda: self.open(item))
        menu.addAction(icons.icon("play"), "Open and run", lambda: self.open(item, run=True))
        menu.addAction(icons.icon("copy"), "Copy SQL", lambda: QApplication.clipboard().setText(q["sql"]))
        menu.addSeparator()
        menu.addAction(icons.icon("edit"), "Rename / move…", lambda: self._rename(q))
        menu.addAction(icons.icon("trash"), "Delete", lambda: self._delete(q))
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    def _rename(self, q):
        dlg = SaveQueryDialog(self, q["name"], q.get("folder") or "")
        if dlg.exec() and dlg.name.text().strip():
            q["name"] = dlg.name.text().strip()
            q["folder"] = dlg.folder.currentText().strip()
            library.save()
            self.reload()

    def _delete(self, q):
        if confirm(self, "Delete saved query", f"Delete “{q['name']}”?"):
            library.remove(q["id"])
            self.reload()

    def export_json(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export saved queries", "quantum-sql-queries.json",
                                              "JSON (*.json)")
        if path:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"app": "Quantum SQL", "queries": library.items}, f, ensure_ascii=False, indent=2)
            self.main.status(f"Exported {len(library.items)} queries to {path}")

    def import_json(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import saved queries", "", "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            items = data.get("queries", data) if isinstance(data, dict) else data
            n = 0
            for q in items:
                if isinstance(q, dict) and q.get("name") and q.get("sql"):
                    library.add(q["name"], q["sql"], q.get("folder") or "", q.get("db") or "")
                    n += 1
        except (OSError, ValueError, AttributeError) as exc:
            QMessageBox.critical(self, "Import", f"Could not import: {exc}")
            return
        self.reload()
        self.main.status(f"Imported {n} queries.")

    def palette_entries(self):
        return [("star", q["name"], f"saved query{' · ' + q['folder'] if q.get('folder') else ''}",
                 lambda q=q: self.main.new_query(None, q.get("db") or None, q["sql"])) for q in library.items]


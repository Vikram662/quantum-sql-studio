"""Colour tokens, Qt stylesheet and palette for the dark and light themes."""
import sys

from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette

DARK = {
    "window": "#1e1f22", "panel": "#2b2d30", "surface": "#1e1f22", "raised": "#313338",
    "border": "#393b40", "border_strong": "#4e5157",
    "text": "#dfe1e5", "muted": "#8c8f96", "faint": "#6f737a",
    "accent": "#3574f0", "accent_hover": "#4682fa", "accent_text": "#ffffff",
    "selection": "#2e436e", "hover": "#2e3036", "alt_row": "#232427",
    "success": "#5fb865", "warning": "#e0a84a", "danger": "#e5534b",
    "cell_modified": "#4a3f1f", "row_new": "#1f3a26", "null": "#6f737a",
    "editor_bg": "#1e1f22", "editor_line": "#26282e", "gutter": "#1e1f22", "gutter_text": "#4b5059",
    "syn_keyword": "#cf8e6d", "syn_function": "#56a8f5", "syn_string": "#6aab73",
    "syn_number": "#2aacb8", "syn_comment": "#7a7e85", "syn_ident": "#c77dbb", "syn_variable": "#e0a84a",
    "ic_server": "#3574f0", "ic_db": "#e0a84a", "ic_table": "#5c9ded", "ic_view": "#a77de8",
    "ic_routine": "#5fb865", "ic_trigger": "#e5534b", "ic_key": "#e0c04a", "ic_tool": "#b4b8bf",
}

LIGHT = {
    "window": "#f2f3f5", "panel": "#ffffff", "surface": "#ffffff", "raised": "#f7f8fa",
    "border": "#d5d7dc", "border_strong": "#b9bcc3",
    "text": "#1f2329", "muted": "#6b7079", "faint": "#9aa0a8",
    "accent": "#3574f0", "accent_hover": "#2a62d4", "accent_text": "#ffffff",
    "selection": "#d4e2ff", "hover": "#eceef2", "alt_row": "#f7f8fa",
    "success": "#2f8a3a", "warning": "#b7791f", "danger": "#d0362c",
    "cell_modified": "#fff1cc", "row_new": "#e3f5e6", "null": "#9aa0a8",
    "editor_bg": "#ffffff", "editor_line": "#f5f8fe", "gutter": "#ffffff", "gutter_text": "#aeb3bb",
    "syn_keyword": "#0033b3", "syn_function": "#00627a", "syn_string": "#067d17",
    "syn_number": "#1750eb", "syn_comment": "#8c8c8c", "syn_ident": "#871094", "syn_variable": "#9e6a00",
    "ic_server": "#3574f0", "ic_db": "#c27c0e", "ic_table": "#2f6fd6", "ic_view": "#7c4dcc",
    "ic_routine": "#2f8a3a", "ic_trigger": "#d0362c", "ic_key": "#b38b00", "ic_tool": "#5a5f68",
}

T = dict(DARK)  # active tokens, mutated in place by apply()


def code_font(size=10):
    families = set(QFontDatabase.families())
    for fam in ("Cascadia Mono", "JetBrains Mono", "Consolas", "SF Mono", "Menlo", "Ubuntu Mono",
                "DejaVu Sans Mono", "Courier New"):
        if fam in families:
            f = QFont(fam, size)
            f.setStyleHint(QFont.Monospace)
            return f
    f = QFont("monospace", size)
    f.setStyleHint(QFont.Monospace)
    return f


QSS = """
* {{ outline: 0; }}
QWidget {{ color: {text}; font-family: {ui_font}; font-size: {ui_size}; }}
QMainWindow, QDialog {{ background: {window}; }}
QMainWindow::separator {{ background: {border}; width: 1px; height: 1px; }}

QMenuBar {{ background: {panel}; border-bottom: 1px solid {border}; padding: 2px 4px; }}
QMenuBar::item {{ padding: 5px 10px; border-radius: 4px; background: transparent; }}
QMenuBar::item:selected {{ background: {hover}; }}
QMenu {{ background: {raised}; border: 1px solid {border}; padding: 4px; border-radius: 6px; }}
QMenu::item {{ padding: 6px 28px 6px 26px; border-radius: 4px; }}
QMenu::item:selected {{ background: {accent}; color: {accent_text}; }}
QMenu::item:disabled {{ color: {faint}; }}
QMenu::separator {{ height: 1px; background: {border}; margin: 4px 6px; }}
QMenu::icon {{ padding-left: 6px; }}

QToolBar {{ background: {panel}; border: none; border-bottom: 1px solid {border}; padding: 3px 6px; spacing: 2px; }}
QToolBar::separator {{ background: {border}; width: 1px; margin: 5px 6px; }}
QToolButton {{ background: transparent; border: 1px solid transparent; border-radius: 5px; padding: 4px; }}
QToolButton:hover {{ background: {hover}; border-color: {border}; }}
QToolButton:pressed, QToolButton:checked {{ background: {selection}; }}
QToolButton:disabled {{ color: {faint}; }}
QToolButton[popupMode="1"] {{ padding-right: 14px; }}

QStatusBar {{ background: {panel}; border-top: 1px solid {border}; color: {muted}; }}
QStatusBar QLabel {{ color: {muted}; padding: 0 8px; }}
QStatusBar::item {{ border: none; }}

QDockWidget {{ titlebar-close-icon: none; color: {muted}; }}
QDockWidget::title {{ background: {panel}; padding: 6px 10px; border-bottom: 1px solid {border};
    text-transform: uppercase; font-weight: 600; }}

QTabWidget::pane {{ border: none; border-top: 1px solid {border}; background: {window}; }}
QTabBar {{ background: {panel}; }}
QTabBar::tab {{ background: transparent; color: {muted}; padding: 7px 14px; border: none;
    border-bottom: 2px solid transparent; margin-right: 1px; }}
QTabBar::tab:hover {{ color: {text}; background: {hover}; }}
QTabBar::tab:selected {{ color: {text}; border-bottom: 2px solid {accent}; background: {window}; }}
QTabBar::close-button {{ subcontrol-position: right; }}
QTabWidget#subtabs QTabBar::tab {{ padding: 5px 12px; }}

QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QComboBox {{
    background: {surface}; border: 1px solid {border}; border-radius: 5px; padding: 4px 6px;
    selection-background-color: {selection}; selection-color: {text}; }}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus, QTextEdit:focus {{ border-color: {accent}; }}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled {{ color: {faint}; background: {window}; }}
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox QAbstractItemView {{ background: {raised}; border: 1px solid {border}; selection-background-color: {accent};
    selection-color: {accent_text}; padding: 2px; }}
QSpinBox::up-button, QSpinBox::down-button {{ width: 14px; border: none; }}

QPushButton {{ background: {raised}; border: 1px solid {border}; border-radius: 5px; padding: 5px 14px; min-height: 18px; }}
QPushButton:hover {{ border-color: {border_strong}; background: {hover}; }}
QPushButton:pressed {{ background: {selection}; }}
QPushButton:disabled {{ color: {faint}; }}
QPushButton[primary="true"] {{ background: {accent}; border-color: {accent}; color: {accent_text}; font-weight: 600; }}
QPushButton[primary="true"]:hover {{ background: {accent_hover}; border-color: {accent_hover}; }}
QPushButton[danger="true"] {{ color: {danger}; }}
QPushButton:default {{ border-color: {accent}; }}

QCheckBox, QRadioButton {{ spacing: 6px; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 14px; height: 14px; border: 1px solid {border_strong};
    background: {surface}; border-radius: 3px; }}
QRadioButton::indicator {{ border-radius: 7px; }}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{ background: {accent}; border-color: {accent}; }}

QGroupBox {{ border: 1px solid {border}; border-radius: 6px; margin-top: 14px; padding: 10px 8px 8px 8px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {muted}; }}

QTreeView, QTreeWidget, QTableView, QListView, QListWidget {{
    background: {surface}; alternate-background-color: {alt_row}; border: none;
    selection-background-color: {selection}; selection-color: {text}; gridline-color: {border}; }}
QTreeView::item, QListView::item {{ padding: 3px 2px; }}
QTreeView::item:hover, QListView::item:hover {{ background: {hover}; }}
QTreeView::item:selected, QListView::item:selected {{ background: {selection}; color: {text}; }}
QTableView {{ border-top: 1px solid {border}; }}
QTableView::item {{ padding: 0 6px; }}
QTableView QTableCornerButton::section {{ background: {panel}; border: none;
    border-right: 1px solid {border}; border-bottom: 1px solid {border}; }}
QHeaderView {{ background: {panel}; }}
QHeaderView::section {{ background: {panel}; color: {muted}; border: none; border-right: 1px solid {border};
    border-bottom: 1px solid {border}; padding: 5px 6px; font-weight: 600; }}
QHeaderView::section:hover {{ color: {text}; }}
QHeaderView::section:vertical {{ padding: 0 6px; font-weight: 400; color: {faint}; }}

QScrollBar:vertical {{ background: transparent; width: 11px; margin: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 11px; margin: 0; }}
QScrollBar::handle {{ background: {border_strong}; border-radius: 4px; min-height: 28px; min-width: 28px; margin: 2px; }}
QScrollBar::handle:hover {{ background: {muted}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QSplitter::handle {{ background: {border}; }}
QSplitter::handle:horizontal {{ width: 1px; }}
QSplitter::handle:vertical {{ height: 1px; }}

QToolTip {{ background: {raised}; color: {text}; border: 1px solid {border}; padding: 5px; border-radius: 4px; }}
QProgressBar {{ background: {surface}; border: 1px solid {border}; border-radius: 4px; text-align: center; height: 16px; }}
QProgressBar::chunk {{ background: {accent}; border-radius: 3px; }}

QLabel#h1 {{ font-size: 15pt; font-weight: 600; }}
QLabel#h2 {{ font-size: 11pt; font-weight: 600; }}
QLabel#muted {{ color: {muted}; }}
QLabel#badge {{ background: {raised}; border: 1px solid {border}; border-radius: 9px; padding: 1px 8px; color: {muted}; }}
QFrame#card {{ background: {panel}; border: 1px solid {border}; border-radius: 8px; }}
QFrame#bar {{ background: {panel}; border-bottom: 1px solid {border}; }}
QFrame#banner {{ background: {raised}; border-bottom: 1px solid {border}; }}
QWidget#sidebar {{ background: {panel}; }}
QListWidget#sessions {{ background: {panel}; border-right: 1px solid {border}; }}
QListWidget#sessions::item {{ padding: 7px 8px; border-radius: 5px; margin: 1px 4px; }}
"""


def _write_svg_assets():
    """Stylesheets can only reference image files, so write the few we need."""
    import os
    from .config import data_dir
    folder = os.path.join(data_dir(), "theme")
    os.makedirs(folder, exist_ok=True)
    shapes = {
        "close": ("muted", '<path d="M7 7l10 10M17 7L7 17"/>'),
        "close_hover": ("text", '<path d="M7 7l10 10M17 7L7 17"/>'),
        "chevron": ("muted", '<path d="M7 10l5 5 5-5"/>'),
        "check": ("accent_text", '<path d="M6 12l4 4 8-9"/>'),
    }
    paths = {}
    for key, (color, body) in shapes.items():
        path = os.path.join(folder, f"{key}_{T['window'][1:]}.svg")
        if not os.path.exists(path):
            with open(path, "w", encoding="utf-8") as f:
                f.write(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
                        f'stroke="{T[color]}" stroke-width="2.2" stroke-linecap="round" '
                        f'stroke-linejoin="round">{body}</svg>')
        paths[key] = path.replace("\\", "/")
    return paths


ASSET_QSS = """
QTabBar::close-button {{ image: url("{close}"); border-radius: 3px; padding: 1px; }}
QTabBar::close-button:hover {{ image: url("{close_hover}"); background: {hover}; }}
QComboBox::down-arrow {{ image: url("{chevron}"); width: 12px; height: 12px; }}
QCheckBox::indicator:checked, QTableView::indicator:checked {{ image: url("{check}"); }}
QTableView::indicator {{ width: 14px; height: 14px; border: 1px solid {border_strong}; border-radius: 3px;
    background: {surface}; }}
QTableView::indicator:checked {{ background: {accent}; border-color: {accent}; }}
"""


if sys.platform == "win32":
    UI_FONT = {"ui_font": '"Segoe UI"', "ui_size": "9pt"}
elif sys.platform == "darwin":
    UI_FONT = {"ui_font": '".AppleSystemUIFont", "Helvetica Neue"', "ui_size": "13px"}
else:
    UI_FONT = {"ui_font": '"Noto Sans", "Ubuntu", "Cantarell", "DejaVu Sans"', "ui_size": "10pt"}


def apply(app, name="dark"):
    T.clear()
    T.update(LIGHT if name == "light" else DARK)
    app.setStyle("Fusion")
    pal = QPalette()
    c = lambda k: QColor(T[k])  # noqa: E731
    pal.setColor(QPalette.Window, c("window"))
    pal.setColor(QPalette.WindowText, c("text"))
    pal.setColor(QPalette.Base, c("surface"))
    pal.setColor(QPalette.AlternateBase, c("alt_row"))
    pal.setColor(QPalette.Text, c("text"))
    pal.setColor(QPalette.Button, c("raised"))
    pal.setColor(QPalette.ButtonText, c("text"))
    pal.setColor(QPalette.Highlight, c("selection"))
    pal.setColor(QPalette.HighlightedText, c("text"))
    pal.setColor(QPalette.ToolTipBase, c("raised"))
    pal.setColor(QPalette.ToolTipText, c("text"))
    pal.setColor(QPalette.PlaceholderText, c("faint"))
    pal.setColor(QPalette.Link, c("accent"))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        pal.setColor(QPalette.Disabled, role, c("faint"))
    app.setPalette(pal)
    qss = QSS.format(**T, **UI_FONT)
    try:
        qss += ASSET_QSS.format(**T, **_write_svg_assets())
    except OSError:
        pass
    app.setStyleSheet(qss)

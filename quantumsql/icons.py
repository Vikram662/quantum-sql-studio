"""Inline SVG line icons, tinted to the active theme."""
from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from . import theme

_P = {
    "server": '<rect x="3" y="3" width="18" height="7" rx="2"/><rect x="3" y="14" width="18" height="7" rx="2"/>'
              '<path d="M7 6.5h.01M7 17.5h.01"/>',
    "database": '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5"/>'
                '<path d="M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"/>',
    "table": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 10h18M3 15h18M9 10v10"/>',
    "view": '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
    "procedure": '<path d="M14 4h-2a3 3 0 0 0-3 3v13M6 11h7"/><path d="M15 14l5 5M20 14l-5 5"/>',
    "function": '<path d="M14 4h-2a3 3 0 0 0-3 3v13M6 11h7"/><path d="M16 13l2 3 2-3M18 16v4"/>',
    "trigger": '<path d="M13 2L4 14h7l-1 8 9-12h-7z"/>',
    "key": '<circle cx="8" cy="15" r="4"/><path d="M11 12l9-9M17 6l3 3M15 8l2 2"/>',
    "column": '<rect x="8" y="3" width="8" height="18" rx="1"/>',
    "play": '<path d="M7 4l13 8-13 8z" fill="{c}"/>',
    "play-line": '<path d="M4 5l9 7-9 7z" fill="{c}"/><path d="M17 5v14M21 5v14"/>',
    "stop": '<rect x="6" y="6" width="12" height="12" rx="2" fill="{c}"/>',
    "save": '<path d="M5 3h11l3 3v13a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z"/><path d="M7 3v5h8V3M7 21v-7h10v7"/>',
    "open": '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "minus": '<path d="M5 12h14"/>',
    "trash": '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
    "refresh": '<path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 4v7h-7"/>',
    "export": '<path d="M12 4v11M7 10l5 5 5-5M5 20h14"/>',
    "import": '<path d="M12 20V9M7 14l5-5 5 5M5 4h14"/>',
    "plug": '<path d="M9 2v6M15 2v6M6 8h12v4a6 6 0 0 1-12 0zM12 18v4"/>',
    "unplug": '<path d="M9 2v6M15 2v6M6 8h12v4a6 6 0 0 1-12 0zM12 18v4"/><path d="M3 3l18 18"/>',
    "code": '<path d="M8 8l-5 4 5 4M16 8l5 4-5 4M14 5l-4 14"/>',
    "query": '<path d="M5 3h10l4 4v14H5z"/><path d="M14 3v5h5M8 13h8M8 17h5"/>',
    "history": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 3"/>',
    "filter": '<path d="M4 5h16l-6 8v6l-4-2v-4z"/>',
    "search": '<circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/>',
    "users": '<circle cx="9" cy="8" r="4"/><path d="M2 21a7 7 0 0 1 14 0"/><path d="M16 4a4 4 0 0 1 0 8M22 21a7 7 0 0 0-4-6.3"/>',
    "chart": '<path d="M4 20V10M10 20V4M16 20v-7M22 20H2"/>',
    "left": '<path d="M15 6l-6 6 6 6"/>',
    "right": '<path d="M9 6l6 6-6 6"/>',
    "first": '<path d="M17 6l-6 6 6 6M7 6v12"/>',
    "last": '<path d="M7 6l6 6-6 6M17 6v12"/>',
    "up": '<path d="M12 19V5M6 11l6-6 6 6"/>',
    "down": '<path d="M12 5v14M6 13l6 6 6-6"/>',
    "check": '<path d="M5 12l5 5 9-10"/>',
    "x": '<path d="M6 6l12 12M18 6L6 18"/>',
    "edit": '<path d="M4 20h4L19 9l-4-4L4 16z"/>',
    "copy": '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/>',
    "moon": '<path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5h.01"/>',
    "log": '<path d="M4 6h16M4 12h16M4 18h10"/>',
    "activity": '<path d="M3 12h4l3-8 4 16 3-8h4"/>',
    "settings": '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1L7 17M17 7l2.1-2.1"/>',
    "format": '<path d="M4 6h16M8 12h12M4 18h16"/>',
    "comment": '<path d="M7 20l3-16M14 20l3-16"/>',
    "dot": '<circle cx="12" cy="12" r="6" fill="{c}" stroke="none"/>',
    "shield": '<path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/><path d="M9 12l2 2 4-4"/>',
    "undo": '<path d="M9 14L4 9l5-5"/><path d="M4 9h11a5 5 0 0 1 0 10h-3"/>',
    "sparkle": '<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/><path d="M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z"/>',
    "star": '<path d="M12 3l2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1-4.4-4.3 6.1-.9z"/>',
    "diagram": '<rect x="3" y="3" width="7" height="6" rx="1"/><rect x="14" y="15" width="7" height="6" rx="1"/><rect x="14" y="3" width="7" height="6" rx="1"/><path d="M10 6h4M17.5 9v6"/>',
    "diff": '<path d="M6 3v12M3 6h6M15 18h6"/><rect x="13" y="3" width="8" height="10" rx="1"/><rect x="3" y="15" width="8" height="6" rx="1"/>',
    "command": '<path d="M9 6a3 3 0 1 0-3 3h12a3 3 0 1 0-3-3v12a3 3 0 1 0 3-3H6a3 3 0 1 0 3 3z"/>',
    "wand": '<path d="M4 20L16 8M14 4v2M18 8h2M17 5l1.5-1.5M10 6l1 1M17 12l1 1"/>',
    "folder": '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    "lock": '<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>',
    "bolt": '<path d="M13 2L4 14h7l-1 8 9-12h-7z"/>',
    "grid": '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
}

_KIND_COLORS = {
    "server": "ic_server", "database": "ic_db", "table": "ic_table", "view": "ic_view",
    "procedure": "ic_routine", "function": "ic_routine", "trigger": "ic_trigger", "key": "ic_key",
    "play": "success", "play-line": "success", "stop": "danger", "trash": "danger",
    "shield": "success", "sparkle": "ic_view", "star": "warning", "bolt": "warning",
}

_cache = {}


def icon(name, color=None):
    color = color or theme.T.get(_KIND_COLORS.get(name, "ic_tool"), "#888888")
    key = (name, color)
    if key in _cache:
        return _cache[key]
    body = _P.get(name, _P["dot"]).replace("{c}", color)
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
           f'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">{body}</svg>')
    renderer = QSvgRenderer(QByteArray(svg.encode()))
    ic = QIcon()
    for size in (16, 20, 24, 32, 48):
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        renderer.render(p, QRectF(0, 0, size, size))
        p.end()
        ic.addPixmap(pm)
    _cache[key] = ic
    return ic


def clear_cache():
    _cache.clear()

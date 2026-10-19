"""Outline line icons (Lucide/Feather style, 24x24 grid) drawn from inline SVG strings with QtSvg."""
from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

ICONS = {
    "dashboard": '<rect x="3" y="3" width="7" height="9" rx="1"/><rect x="14" y="3" width="7" height="5" rx="1"/>'
                 '<rect x="14" y="12" width="7" height="9" rx="1"/><rect x="3" y="16" width="7" height="5" rx="1"/>',
    "duplicates": '<rect x="9" y="9" width="13" height="13" rx="2"/>'
                  '<path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
    "organize": '<path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/>',
    "import": '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/>'
              '<line x1="12" y1="15" x2="12" y2="3"/>',
    "backup": '<line x1="22" y1="12" x2="2" y2="12"/>'
              '<path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/>'
              '<line x1="6" y1="16" x2="6.01" y2="16"/><line x1="10" y1="16" x2="10.01" y2="16"/>',
    "health": '<polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/>',
    "browse": '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/>'
              '<path d="m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21"/>',
    "search": '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
    "people": '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/>'
              '<path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>',
    "tags": '<path d="M12.586 2.586A2 2 0 0 0 11.172 2H4a2 2 0 0 0-2 2v7.172a2 2 0 0 0 .586 1.414l8.704 8.704a2.426 2.426 0 0 0 3.42 0l6.58-6.58a2.426 2.426 0 0 0 0-3.42z"/>'
            '<circle cx="7.5" cy="7.5" r=".6"/>',
    "map": '<path d="M20 10c0 6-8 12-8 12s-8-6-8-12a8 8 0 0 1 16 0Z"/><circle cx="12" cy="10" r="3"/>',
    "ai": '<rect x="4" y="4" width="16" height="16" rx="2"/><rect x="9" y="9" width="6" height="6"/>'
          '<path d="M15 2v2M15 20v2M2 15h2M2 9h2M20 15h2M20 9h2M9 2v2M9 20v2"/>',
    "quarantine": '<rect x="2" y="3" width="20" height="5" rx="1"/><path d="M4 8v11a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8"/>'
                  '<path d="M10 12h4"/>',
    "log": '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/>'
           '<path d="M10 9H8M16 13H8M16 17H8"/>',
    "help": '<circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><path d="M12 17h.01"/>',
    "settings": '<line x1="21" y1="4" x2="14" y2="4"/><line x1="10" y1="4" x2="3" y2="4"/>'
                '<line x1="21" y1="12" x2="12" y2="12"/><line x1="8" y1="12" x2="3" y2="12"/>'
                '<line x1="21" y1="20" x2="16" y2="20"/><line x1="12" y1="20" x2="3" y2="20"/>'
                '<line x1="14" y1="2" x2="14" y2="6"/><line x1="8" y1="10" x2="8" y2="14"/>'
                '<line x1="16" y1="18" x2="16" y2="22"/>',
}

_cache: dict = {}


def svg_for(name: str, color: str, stroke: float = 1.75) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
            f'stroke-width="{stroke}" stroke-linecap="round" stroke-linejoin="round">{ICONS[name]}</svg>')


def icon_pixmap(name: str, color: str, size: int, dpr: float = 1.0) -> QPixmap:
    """Icon `name` as a size x size (logical px) pixmap, sharp at the given device pixel ratio."""
    key = (name, color, size, dpr)
    pm = _cache.get(key)
    if pm is None:
        dev = max(1, int(round(size * dpr)))
        pm = QPixmap(dev, dev)
        pm.fill(Qt.transparent)
        r = QSvgRenderer(QByteArray(svg_for(name, color).encode("utf-8")))
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        r.render(p, QRectF(0, 0, dev, dev))
        p.end()
        pm.setDevicePixelRatio(dpr)
        _cache[key] = pm
    return pm


def qcolor(c: str) -> QColor:
    """Parse '#rrggbb' or the 'rgba(r,g,b,a255)' strings used by the theme tokens."""
    if c.startswith("rgba"):
        r, g, b, a = [int(float(x)) for x in c[c.index("(") + 1:c.index(")")].split(",")]
        return QColor(r, g, b, a)
    return QColor(c)

"""Ikon garis (line icon) sidebar, digambar dari SVG sederhana tanpa berkas/pustaka luar."""
from PyQt6.QtCore import QByteArray, QRectF, Qt
from PyQt6.QtGui import QIcon, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer

# 24×24, stroke = {c}; gaya garis membulat seperti ikon antarmuka modern
_SVG = {
    "live": '<rect x="2" y="4" width="20" height="14" rx="2"/><path d="M10 8.5v5l4.5-2.5z"/>'
            '<path d="M8 21h8"/>',
    "camera": '<rect x="2" y="6" width="14" height="12" rx="2"/><path d="M16 10.5l6-3.5v10l-6-3.5"/>',
    "zone": '<path d="M5 5l11-1 4 9-7 7-9-4z"/><circle cx="5" cy="5" r="1.4"/>'
            '<circle cx="16" cy="4" r="1.4"/><circle cx="20" cy="13" r="1.4"/>'
            '<circle cx="13" cy="20" r="1.4"/><circle cx="4" cy="16" r="1.4"/>',
    "module": '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/>'
              '<rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
    "analytics": '<path d="M3 21h18"/><path d="M6 17v-5"/><path d="M11 17V7"/><path d="M16 17v-8"/>'
                 '<path d="M21 17V4"/>',
    "report": '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/>'
              '<path d="M8 13h8"/><path d="M8 17h5"/>',
    "alert": '<path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.9 1.9 0 0 0 3.4 0"/>',
    "settings": '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1'
                'M17 17l2.1 2.1M4.9 19.1L7 17M17 7l2.1-2.1"/><circle cx="12" cy="12" r="7"/>',
    # ikon tombol (halaman Kamera dan lainnya)
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "edit": '<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="M13.5 6.5l4 4"/>',
    "trash": '<path d="M4 7h16"/><path d="M9 7V4h6v3"/><path d="M6 7l1 13h10l1-13"/><path d="M10 11v6M14 11v6"/>',
    "brush": '<path d="M14 4l6 6-8 8-6-6z"/><path d="M6 12l-2 6 6-2"/>',
    "wand": '<path d="M4 20L16 8"/><path d="M17 3v4M15 5h4"/><path d="M20 10v2M19 11h2"/>'
            '<path d="M9 3v2M8 4h2"/>',
    "export": '<path d="M12 15V3"/><path d="M7 8l5-5 5 5"/><path d="M4 14v5a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-5"/>',
    "import": '<path d="M12 3v12"/><path d="M7 10l5 5 5-5"/><path d="M4 14v5a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-5"/>',
    "search": '<circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/>',
    "eye": '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
    "copy": '<rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h8"/>',
    "list": '<path d="M9 6h11M9 12h11M9 18h11"/><circle cx="4.5" cy="6" r="1"/><circle cx="4.5" cy="12" r="1"/>'
            '<circle cx="4.5" cy="18" r="1"/>',
    "more": '<circle cx="12" cy="5" r="1.3"/><circle cx="12" cy="12" r="1.3"/><circle cx="12" cy="19" r="1.3"/>',
    "play": '<path d="M7 4l13 8-13 8z"/>',
    "user": '<circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 4-6 8-6s8 2 8 6"/>',
    "cpu": '<rect x="6" y="6" width="12" height="12" rx="2"/><rect x="9.5" y="9.5" width="5" height="5"/>'
           '<path d="M9 2v3M15 2v3M9 19v3M15 19v3M2 9h3M2 15h3M19 9h3M19 15h3"/>',
    "pin": '<path d="M12 22s7-6.2 7-12a7 7 0 0 0-14 0c0 5.8 7 12 7 12z"/><circle cx="12" cy="10" r="2.5"/>',
    "check": '<path d="M4 12.5l5 5L20 6.5"/>',
    "check_circle": '<circle cx="12" cy="12" r="9"/><path d="M8 12.5l3 3 5-6"/>',
    "folder": '<path d="M3 6a2 2 0 0 1 2-2h4l2 3h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    "refresh": '<path d="M20 11a8 8 0 0 0-14.6-4.4L4 8"/><path d="M4 4v4h4"/><path d="M4 13a8 8 0 0 0 14.6 4.4L20 16"/>'
               '<path d="M20 20v-4h-4"/>',
    "flask": '<path d="M9 3h6"/><path d="M10 3v6l-5 9a2 2 0 0 0 1.8 3h10.4a2 2 0 0 0 1.8-3l-5-9V3"/>'
             '<path d="M7.5 15h9"/>',
    "archive": '<rect x="3" y="4" width="18" height="5" rx="1"/><path d="M5 9v10a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V9"/>'
               '<path d="M10 13h4"/>',
    "chevron": '<path d="M6 9l6 6 6-6"/>',
    "chevron_r": '<path d="M9 6l6 6-6 6"/>',
    "line": '<path d="M5 19L19 5"/><circle cx="5" cy="19" r="2"/><circle cx="19" cy="5" r="2"/>',
    "undo": '<path d="M9 14L4 9l5-5"/><path d="M4 9h11a5 5 0 0 1 0 10h-4"/>',
    "photo": '<path d="M4 8h3l2-3h6l2 3h3v11H4z"/><circle cx="12" cy="13" r="3.5"/>',
    "eye_off": '<path d="M2 12s3.5-7 10-7c2 0 3.7.6 5.1 1.5M22 12s-3.5 7-10 7c-2 0-3.7-.6-5.1-1.5"/>'
               '<path d="M3 3l18 18"/>',
    "flip": '<path d="M7 4L3 8l4 4"/><path d="M3 8h14"/><path d="M17 20l4-4-4-4"/><path d="M21 16H7"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "gauge": '<path d="M4 18a8 8 0 1 1 16 0"/><path d="M12 18l4-6"/><path d="M4 18h16"/>',
    "pulse": '<path d="M2 12h4l3-7 4 14 3-7h6"/>',
    "swap": '<path d="M7 4L3 8l4 4"/><path d="M3 8h13"/><path d="M17 20l4-4-4-4"/><path d="M21 16H8"/>',
    "pdf": '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><path d="M14 2v6h6"/>'
           '<path d="M9 15h6"/><path d="M12 12v6"/>',
    "table": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 10h18M3 15h18M9 10v10"/>',
    "warning": '<path d="M12 3L2 20h20z"/><path d="M12 10v4"/><circle cx="12" cy="17" r="0.6"/>',
    "x_circle": '<circle cx="12" cy="12" r="9"/><path d="M9 9l6 6M15 9l-6 6"/>',
    "alert_circle": '<circle cx="12" cy="12" r="9"/><path d="M12 7.5v5.5"/><circle cx="12" cy="16.3" r="0.6"/>',
    "save": '<path d="M5 3h11l4 4v12a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z"/><path d="M8 3v5h7V3"/>'
            '<rect x="8" y="13" width="8" height="6" rx="1"/>',
    "shield": '<path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/><rect x="9" y="11" width="6" height="5" rx="1"/>'
              '<path d="M10.2 11V9.5a1.8 1.8 0 0 1 3.6 0V11"/>',
    "globe": '<circle cx="12" cy="12" r="9"/><path d="M3 12h18"/><path d="M12 3c2.5 2.5 3.8 5.5 3.8 9s-1.3 6.5-3.8 9'
             'c-2.5-2.5-3.8-5.5-3.8-9S9.5 5.5 12 3z"/>',
    "browser": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 9h18"/><circle cx="6.5" cy="6.5" r="0.6"/>'
               '<circle cx="9" cy="6.5" r="0.6"/>',
    "history": '<path d="M3 12a9 9 0 1 0 3-6.7L3 8"/><path d="M3 3v5h5"/><path d="M12 7v5l3 2"/>',
    "key": '<circle cx="8" cy="15" r="4"/><path d="M11 12l9-9M17 6l3 3M15 8l2 2"/>',
    "external": '<path d="M14 4h6v6"/><path d="M20 4l-9 9"/><path d="M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
    "stop": '<rect x="5" y="5" width="14" height="14" rx="2"/>',
}


def _pixmap(name: str, color: str, size: int) -> QPixmap:
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
           f'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">{_SVG[name]}</svg>')
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    QSvgRenderer(QByteArray(svg.encode())).render(painter, QRectF(0, 0, size, size))
    painter.end()
    return pix


def icon(name: str, color: str, size: int = 36) -> QIcon:
    """Ikon satu warna untuk tombol biasa."""
    return QIcon(_pixmap(name, color, size))


def nav_icon(name: str, color: str, active: str, size: int = 36) -> QIcon:
    """Ikon dua warna: `color` saat tombol tidak aktif, `active` saat terpilih (checked)."""
    icon = QIcon()
    icon.addPixmap(_pixmap(name, color, size), QIcon.Mode.Normal, QIcon.State.Off)
    icon.addPixmap(_pixmap(name, active, size), QIcon.Mode.Normal, QIcon.State.On)
    icon.addPixmap(_pixmap(name, active, size), QIcon.Mode.Active, QIcon.State.On)
    return icon

"""Small SVG icon set, rendered locally without additional dependencies."""
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QByteArray, Qt, QRectF
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer


SHAPES = {
    'chevron_down': '<path d="m5 9 7 7 7-7"/>',
    'bold': '<path d="M6 4h6a4 4 0 0 1 0 8H6zm0 8h7a4 4 0 0 1 0 8H6z"/>',
    'italic': '<path d="M11 4h8M5 20h8M15 4 9 20"/>',
    'numbered': '<path d="m3 8 3-2v12m-3 0h6m5-9h7m-7 6h7"/>',
    'microphone': '<rect x="9" y="3" width="6" height="12" rx="3"/><path d="M6 11v1a6 6 0 0 0 12 0v-1M12 18v3m-3 0h6"/>',
    'stop': '<rect x="6" y="6" width="12" height="12" rx="2"/>',
    'copy': '<rect x="8" y="8" width="13" height="13" rx="2"/><path d="M16 5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2"/>',
    'dot': '<circle cx="12" cy="12" r="7"/>',
    'note': '<rect x="5" y="4" width="14" height="16" rx="2"/><path d="M9 8h6M9 12h6M9 16h3"/>',
    'folder': '<path d="M3 7a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v10H3z"/><path d="M3 10h18"/>',
    'bell': '<path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4"/>',
    'calendar': '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M7 3v4M17 3v4M3 10h18M7 14h2M13 14h2M7 18h2"/>',
    'star': '<path d="m12 3 2.8 5.7 6.2.9-4.5 4.4 1.1 6.2-5.6-3-5.6 3 1.1-6.2L3 9.6l6.2-.9z"/>',
    'archive': '<rect x="3" y="4" width="18" height="4" rx="1"/><path d="M4 8v12h16V8M9 12h6"/>',
    'trash': '<path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7"/>',
    'search': '<circle cx="10.5" cy="10.5" r="7"/><path d="m16 16 5 5"/>',
    'filter': '<path d="M4 6h16M7 12h10M10 18h4"/>',
    'phone': '<path d="M5 3 3 5c0 8 8 16 16 16l2-2-5-5-3 2-5-5 2-3z"/>',
    'laptop': '<rect x="5" y="4" width="14" height="12" rx="1.5"/><path d="m5 16-3 4h20l-3-4M9 20h6"/>',
    'plane': '<path transform="rotate(35 12 12)" d="M21 16v-2l-8-5V3.5a1.5 1.5 0 0 0-3 0V9l-8 5v2l8-2.5V19l-2 1.5V22l3.5-1 3.5 1v-1.5L13 19v-5.5z"/>',
    'book': '<path d="M12 5C9 3 6 3 3 4v15c3-1 6-1 9 1 3-2 6-2 9-1V4c-3-1-6-1-9 1v15"/>',
    'gift': '<rect x="3" y="8" width="18" height="4" rx="1"/><path d="M5 12v9h14v-9M12 8v13"/><path d="M12 8C2 8 6 0 10 4l2 4c10 0 6-8 2-4z"/>',
    'team': '<circle cx="9" cy="7" r="4"/><path d="M2 21v-3a7 7 0 0 1 14 0v3zM16 4a4 4 0 0 1 0 8M19 15c3 1 3 3 3 6"/>',
    'check': '<rect x="3" y="3" width="18" height="18" rx="3"/><path d="m7 12 3 3 7-7"/>',
    'checklist': '<path d="m3 6 2 2 4-4M12 6h9M12 17h9"/><rect x="3" y="14" width="6" height="6" rx="1"/>',
    'tick': '<path d="m4 12 5 5L20 6"/>',
    'list': '<path d="M9 6h12M9 12h12M9 18h12M3 6h1M3 12h1M3 18h1"/>',
    'link': '<path d="m10 7 2-2a5 5 0 0 1 7 7l-3 3M14 17l-2 2a5 5 0 0 1-7-7l3-3M8 16l8-8"/>',
    'image': '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8" cy="8" r="1.5"/><path d="m3 18 6-6 4 4 3-3 5 5"/>',
    'share': '<path d="M8 10H5v11h14V10h-3M12 15V2M7 7l5-5 5 5"/>',
    'clock': '<circle cx="12" cy="12" r="9"/><path d="M12 6v6l4 2"/>',
    'sun': '<circle cx="12" cy="12" r="4"/><path d="M12 1v2M12 21v2M1 12h2M21 12h2M4 4l2 2M18 18l2 2M4 20l2-2M18 6l2-2"/>',
    'moon': '<path d="M20 15A9 9 0 0 1 9 4a9 9 0 1 0 11 11z"/>',
    'repeat': '<path d="M20 8a8 8 0 0 0-14-3L3 8M3 3v5h5M4 16a8 8 0 0 0 14 3l3-3M21 21v-5h-5"/>',
    'plus': '<path d="M12 4v16M4 12h16"/>',
    'close': '<path d="m6 6 12 12M18 6 6 18"/>',
    'chevron': '<path d="m9 4 8 8-8 8"/>',
    'restore': '<path d="M3 10a9 9 0 1 1 2 8M3 4v6h6M12 7v5l3 2"/>',
}


def _svg_pixmap(svg, size):
    result = QPixmap(size*2,size*2)
    result.setDevicePixelRatio(2)
    result.fill(Qt.transparent)
    painter = QPainter(result)
    QSvgRenderer(QByteArray(svg.encode())).render(painter,QRectF(0,0,size,size))
    painter.end()
    return result


def pixmap(name, color='#17202c', size=24, filled=False):
    shape = SHAPES.get(name,SHAPES['note'])
    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24"><g fill="{color if filled else "none"}" stroke="{color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">{shape}</g></svg>'
    return _svg_pixmap(svg, size)


def icon(name,color='#17202c',size=24,filled=False):
    return QIcon(pixmap(name,color,size,filled))


_PHOSPHOR_ASSETS = Path(__file__).resolve().parents[1] / 'assets' / 'phosphor'


@lru_cache(maxsize=32)
def _phosphor_svg(name):
    return (_PHOSPHOR_ASSETS / f'{name}.svg').read_text(encoding='utf-8')


def duotone_pixmap(name, color, size=24):
    return _svg_pixmap(_phosphor_svg(name).replace('currentColor', color), size)


def duotone_icon(name, color, size=24):
    return QIcon(duotone_pixmap(name, color, size))


# Sidebar tag artwork is separate from the note icon picker.
_TAG_SYMBOLS = {
    'home': ('#91572E', '<path d="M3 11 12 3.5 21 11l-2-.2V20H5v-9.2z"/>'
                        '<path d="M9.4 20v-6h5.2v6" fill="none"/>'),
    'bookmark': ('#735B80', '<path d="M6.5 3.5h11v17L12 16.7l-5.5 3.8z"/>'),
    'route': ('#316777', '<path d="M7.4 18H15a3.1 3.1 0 0 0 0-6.2H9a3.4 3.4 0 0 1 0-6.8h7.6" fill="none"/>'
                         '<circle cx="5" cy="18" r="2.35"/><circle cx="19" cy="5" r="2.35"/>'),
    'glasses': ('#655F37', '<path d="M1.6 8.1h2.5m15.8 0h2.5m-12 2a2 2 0 0 1 3.2 0" fill="none"/>'
                           '<path d="M4.4 7.2h5a1 1 0 0 1 1 1.1l-.3 4.4a1.6 1.6 0 0 1-1.6 1.5H5.4a1.6 1.6 0 0 1-1.6-1.5l-.4-4.4a1 1 0 0 1 1-1.1Z"/>'
                           '<path d="M14.6 7.2h5a1 1 0 0 1 1 1.1l-.4 4.4a1.6 1.6 0 0 1-1.6 1.5h-3.1a1.6 1.6 0 0 1-1.6-1.5l-.3-4.4a1 1 0 0 1 1-1.1Z"/>'),
    'tag': ('#755B47', '<path d="M4 5h9l7 7-7 7H4z"/><circle cx="8" cy="12" r="1" fill="none"/>'),
}

_TAG_ICON_BY_NAME = {
    'дом': 'home',
    'личное': 'bookmark',
    'поездки': 'route',
    'путешествия': 'route',
    'чтение': 'glasses',
}


def tag_icon(name, size=24):
    symbol = _TAG_ICON_BY_NAME.get(name.strip().casefold(), 'tag')
    color, shape = _TAG_SYMBOLS[symbol]
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24">'
        f'<g fill="{color}" fill-opacity=".2" stroke="{color}" stroke-width="1.65" '
        f'stroke-linecap="round" stroke-linejoin="round">{shape}</g></svg>'
    )
    return QIcon(_svg_pixmap(svg, size))


# Keys are the existing SQLite values. Only the artwork and palette change.
_NOTE_SYMBOLS = {
    'note': ('note-pencil', '#A24F25', '#F8EBDD'),
    'phone': ('phone-call', '#346952', '#E6F0E8'),
    'laptop': ('briefcase', '#63516F', '#F0EAF3'),
    'plane': ('airplane-tilt', '#2B637A', '#E6F0F4'),
    'book': ('book-open-text', '#825028', '#F4EBDF'),
    'gift': ('shopping-bag-open', '#A05146', '#F8EBE8'),
    'team': ('chats-circle', '#675080', '#F0EAF4'),
    'calendar': ('calendar-dots', '#9D4B27', '#F8EBDD'),
    'star': ('star', '#9E621F', '#FAF0DB'),
    'check': ('list-checks', '#367256', '#E6F0E8'),
}

_AUTOMATIC_SYMBOLS = (
    (('позвон', 'звонок', 'телефон'), 'phone'),
    (('презентац', 'проект'), 'laptop'),
    (('отпуск', 'путеше', 'поездк'), 'plane'),
    (('книг', 'читать'), 'book'),
    (('подар', 'купить', 'покупк'), 'gift'),
    (('команд', 'встреч'), 'team'),
)


def note_symbol(title,selected=None):
    if selected:
        return _NOTE_SYMBOLS.get(selected, _NOTE_SYMBOLS['note'])
    title=title.casefold()
    for words, name in _AUTOMATIC_SYMBOLS:
        if any(word in title for word in words):
            return _NOTE_SYMBOLS[name]
    return _NOTE_SYMBOLS['note']

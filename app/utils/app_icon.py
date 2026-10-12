"""Shared branded icon for application windows and UI identity surfaces."""
import sys

from PySide6.QtGui import QIcon

from app.utils.runtime_paths import resource_path


def application_icon():
    """Load packaged assets, falling back if a platform icon cannot be decoded."""
    names = ('app.ico', 'app.png') if sys.platform == 'win32' else ('app.png', 'app.ico')
    for name in names:
        path = resource_path('app/assets/' + name)
        if path.is_file():
            result = QIcon(str(path))
            if not result.isNull() and not result.pixmap(32, 32).isNull():
                return result
    return QIcon()

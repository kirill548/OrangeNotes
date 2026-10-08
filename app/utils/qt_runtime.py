"""Keep the frozen application independent of another application's Qt setup."""
import os
import sys
from pathlib import Path


def configure_platform():
    if not getattr(sys, 'frozen', False):
        return None
    root = Path(sys._MEIPASS)
    candidates = [root / 'PySide6/plugins', root / 'PySide6/Qt/plugins']
    if sys.platform != 'win32':
        candidates.reverse()
    plugins = next((candidate for candidate in candidates if (candidate / 'platforms').is_dir()), candidates[0])
    if sys.platform == 'win32' and not (plugins / 'platforms/qwindows.dll').is_file():
        raise RuntimeError('Отсутствует плагин Windows. Распакуйте весь архив OrangeNotes; папка _internal должна находиться рядом с EXE.')
    # An inherited plugin path or QPA backend may belong to a different Qt version.
    for name in ('QT_PLUGIN_PATH', 'QT_QPA_PLATFORM_PLUGIN_PATH', 'QT_QPA_PLATFORM',
                 'QT_QPA_PLATFORMTHEME', 'QT_QPA_GENERIC_PLUGINS'):
        os.environ.pop(name, None)
    os.environ['QT_PLUGIN_PATH'] = str(plugins)
    os.environ['QT_QPA_PLATFORM_PLUGIN_PATH'] = str(plugins / 'platforms')
    if sys.platform == 'win32':
        os.environ['QT_QPA_PLATFORM'] = 'windows'
    return plugins

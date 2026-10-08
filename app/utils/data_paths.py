"""Per-user paths; Windows retains the established database location."""
import os
import sys
from pathlib import Path


def data_directory():
    if sys.platform == 'win32':
        return Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData' / 'Local')) / 'OrangeNotes'
    from platformdirs import user_data_path
    return user_data_path('OrangeNotes', appauthor=False)


def database_path():
    return data_directory() / 'notes.sqlite3'

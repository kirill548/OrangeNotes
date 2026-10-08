import os
from pathlib import Path


def in_flatpak():
    return bool(os.environ.get('FLATPAK_ID')) or Path('/.flatpak-info').is_file()

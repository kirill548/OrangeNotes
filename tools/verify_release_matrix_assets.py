"""Fail CI if any required native format is absent or empty."""
import os
import zipfile
from pathlib import Path

def verify(root, target):
    patterns = {'Windows': ['OrangeNotes-Windows-AMD64.zip'],
                'Linux': ['*.deb', '*.AppImage', '*.flatpak'],
                'macOS': ['*.dmg', 'OrangeNotes.app/Contents/MacOS/OrangeNotes']}[target]
    for pattern in patterns:
        files=list(root.glob(pattern))
        if not files or any(not item.is_file() or item.stat().st_size == 0 for item in files):
            raise ValueError('Missing or empty required package: '+pattern)
    if target == 'Windows':
        with zipfile.ZipFile(root/'OrangeNotes-Windows-AMD64.zip') as archive:
            if archive.testzip() is not None:
                raise ValueError('ZIP checksum failure')
            if not any(name.endswith('OrangeNotes.exe') for name in archive.namelist()):
                raise ValueError('Windows executable absent from archive')
    return patterns

if __name__ == '__main__':
    print(verify(Path('dist'), os.environ['TARGET_OS']))

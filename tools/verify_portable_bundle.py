"""Verify that Lite packages include writable component roots, not user weights."""
import os
from pathlib import Path


def verify(root, target):
    root = Path(root)
    application = root / ('OrangeNotes.app' if target == 'macOS' else 'OrangeNotes')
    executable_root = application / 'Contents/MacOS' if target == 'macOS' else application
    for name in ('ai', 'stt'):
        component = executable_root / name
        if not component.is_dir() or component.is_symlink():
            raise ValueError(f'Missing ordinary portable component directory: {component}')
        if any(component.iterdir()):
            raise ValueError(f'Lite component directory must not contain developer data: {component}')
    multimedia = [path for path in application.rglob('*QtMultimedia*') if path.is_file()]
    if not multimedia:
        raise ValueError('QtMultimedia binding/library missing from frozen application')
    print('Portable ai/ and stt/ are empty; Qt Multimedia is bundled.')


if __name__ == '__main__':
    verify(Path('dist'), os.environ['TARGET_OS'])

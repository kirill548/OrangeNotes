"""Portable component paths: never fall back to a global user profile."""
import os
from pathlib import Path
import sys
import tempfile


def get_app_dir():
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def ai_dir():
    return get_app_dir() / 'ai'


def stt_dir():
    return get_app_dir() / 'stt'


def resolve_app_path(value):
    path = Path(value)
    candidate = path if path.is_absolute() else get_app_dir() / path
    root = get_app_dir().resolve()
    if not candidate.resolve().is_relative_to(root):
        raise ValueError('Компоненты должны находиться в папке приложения.')
    for item in (candidate, *candidate.parents):
        if item == root:
            break
        if item.is_symlink() or getattr(item, 'is_junction', lambda: False)():
            raise ValueError('Путь компонентов не должен содержать ссылки.')
    return candidate


def portable_app_path(value):
    path = resolve_app_path(value).resolve()
    root = get_app_dir().resolve()
    return path.relative_to(root).as_posix()


def check_app_writable(directory=None):
    """Probe write permission, reject component links and leave no probe file."""
    root = get_app_dir().resolve()
    folder = Path(directory) if directory is not None else root
    if not folder.resolve().is_relative_to(root):
        raise PermissionError('Компоненты должны находиться в папке приложения.')
    for item in (folder, *folder.parents):
        if item == root:
            break
        if item.is_symlink() or getattr(item, 'is_junction', lambda: False)():
            raise PermissionError('Папка компонентов не должна быть ссылкой.')
    try:
        folder.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix='.orange-write-', dir=folder)
        os.close(fd)
        Path(name).unlink()
    except OSError as exc:
        raise PermissionError('Нет прав записи в папку приложения. Перенесите приложение в доступную для записи папку.') from exc
    return folder

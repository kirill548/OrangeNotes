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
    lexical_root = get_app_dir().absolute()
    root = lexical_root.resolve()
    candidate = path.absolute() if path.is_absolute() else lexical_root / path
    # macOS /var and /tmp are trusted OS aliases above the application.
    # Canonicalize that prefix only; inspecting the full canonical path would
    # hide forbidden links *inside* the application directory.
    try:
        relative = candidate.relative_to(lexical_root)
    except ValueError:
        try:
            relative = candidate.relative_to(root)
        except ValueError as exc:
            raise ValueError('Компоненты должны находиться в папке приложения.') from exc
    candidate = root / relative
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
    try:
        folder = resolve_app_path(directory if directory is not None else root)
    except ValueError as exc:
        raise PermissionError(str(exc)) from exc
    try:
        folder.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix='.orange-write-', dir=folder)
        os.close(fd)
        Path(name).unlink()
    except OSError as exc:
        raise PermissionError('Нет прав записи в папку приложения. Перенесите приложение в доступную для записи папку.') from exc
    return folder

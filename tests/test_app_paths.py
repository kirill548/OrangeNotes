from pathlib import Path
import pytest
from app.services import app_paths


def test_source_root_ignores_python_installation():
    assert app_paths.get_app_dir() == Path(app_paths.__file__).resolve().parents[2]


def test_portable_relocation(monkeypatch, tmp_path):
    first = tmp_path / 'first'
    second = tmp_path / 'second'
    monkeypatch.setattr(app_paths.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(app_paths.sys, 'executable', str(first / 'OrangeNotes.exe'))
    stored = app_paths.portable_app_path(app_paths.ai_dir() / 'runtime/ollama.exe')
    assert stored == 'ai/runtime/ollama.exe'
    monkeypatch.setattr(app_paths.sys, 'executable', str(second / 'OrangeNotes.exe'))
    assert app_paths.resolve_app_path(stored) == second / stored
    assert app_paths.stt_dir() == second / 'stt'


def test_permission_error_does_not_fall_back(monkeypatch, tmp_path):
    monkeypatch.setattr(app_paths, 'get_app_dir', lambda: tmp_path)
    def denied(*args, **kwargs):
        raise PermissionError('denied')
    monkeypatch.setattr(app_paths.tempfile, 'mkstemp', denied)
    with pytest.raises(PermissionError, match='Нет прав записи'):
        app_paths.check_app_writable(tmp_path / 'stt')


def test_write_probe_cleans_up(monkeypatch, tmp_path):
    monkeypatch.setattr(app_paths, 'get_app_dir', lambda: tmp_path)
    directory = app_paths.check_app_writable(tmp_path / 'ai')
    assert list(directory.iterdir()) == []


def test_no_escape(monkeypatch, tmp_path):
    monkeypatch.setattr(app_paths, 'get_app_dir', lambda: tmp_path / 'app')
    with pytest.raises(PermissionError):
        app_paths.check_app_writable(tmp_path / 'outside')


@pytest.mark.parametrize('value', ['../outside', 'ai/../../outside'])
def test_relative_config_escape_rejected(monkeypatch, tmp_path, value):
    monkeypatch.setattr(app_paths, 'get_app_dir', lambda: tmp_path / 'app')
    with pytest.raises(ValueError):
        app_paths.resolve_app_path(value)


def test_external_absolute_config_rejected(monkeypatch, tmp_path):
    monkeypatch.setattr(app_paths, 'get_app_dir', lambda: tmp_path / 'app')
    with pytest.raises(ValueError):
        app_paths.portable_app_path(tmp_path / 'outside')


def test_linked_component_rejected(monkeypatch, tmp_path):
    root = tmp_path / 'app'
    root.mkdir()
    destination = root / 'other'
    destination.mkdir()
    try:
        (root / 'ai').symlink_to(destination, target_is_directory=True)
    except OSError:
        pytest.skip('Creating links requires platform permission')
    monkeypatch.setattr(app_paths, 'get_app_dir', lambda: root)
    with pytest.raises(ValueError):
        app_paths.resolve_app_path('ai/model.bin')

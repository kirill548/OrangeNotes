"""Portable packaging and offline STT contracts; no physical microphone claim."""
import ast
from pathlib import Path
import threading

import pytest

from app.services.local_stt import MAX_PCM_BYTES, STTError, WhisperCppAdapter


def test_component_paths_survive_application_relocation(tmp_path, monkeypatch):
    from app.services import app_paths
    first = tmp_path / 'first'
    second = tmp_path / 'second'
    monkeypatch.setattr(app_paths, 'get_app_dir', lambda: first)
    stored = app_paths.portable_app_path(app_paths.stt_dir() / 'ggml-tiny.bin')
    assert stored == 'stt/ggml-tiny.bin'
    assert app_paths.ai_dir() == first / 'ai'
    monkeypatch.setattr(app_paths, 'get_app_dir', lambda: second)
    assert app_paths.resolve_app_path(stored) == second / 'stt/ggml-tiny.bin'
    assert app_paths.ai_dir() == second / 'ai'


def test_packaging_collects_qt_multimedia_hook():
    source = (Path(__file__).resolve().parents[1] / 'tools/build.py').read_text(encoding='utf-8')
    tree = ast.parse(source)
    lists = [node for node in ast.walk(tree) if isinstance(node, ast.List)]
    assert any(any(isinstance(item, ast.Constant) and item.value == 'PySide6.QtMultimedia'
                   for item in node.elts) and
               any(isinstance(item, ast.Constant) and item.value == '--hidden-import'
                   for item in node.elts) for node in lists)


@pytest.mark.parametrize('platform', ['Windows x64', 'Linux x64', 'macOS Intel', 'macOS arm64'])
def test_missing_optional_runtime_is_unavailable(tmp_path, platform):
    # Platform labels describe the common portable contract, not native runs.
    adapter = WhisperCppAdapter(tmp_path / 'missing-engine', tmp_path / 'missing-model')
    assert adapter.available is False
    with pytest.raises(STTError, match='не настроен'):
        adapter.transcribe(b'\x00\x00')
    adapter.cancel()


def test_unicode_and_spaces_in_configured_paths(tmp_path):
    folder = tmp_path / 'Мои модели Whisper'
    folder.mkdir()
    engine = folder / 'whisper cli'
    model = folder / 'ggml-tiny-q5_1.bin'
    engine.write_bytes(b'fixture')
    model.write_bytes(b'fixture')
    adapter = WhisperCppAdapter(engine, model)
    assert adapter.available
    cancelled = threading.Event()
    cancelled.set()
    assert adapter.transcribe(b'\x00\x00', cancelled) == ''


@pytest.mark.parametrize('pcm', [b'', b'\x00', b'\x00' * (MAX_PCM_BYTES + 2)],
                         ids=['empty', 'odd', 'too_large'])
def test_invalid_pcm_never_launches_engine(tmp_path, pcm, monkeypatch):
    engine, model = tmp_path / 'engine', tmp_path / 'model'
    engine.touch()
    model.touch()
    def forbidden(*args, **kwargs):
        pytest.fail('Invalid PCM must not spawn a process')
    monkeypatch.setattr('app.services.local_stt.subprocess.Popen', forbidden)
    with pytest.raises(STTError, match='неверный формат'):
        WhisperCppAdapter(engine, model).transcribe(pcm)


def test_installed_components_remain_ready_after_whole_folder_move(tmp_path):
    import shutil
    from app.services.stt_installer import STTManager
    source = tmp_path / 'selected'
    source.mkdir()
    engine = source / 'whisper-cli'
    model = source / 'tiny.bin'
    engine.write_bytes(b'engine fixture')
    model.write_bytes(b'model fixture')
    original = tmp_path / 'Orange Notes'
    manager = STTManager(original / 'stt')
    assert manager.save_manual_paths(engine, model)['ready']
    relocated = tmp_path / 'Перенесённая папка'
    shutil.move(str(original), str(relocated))
    moved = STTManager(relocated / 'stt')
    assert moved.status()['ready']
    assert all(path.is_relative_to(relocated) for path in moved.component_paths())

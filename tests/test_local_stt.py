import threading
from pathlib import Path

import pytest

from app.services.local_stt import MAX_PCM_BYTES, STTError, WhisperCppAdapter


def test_missing_local_assets_do_not_download(tmp_path):
    adapter = WhisperCppAdapter(tmp_path / 'missing-cli', tmp_path / 'missing-model')
    assert not adapter.available
    with pytest.raises(STTError):
        adapter.transcribe(b'\0\0')
    assert not list(tmp_path.iterdir())


def test_reject_empty_oversized_and_partial_samples(tmp_path):
    executable, model = tmp_path / 'cli', tmp_path / 'model.bin'
    executable.touch()
    model.touch()
    adapter = WhisperCppAdapter(executable, model)
    for pcm in (b'', b'1', b'0' * (MAX_PCM_BYTES + 2)):
        with pytest.raises(STTError):
            adapter.transcribe(pcm)


def test_cancelled_before_launch(tmp_path):
    executable, model = tmp_path / 'cli', tmp_path / 'model.bin'
    executable.touch()
    model.touch()
    cancelled = threading.Event()
    cancelled.set()
    assert WhisperCppAdapter(executable, model).transcribe(b'\0\0', cancelled) == ''


def test_cpu_cli_wav_and_cleanup(monkeypatch, tmp_path):
    import wave
    from app.services import local_stt
    executable, model = tmp_path / 'cli', tmp_path / 'model.bin'
    executable.touch()
    model.touch()
    observed = {}
    class Process:
        returncode = 0
        def __init__(self, command, **kwargs):
            observed['command'] = command
            wav_path = Path(command[command.index('-f') + 1])
            observed['wav'] = wav_path
            with wave.open(str(wav_path)) as wav:
                assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (1, 2, 16000)
            Path(command[command.index('-of') + 1] + '.txt').write_text('Купить молоко', encoding='utf-8')
        def poll(self):
            return 0
    monkeypatch.setattr(local_stt.subprocess, 'Popen', Process)
    assert WhisperCppAdapter(executable, model).transcribe(b'\0\0' * 160) == 'Купить молоко'
    assert '-ng' in observed['command']
    assert not observed['wav'].exists()


def test_cancel_running_process_and_cleanup(monkeypatch, tmp_path):
    from app.services import local_stt
    executable, model = tmp_path / 'cli', tmp_path / 'model.bin'
    executable.touch()
    model.touch()
    cancelled = threading.Event()
    observed = {}
    class Process:
        returncode = None
        def __init__(self, command, **kwargs):
            observed['wav'] = Path(command[command.index('-f') + 1])
            cancelled.set()
        def poll(self):
            return self.returncode
        def terminate(self):
            self.returncode = -1
        def wait(self, timeout=None):
            return self.returncode
    monkeypatch.setattr(local_stt.subprocess, 'Popen', Process)
    assert WhisperCppAdapter(executable, model).transcribe(b'\0\0', cancelled) == ''
    assert not observed['wav'].exists()

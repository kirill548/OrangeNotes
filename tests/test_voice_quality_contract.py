"""Audio boundary contracts; mocked CLI output does not measure speech accuracy."""
from pathlib import Path
from types import SimpleNamespace
import struct
import wave

import pytest

from app.services import audio_capture, local_stt


def test_wav_preserves_sample_values_and_duration(monkeypatch, tmp_path):
    executable, model = tmp_path / 'cli', tmp_path / 'model.bin'
    executable.touch()
    model.touch()
    pcm = struct.pack('<hhhh', -32768, -1000, 1000, 32767) * 4000

    class Process:
        returncode = 0

        def __init__(self, command, **kwargs):
            with wave.open(command[command.index('-f') + 1], 'rb') as recording:
                assert recording.getframerate() == 16000
                assert recording.getnchannels() == 1
                assert recording.getsampwidth() == 2
                assert recording.getnframes() == 16000
                assert recording.readframes(16000) == pcm
            Path(command[command.index('-of') + 1] + '.txt').write_text(
                'Напомни завтра в четырнадцать часов', encoding='utf-8')

        def poll(self):
            return 0

    monkeypatch.setattr(local_stt.subprocess, 'Popen', Process)
    result = local_stt.WhisperCppAdapter(executable, model).transcribe(pcm)
    assert 'Напомни' in result


@pytest.mark.parametrize('annotation', ['[музыка]', '(музыка)', '♪♫', '[тишина] [музыка]', ''])
def test_sound_annotations_cannot_become_user_command(monkeypatch, tmp_path, annotation):
    executable, model = tmp_path / 'cli', tmp_path / 'model.bin'
    executable.touch()
    model.touch()

    class Process:
        returncode = 0

        def __init__(self, command, **kwargs):
            Path(command[command.index('-of') + 1] + '.txt').write_text(annotation, encoding='utf-8')

        def poll(self):
            return 0

    monkeypatch.setattr(local_stt.subprocess, 'Popen', Process)
    with pytest.raises(local_stt.STTError):
        local_stt.WhisperCppAdapter(executable, model).transcribe(b'\x01\x00' * 16000)


def test_selected_input_is_used_even_if_default_is_different(monkeypatch):
    selected = SimpleNamespace(id=lambda: b'user-microphone')
    default = SimpleNamespace(id=lambda: b'loopback-output')
    monkeypatch.setattr(audio_capture, 'input_device_id', lambda: b'user-microphone'.hex())
    monkeypatch.setattr(audio_capture, 'QMediaDevices', SimpleNamespace(
        audioInputs=lambda: [default, selected], defaultAudioInput=lambda: default))
    assert audio_capture.selected_microphone() is selected


def test_disconnected_selection_never_silently_uses_loopback(monkeypatch):
    default = SimpleNamespace(id=lambda: b'loopback-output')
    monkeypatch.setattr(audio_capture, 'input_device_id', lambda: b'user-microphone'.hex())
    monkeypatch.setattr(audio_capture, 'QMediaDevices', SimpleNamespace(
        audioInputs=lambda: [default], defaultAudioInput=lambda: default))
    with pytest.raises(RuntimeError, match='микрофон'):
        audio_capture.selected_microphone()

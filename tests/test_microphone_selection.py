from types import SimpleNamespace
import pytest
from app.services import audio_capture as capture


def device(identifier, name):
    return SimpleNamespace(id=lambda: identifier, description=lambda: name)


def test_selected_input_survives_restart(monkeypatch):
    first, second = device(b'first', 'Webcam'), device(b'second', 'Headset')
    monkeypatch.setattr(capture, 'QMediaDevices', SimpleNamespace(
        audioInputs=lambda: [first, second], defaultAudioInput=lambda: first))
    capture.save_input_device(b'second'.hex())
    assert capture.selected_microphone() is second
    assert capture.audio_inputs()[1] == (b'second'.hex(), 'Headset')


def test_disconnected_selection_never_silently_uses_other_input(monkeypatch):
    capture.save_input_device('missing')
    monkeypatch.setattr(capture, 'QMediaDevices', SimpleNamespace(audioInputs=lambda: []))
    with pytest.raises(RuntimeError, match='отключён'):
        capture.selected_microphone()


def test_system_default_input(monkeypatch):
    microphone = object()
    capture.save_input_device('')
    monkeypatch.setattr(capture, 'QMediaDevices', SimpleNamespace(defaultAudioInput=lambda: microphone))
    assert capture.selected_microphone() is microphone

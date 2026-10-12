"""Real Qt signal dispatch with mocked microphone/backend; no hardware needed."""
import threading
import time
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from app.services import audio_capture as capture


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


class Device(QObject):
    readyRead = Signal()
    def __init__(self):
        super().__init__()
        self.data = bytearray()
    def read(self, limit):
        result = bytes(self.data[:limit])
        del self.data[:limit]
        return result


class Source(QObject):
    stateChanged = Signal(object)
    def __init__(self, microphone, audio_format, parent):
        super().__init__(parent)
        self.device = Device()
        self.stopped = False
    def start(self):
        return self.device
    def error(self):
        return SimpleNamespace(value=0)
    def stop(self):
        self.stopped = True


class Format:
    SampleFormat = SimpleNamespace(Int16=1)
    def setSampleRate(self, value):
        assert value == 16000
    def setChannelCount(self, value):
        assert value == 1
    def setSampleFormat(self, value):
        assert value == 1


@pytest.fixture
def controller(app, monkeypatch):
    microphone = SimpleNamespace(isNull=lambda: False, isFormatSupported=lambda _format: True)
    monkeypatch.setattr(capture, 'QAudioFormat', Format)
    monkeypatch.setattr(capture, 'QMediaDevices', SimpleNamespace(defaultAudioInput=lambda: microphone))
    monkeypatch.setattr(capture, 'QAudioSource', Source)
    result = capture.VoiceInputController()
    result.adapter = SimpleNamespace(available=True, transcribe=lambda pcm, cancelled: 'Купить молоко', cancel=lambda: None)
    yield result
    result.cancel()
    if result._thread:
        result._thread.join(timeout=2)
    app.processEvents()


def wait_idle(app, controller):
    deadline = time.monotonic() + 2
    while controller.busy and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.001)
    assert not controller.busy


def test_capture_stream_async_text_and_cleanup(app, controller):
    states, text, finished = [], [], []
    controller.state_changed.connect(states.append)
    controller.text_ready.connect(text.append)
    controller.finished.connect(lambda: finished.append(True))
    controller.start()
    source = controller._source
    source.device.data.extend(b'\0\0' * 50)
    source.device.readyRead.emit()
    assert len(controller._pcm) == 100
    controller.stop()
    wait_idle(app, controller)
    assert states == ['recording', 'transcribing', 'idle']
    assert text == ['Купить молоко'] and finished == [True]
    assert source.stopped and not controller._pcm and controller._device is None


def test_capture_limit_stops_automatically(app, controller):
    received = []
    controller.adapter.transcribe = lambda pcm, cancelled: received.append(len(pcm)) or 'Текст'
    controller.start()
    source = controller._source
    source.device.data.extend(b'\0' * (capture.MAX_PCM_BYTES + 100))
    while controller._state == 'recording':
        source.device.readyRead.emit()
    wait_idle(app, controller)
    assert received == [capture.MAX_PCM_BYTES]


def test_cancel_recognition_suppresses_stale_text(app, controller):
    entered, release = threading.Event(), threading.Event()
    def transcribe(pcm, cancelled):
        entered.set()
        release.wait(1)
        return 'Не должен появиться'
    controller.adapter.transcribe = transcribe
    texts, errors, finished = [], [], []
    controller.text_ready.connect(texts.append)
    controller.error.connect(errors.append)
    controller.finished.connect(lambda: finished.append(True))
    controller.start()
    controller._device.data.extend(b'\0\0')
    controller.stop()
    assert entered.wait(1)
    controller.cancel()
    assert controller.busy  # worker still owns temporary recording
    release.set()
    wait_idle(app, controller)
    assert texts == [] and errors == [] and finished == [True]


@pytest.mark.parametrize('case', ['addon', 'model', 'microphone', 'format', 'provider_error'])
def test_early_failure_always_releases_ui(controller, monkeypatch, case):
    errors, finished = [], []
    controller.error.connect(errors.append)
    controller.finished.connect(lambda: finished.append(True))
    if case == 'addon':
        monkeypatch.setattr(capture, 'QAudioSource', None)
    elif case == 'model':
        controller.adapter.available = False
    elif case == 'microphone':
        monkeypatch.setattr(capture, 'QMediaDevices', SimpleNamespace(defaultAudioInput=lambda: SimpleNamespace(isNull=lambda: True, isFormatSupported=lambda _: False)))
    elif case == 'format':
        monkeypatch.setattr(capture, 'QMediaDevices', SimpleNamespace(defaultAudioInput=lambda: SimpleNamespace(isNull=lambda: False, isFormatSupported=lambda _: False)))
    else:
        def fail():
            raise RuntimeError('provider unavailable')
        monkeypatch.setattr(capture, 'QMediaDevices', SimpleNamespace(defaultAudioInput=fail))
        assert not controller.available
    controller.start()
    assert len(errors) == 1 and finished == [True] and not controller.busy


def test_cancel_recording_clears_buffer(controller):
    finished = []
    controller.finished.connect(lambda: finished.append(True))
    controller.start()
    controller._pcm.extend(b'\0\0')
    controller.cancel()
    assert not controller.busy and not controller._pcm and controller._source is None
    assert finished == [True]

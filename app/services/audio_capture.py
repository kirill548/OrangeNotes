"""Bounded Qt microphone capture and asynchronous local speech recognition."""
import threading
import json
from .app_paths import stt_dir, resolve_app_path


def input_device_id():
    try:
        value = json.loads(resolve_app_path(stt_dir() / 'audio_settings.json').read_text(encoding='utf-8')).get('input_device', '')
        return value if isinstance(value, str) else ''
    except (OSError, ValueError):
        return ''


def save_input_device(device_id):
    folder = resolve_app_path(stt_dir())
    folder.mkdir(parents=True, exist_ok=True)
    target = resolve_app_path(folder / 'audio_settings.json')
    temporary = resolve_app_path(target.with_suffix('.tmp'))
    temporary.write_text(json.dumps({'input_device': device_id}), encoding='utf-8')
    temporary.replace(target)


def audio_inputs():
    if QMediaDevices is None:
        return []
    try:
        return [(bytes(device.id()).hex(), device.description()) for device in QMediaDevices.audioInputs()]
    except (RuntimeError, OSError):
        return []


def selected_microphone():
    selected = input_device_id()
    if not selected:
        return QMediaDevices.defaultAudioInput()
    for device in QMediaDevices.audioInputs():
        if bytes(device.id()).hex() == selected:
            return device
    raise RuntimeError('Выбранный микрофон отключён. Выберите другой в настройках голосового ввода.')

from PySide6.QtCore import QObject, QTimer, Signal
from .local_stt import MAX_PCM_BYTES, SAMPLE_RATE, WhisperCppAdapter, STTError

try:
    from PySide6.QtMultimedia import QAudioFormat, QAudioSource, QMediaDevices
except (ImportError, OSError):
    QAudioFormat = QAudioSource = QMediaDevices = None


def audio_diagnostics():
    """Worker-safe capability report; no recording or permission request."""
    if QMediaDevices is None or QAudioSource is None:
        return dict(backend_available=False, microphone_available=False,
                    message='Аудиокомпоненты недоступны в этой сборке. Можно ввести текст вручную.')
    try:
        microphone = selected_microphone()
        available = not microphone.isNull()
        return dict(backend_available=True, microphone_available=available,
                    message='Микрофон доступен' if available else 'Микрофон не найден или отключён. Можно ввести текст вручную.')
    except (ImportError, OSError, RuntimeError):
        return dict(backend_available=False, microphone_available=False,
                    message='Не удалось получить доступ к микрофону. Проверьте аудиоустройства и разрешение на запись.')


class VoiceInputController(QObject):
    text_ready = Signal(str)
    state_changed = Signal(str)
    error = Signal(str)
    finished = Signal()
    _result = Signal(int, str, str)

    def __init__(self, parent=None, model_path=None, executable_path=None):
        super().__init__(parent)
        self.adapter = WhisperCppAdapter(executable_path, model_path)
        self._state = 'idle'
        self._source = self._device = None
        self._pcm = bytearray()
        self._cancelled = threading.Event()
        self._generation = 0
        self._thread = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.stop)
        self._result.connect(self._receive)

    @property
    def busy(self):
        return self._state != 'idle'

    @property
    def available(self):
        try:
            return bool(QMediaDevices is not None and self.adapter.available
                        and not selected_microphone().isNull())
        except Exception:
            return False

    def _fail(self, message):
        self.error.emit(message)
        self.finished.emit()

    def _set_state(self, state):
        self._state = state
        self.state_changed.emit(state)

    def start(self):
        if self.busy:
            return
        if QAudioSource is None:
            self._fail('Голосовой ввод недоступен в этой сборке.')
            return
        if not self.adapter.available:
            self._fail('Голосовой ввод не настроен: установите локальную модель Whisper.')
            return
        try:
            microphone = selected_microphone()
            audio_format = QAudioFormat()
            audio_format.setSampleRate(SAMPLE_RATE)
            audio_format.setChannelCount(1)
            audio_format.setSampleFormat(QAudioFormat.SampleFormat.Int16)
            supported = microphone.isFormatSupported(audio_format)
        except Exception:
            self._fail('Не удалось включить выбранный микрофон. Проверьте устройство в настройках голосового ввода и разрешение на запись.')
            return
        if microphone.isNull():
            self._fail('Микрофон не найден. Можно ввести текст вручную.')
            return
        if not supported:
            self._fail('Микрофон не поддерживает формат записи. Можно ввести текст вручную.')
            return
        self._generation += 1
        self._cancelled = threading.Event()
        self._pcm.clear()
        try:
            self._source = QAudioSource(microphone, audio_format, self)
            self._source.stateChanged.connect(self._audio_state)
            self._device = self._source.start()
            if self._device is None or getattr(self._source.error(), 'value', 0) != 0:
                raise RuntimeError('Микрофон недоступен.')
            self._device.readyRead.connect(self._read)
            self._set_state('recording')
            self._timer.start(60000)
        except Exception:
            self._release_audio()
            self._set_state('idle')
            self.error.emit('Не удалось включить микрофон. Проверьте разрешение на запись.')
            self.finished.emit()

    def _audio_state(self, _state):
        if self._source is not None and self._state == 'recording':
            try:
                error = self._source.error()
            except (RuntimeError, OSError):
                self.cancel()
                self.error.emit('Запись прервалась. Проверьте микрофон и разрешение на запись.')
                return
            # Enum comparison via value keeps compatibility across Qt releases.
            if getattr(error, 'value', 0) != 0:
                self.cancel()
                self.error.emit('Запись прервалась. Проверьте микрофон и разрешение на запись.')

    def _read(self):
        if self._device is None or self._state != 'recording':
            return
        remaining = MAX_PCM_BYTES - len(self._pcm)
        try:
            chunk = bytes(self._device.read(min(65536, remaining)))
        except (RuntimeError, OSError):
            self.cancel()
            self.error.emit('Запись прервалась. Проверьте микрофон и разрешение на запись.')
            return
        self._pcm.extend(chunk[:remaining])
        if len(self._pcm) >= MAX_PCM_BYTES:
            self.stop()

    def _release_audio(self):
        self._timer.stop()
        source, self._source = self._source, None
        device, self._device = self._device, None
        if device is not None:
            try:
                device.readyRead.disconnect(self._read)
            except (RuntimeError, TypeError):
                pass
        if source is not None:
            try:
                source.stop()
            except (RuntimeError, OSError):
                pass
            try:
                source.deleteLater()
            except RuntimeError:
                pass

    def stop(self):
        if self._state != 'recording':
            return
        # Drain what is already available, bounded independently of device claims.
        if self._device is not None:
            remaining = MAX_PCM_BYTES - len(self._pcm)
            try:
                self._pcm.extend(bytes(self._device.read(remaining))[:remaining])
            except (RuntimeError, OSError):
                self.cancel()
                self.error.emit('Запись прервалась. Проверьте микрофон и разрешение на запись.')
                return
        self._release_audio()
        pcm = bytes(self._pcm[:len(self._pcm) // 2 * 2])
        self._pcm.clear()
        self._set_state('transcribing')
        generation, cancelled = self._generation, self._cancelled
        def run():
            text = error = ''
            try:
                text = self.adapter.transcribe(pcm, cancelled)
            except STTError as exception:
                error = str(exception)
            except Exception:
                error = 'Не удалось распознать запись. Попробуйте ещё раз или введите текст.'
            try:
                self._result.emit(generation, text, error)
            except RuntimeError:
                pass  # Dialog was destroyed; WAV cleanup remains in adapter.
        self._thread = threading.Thread(target=run, name='orange-local-stt', daemon=True)
        self._thread.start()

    def cancel(self):
        if not self.busy:
            return
        self._cancelled.set()
        self.adapter.cancel()
        self._pcm.clear()
        self._release_audio()
        if self._state == 'recording':
            self._set_state('idle')
            self.finished.emit()
        # During recognition keep busy until worker cleans temporary files.

    def _receive(self, generation, text, error):
        if generation != self._generation:
            return
        cancelled = self._cancelled.is_set()
        self._thread = None
        self._set_state('idle')
        if not cancelled:
            if error:
                self.error.emit(error)
            elif text:
                self.text_ready.emit(text)
            else:
                self.error.emit('Речь не распознана. Попробуйте ещё раз.')
        self.finished.emit()

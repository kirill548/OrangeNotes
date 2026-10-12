"""Optional, offline whisper.cpp adapter. No model download or cloud fallback."""
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import wave
import re

SAMPLE_RATE = 16000
MAX_SECONDS = 60
MAX_PCM_BYTES = SAMPLE_RATE * 2 * MAX_SECONDS


class STTError(RuntimeError):
    pass


class WhisperCppAdapter:
    def __init__(self, executable_path, model_path, timeout=60, threads=2):
        self.executable = Path(executable_path) if executable_path else None
        self.model = Path(model_path) if model_path else None
        self.timeout = min(60, max(1, timeout))
        self.threads = min(4, max(1, threads))
        self._process = None
        self._lock = threading.Lock()

    @property
    def available(self):
        return bool(self.executable and self.model and self.executable.is_file()
                    and self.model.is_file())

    def cancel(self):
        with self._lock:
            process = self._process
        if process is not None and process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass

    def transcribe(self, pcm, cancelled=None):
        cancelled = cancelled or threading.Event()
        if not self.available:
            raise STTError('Голосовой ввод не настроен: нужен локальный движок и модель Whisper.')
        if not pcm or len(pcm) > MAX_PCM_BYTES or len(pcm) % 2:
            raise STTError('Запись пуста или имеет неверный формат.')
        if cancelled.is_set():
            return ''
        with tempfile.TemporaryDirectory(prefix='orange-stt-') as temporary:
            wav_path = Path(temporary) / 'recording.wav'
            output = Path(temporary) / 'transcript'
            with wave.open(str(wav_path), 'wb') as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(SAMPLE_RATE)
                wav.writeframes(pcm)
            # No shell, network, automatic language guessing or GPU dependency.
            command = [str(self.executable.resolve()), '-m', str(self.model.resolve()),
                       '-f', str(wav_path), '-l', 'ru', '-t', str(self.threads),
                       '-ng', '-otxt', '-of', str(output)]
            # Output goes to files: an unexpected CLI cannot fill RAM with logs.
            log_path = Path(temporary) / 'engine.log'
            with log_path.open('wb') as log:
                process = subprocess.Popen(command, stdout=log, stderr=log,
                                           stdin=subprocess.DEVNULL,
                                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                with self._lock:
                    self._process = process
                deadline = time.monotonic() + self.timeout
                try:
                    while process.poll() is None:
                        if log_path.stat().st_size > 1048576:
                            raise STTError('Движок вернул слишком большой ответ.')
                        if cancelled.wait(0.05) or time.monotonic() >= deadline:
                            process.terminate()
                            try:
                                process.wait(timeout=2)
                            except subprocess.TimeoutExpired:
                                process.kill()
                                process.wait(timeout=2)
                            if cancelled.is_set():
                                return ''
                            raise STTError('Распознавание заняло слишком много времени. Попробуйте более короткую запись.')
                    if cancelled.is_set():
                        return ''
                    if process.returncode:
                        raise STTError('Не удалось распознать запись локальной моделью.')
                    transcript = output.with_suffix('.txt')
                    if not transcript.is_file() or transcript.stat().st_size > 65536:
                        raise STTError('Движок не вернул корректный текст.')
                    text = transcript.read_text(encoding='utf-8').strip()
                    # Whisper may return sound annotations instead of speech.
                    if re.fullmatch(r'\s*(?:\[[^\]]+\]|\([^)]*\)|♪|♫|\s|[.,!])*', text):
                        raise STTError('Речь не распознана. Проверьте выбранный микрофон и повторите запись.')
                    from .spoken_time import normalize_spoken_time
                    return normalize_spoken_time(text)
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.wait(timeout=2)
                    with self._lock:
                        self._process = None

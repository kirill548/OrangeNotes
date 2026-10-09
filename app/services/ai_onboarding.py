"""Explicit local AI setup; detection never downloads or starts a daemon."""
import json
from pathlib import Path
import time

from app.services.local_ai import OllamaClient, LocalAIError
from app.services.local_runtime import ensure_runtime, runtime_candidates

REQUIRED_MODELS = ('qwen3:4b', 'qwen3-embedding:0.6b')
SETUP_TIMEOUT = 30 * 60


class PullProgress:
    """Bound progress metadata, not model files (Ollama downloads those itself)."""
    def __init__(self, model, callback=None):
        self.model, self.callback = model, callback
        self.pending = bytearray()
        self.size = 0
        self.success = False

    def feed(self, data):
        self.size += len(data)
        if self.size > 4 * 1024 * 1024:
            raise LocalAIError('Поток установки слишком большой.')
        self.pending.extend(data)
        while b'\n' in self.pending:
            line, _, self.pending = self.pending.partition(b'\n')
            self._line(line)
        if len(self.pending) > 256 * 1024:
            raise LocalAIError('Строка установки слишком большая.')

    def _line(self, line):
        if len(line) > 256 * 1024:
            raise LocalAIError('Строка установки слишком большая.')
        if not line.strip():
            return
        try:
            item = json.loads(line)
            if not isinstance(item, dict):
                raise ValueError()
            if item.get('error'):
                raise LocalAIError(str(item['error'])[:250])
            status = item.get('status')
            if not isinstance(status, str):
                raise ValueError()
            total, completed = item.get('total', 0), item.get('completed', 0)
            if type(total) is not int or type(completed) is not int or min(total, completed) < 0:
                raise ValueError()
        except (ValueError, UnicodeError) as error:
            raise LocalAIError('Некорректный ответ установки модели.') from error
        self.success = self.success or status == 'success'
        if self.callback:
            self.callback({'model': self.model, 'status': status[:250],
                           'percent': min(100, int(100 * completed / total)) if total else None})

    def finish(self):
        if self.pending.strip():
            self._line(self.pending)
        if not self.success:
            raise LocalAIError('Установка оборвалась до подтверждения готовности.')


def _pull(client, model, cancel_event, progress, deadline):
    """Called in setup worker; Qt timer aborts even a silent connection."""
    from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer, QUrl
    from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkProxy
    if QCoreApplication.instance() is None:
        raise LocalAIError('Установку следует запускать из приложения Orange Notes.')
    client._check_cancel(cancel_event)
    manager = QNetworkAccessManager()
    manager.setProxy(QNetworkProxy(QNetworkProxy.ProxyType.NoProxy))
    request = QNetworkRequest(QUrl(client.base_url + '/api/pull'))
    request.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, 'application/json')
    request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                         QNetworkRequest.RedirectPolicy.ManualRedirectPolicy)
    reply = manager.post(request, json.dumps({'model': model, 'stream': True}).encode())
    reply.setReadBufferSize(65536)
    loop, timer = QEventLoop(), QTimer()
    timer.setInterval(10)
    parser, failures = PullProgress(model, progress), []

    def consume():
        try:
            client._check_cancel(cancel_event)
            if time.monotonic() >= deadline:
                raise LocalAIError('Установка превысила лимит 30 минут. Повторите позже.')
            while reply.bytesAvailable():
                client._check_cancel(cancel_event)
                if time.monotonic() >= deadline:
                    raise LocalAIError('Установка превысила лимит 30 минут.')
                parser.feed(bytes(reply.read(65536)))
        except Exception as error:
            failures.append(error if isinstance(error, LocalAIError) else LocalAIError('Ошибка обработки хода установки.'))
            reply.abort()
            loop.quit()

    timer.timeout.connect(consume)
    reply.readyRead.connect(consume)
    reply.finished.connect(loop.quit)
    timer.start()
    if not reply.isFinished():
        loop.exec()
    consume()
    timer.stop()
    try:
        if failures:
            raise failures[0]
        status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        if status != 200 or reply.error() != reply.NetworkError.NoError:
            raise LocalAIError('Не удалось загрузить модель: ' + str(status or reply.errorString()))
        parser.finish()
    finally:
        reply.close()
        manager.deleteLater()


class ModelManager:
    def __init__(self, config):
        self.config = dict(config)
        self.client = OllamaClient(config.get('base_url', 'http://127.0.0.1:11434'),
                                   config.get('model', REQUIRED_MODELS[0]))
        self.runtime_root = config.get('runtime_root')

    def detect(self, cancel_event=None):
        self.client._check_cancel(cancel_event)
        started = time.perf_counter()
        result = self.client.probe()
        self.client._check_cancel(cancel_event)
        models = result.get('models', [])
        embedding = self.config.get('embedding_model', REQUIRED_MODELS[1])
        required = (self.client.model, embedding)
        return {**result, 'embedding_ready': embedding in models,
                'missing_models': [model for model in required if model not in models],
                'latency_ms': round((time.perf_counter() - started) * 1000, 3),
                'transport': 'NDJSON stream=true'}

    def connect_pack(self, path, cancel_event=None):
        root = Path(path).expanduser().resolve()
        if root.name == 'runtime':
            root = root.parent
        candidates = [(Path(exe).resolve(), Path(models).resolve()) for exe, models in runtime_candidates(root)]
        candidates = [(exe, models) for exe, models in candidates
                      if exe.is_relative_to(root) and models.is_relative_to(root) and exe.is_file() and models.is_dir()]
        if not candidates:
            raise LocalAIError('В папке нет совместимого ИИ-комплекта: нужны runtime/ollama и runtime/models для этой ОС.')
        self.client._check_cancel(cancel_event)
        if self.client.probe()['available']:
            raise LocalAIError('Ollama уже запущен. Используйте текущий сервер или закройте Ollama перед сменой ИИ-комплекта.')
        try:
            started = ensure_runtime(self.client, cancel_event, runtime_root=root)
        except OSError as error:
            raise LocalAIError('Нет доступа к ИИ-комплекту. Перенесите его в папку пользователя с правом записи.') from error
        if not started:
            self.client._check_cancel(cancel_event)
            raise LocalAIError('Не удалось запустить ИИ-комплект. Проверьте права папки и совместимость ОС.')
        self.runtime_root = str(root)
        return {**self.detect(cancel_event), 'runtime_root': self.runtime_root}

    def pull_missing(self, cancel_event=None, progress=None):
        state = self.detect(cancel_event)
        if not state['available']:
            raise LocalAIError('Сначала подключите локальный Ollama или готовый ИИ-комплект.')
        missing = state['missing_models']
        if any(model not in REQUIRED_MODELS for model in missing):
            raise LocalAIError('Автоматически устанавливаются только проверенные модели Qwen3.')
        deadline = time.monotonic() + SETUP_TIMEOUT
        for model in missing:
            self.client._check_cancel(cancel_event)
            _pull(self.client, model, cancel_event, progress, deadline)
        result = self.detect(cancel_event)
        if result['missing_models']:
            raise LocalAIError('Модели пока не появились в локальном сервисе. Повторите проверку.')
        return {**result, 'status': 'ready' if not missing else 'installed'}

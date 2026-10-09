"""Bounded NDJSON reception. Model text remains private until grounding completes."""
import json
import time
import socket
from app.services.local_ai import LocalAIError

MAX_RESPONSE = 4 * 1024 * 1024
MAX_LINE = 256 * 1024


class NDJSONAnswer:
    def __init__(self, on_content=None):
        self.on_content = on_content
        self.pending = bytearray()
        self.size = 0
        self.parts = []
        self.done = False
        self.reason = None
        self.aborted = False

    def abort(self):
        """Discard private model text on cancellation, truncation or protocol error."""
        self.pending.clear()
        self.parts.clear()
        self.aborted = True


    def feed(self, data):
        if self.aborted:
            raise LocalAIError("Поток локальной модели уже остановлен.")
        try:
            self._feed(data)
        except Exception:
            self.abort()
            raise

    def _feed(self, data):
        self.size += len(data)
        if self.size > MAX_RESPONSE:
            raise LocalAIError('Ответ локальной модели слишком большой.')
        self.pending.extend(data)
        while b'\n' in self.pending:
            line, _, tail = self.pending.partition(b'\n')
            self.pending = bytearray(tail)
            self._line(line)
        if len(self.pending) > MAX_LINE:
            raise LocalAIError('Строка потока локальной модели слишком большая.')

    def _line(self, line):
        if len(line) > MAX_LINE:
            raise LocalAIError('Строка потока локальной модели слишком большая.')
        if not line.strip():
            return
        try:
            item = json.loads(line)
            if not isinstance(item, dict):
                raise ValueError()
            if self.done:
                raise ValueError()
            if item.get('error'):
                raise LocalAIError('Локальная модель не выполнила запрос: '+str(item['error'])[:250])
            message = item.get('message', {})
            if not isinstance(message, dict):
                raise ValueError()
            content = message.get('content', '')
            if not isinstance(content, str) or type(item.get('done', False)) is not bool:
                raise ValueError()
            if content:
                self.parts.append(content)
                # Private worker callback: never connect raw model text to GUI signals.
                if self.on_content is not None:
                    self.on_content(content)
            self.done = item.get('done', False)
            self.reason = item.get('done_reason', self.reason)
        except (ValueError, UnicodeError) as error:
            raise LocalAIError('Локальный сервер вернул некорректный поток.') from error

    def finish(self):
        if self.aborted:
            raise LocalAIError('Поток локальной модели уже остановлен.')
        try:
            if self.pending.strip():
                self._line(self.pending)
            if not self.done:
                raise LocalAIError('Поток локальной модели оборвался до завершения ответа.')
            result = {'message': {'content': ''.join(self.parts)}, 'done_reason': self.reason}
            self.pending.clear()
            self.parts.clear()
            return result
        except Exception:
            self.abort()
            raise


def receive_stream(client, payload, cancel_event):
    from PySide6.QtCore import QCoreApplication
    if QCoreApplication.instance() is not None:
        return _receive_qt(client, payload, cancel_event)
    # Headless command-line benchmarks have no Qt application/event dispatcher.
    from urllib.request import Request
    deadline = min(client.deadline or time.monotonic()+30, time.monotonic()+client.timeout)
    parser = NDJSONAnswer(getattr(client, "on_stream_content", None))
    request = Request(client.base_url+'/api/chat', data=json.dumps(payload).encode('utf-8'),
                      headers={'Content-Type': 'application/json'}, method='POST')
    try:
        with client._opener.open(request, timeout=max(.001,deadline-time.monotonic())) as response:
            while True:
                client._check_cancel(cancel_event)
                remaining = deadline-time.monotonic()
                if remaining <= 0:
                    raise LocalAIError('Локальный помощник отвечает слишком долго.')
                sock = getattr(getattr(getattr(response, 'fp', None), 'raw', None), '_sock', None)
                if sock is not None:
                    sock.settimeout(remaining)
                data = response.read1(65536)
                if not data:
                    break
                parser.feed(data)
        client._check_cancel(cancel_event)
        return parser.finish()
    except LocalAIError:
        parser.abort()
        raise
    except (TimeoutError, socket.timeout) as error:
        parser.abort()
        client._check_cancel(cancel_event)
        raise LocalAIError("Локальный помощник отвечает слишком долго. Ответ не завершён.") from error
    except Exception as error:
        parser.abort()
        client._check_cancel(cancel_event)
        raise LocalAIError('Локальный помощник недоступен или отвечает слишком долго.') from error


def _receive_qt(client, payload, cancel_event):
    from PySide6.QtCore import QEventLoop, QTimer, QUrl, QCoreApplication, QEvent
    from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkProxy
    client._check_cancel(cancel_event)
    manager = QNetworkAccessManager()
    manager.setProxy(QNetworkProxy(QNetworkProxy.ProxyType.NoProxy))
    request = QNetworkRequest(QUrl(client.base_url+'/api/chat'))
    request.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader, 'application/json')
    request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                         QNetworkRequest.RedirectPolicy.ManualRedirectPolicy)
    reply = manager.post(request, json.dumps(payload,ensure_ascii=False).encode('utf-8'))
    reply.setReadBufferSize(65536)
    loop = QEventLoop()
    timer = QTimer()
    timer.setInterval(10)
    deadline = min(client.deadline or time.monotonic()+30, time.monotonic()+client.timeout)
    parser = NDJSONAnswer(getattr(client, "on_stream_content", None))
    failures = []

    def consume():
        try:
            client._check_cancel(cancel_event)
            if time.monotonic() >= deadline:
                raise LocalAIError('Локальный помощник отвечает слишком долго.')
            while reply.bytesAvailable():
                client._check_cancel(cancel_event)
                if time.monotonic() >= deadline:
                    raise LocalAIError('Локальный помощник отвечает слишком долго.')
                parser.feed(bytes(reply.read(65536)))
        except Exception as error:
            parser.abort()
            failures.append(error if isinstance(error, LocalAIError) else
                            LocalAIError("Ошибка проверки потока локальной модели. Ответ остановлен."))
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
            raise LocalAIError('Локальный сервер вернул ошибку потока: '+str(status or reply.errorString()))
        return parser.finish()
    finally:
        parser.abort()
        reply.close()
        # Drain deletion in the owning worker while its dispatcher still exists.
        # Leaving the network manager to QThread teardown races Cocoa shutdown.
        reply.deleteLater()
        manager.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

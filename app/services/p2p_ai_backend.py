"""Read-only desktop Companion bridge; only explicitly uploaded context is visible.

Model bytes never go directly onto the LAN. The existing complete grounding
validator authorizes the answer before verified fragments are streamed.
"""
import asyncio
import json
import threading
import time

from app.database.store import plain_body
from app.services.companion import _SYSTEM, evidence_prefix
from app.services.local_ai import OllamaClient, LocalAIError, LocalAICancelled
from app.services.rag_context import fit_messages
from app.services.stream_validator import StreamSegmentValidator
from app.services.p2p_ai_protocol import validate_request


class GroundedOllamaBackend:
    def __init__(self, *, model='qwen3:4b', base_url='http://127.0.0.1:11434', client_factory=None):
        # Administrative configuration only. A mobile request cannot choose an URL,
        # model, system prompt, tools, filesystem path or desktop database.
        from urllib.parse import urlsplit
        endpoint = urlsplit(base_url)
        if (endpoint.scheme != 'http' or endpoint.hostname not in ('127.0.0.1', '::1', 'localhost')
                or endpoint.path not in ('', '/') or endpoint.query or endpoint.fragment
                or endpoint.username or endpoint.password):
            raise ValueError('AI backend must be an explicit loopback Ollama endpoint')
        self.model, self.base_url = model, base_url
        self.client_factory = client_factory or OllamaClient

    def _answer(self, request, cancel):
        sources = []
        for note in request['contexts']:
            text = evidence_prefix(plain_body(note['body']))
            if text.strip():
                sources.append({'excerpt': text, 'title': note['title'], 'note_id': note['note_id']})
        if not sources:
            # No ungrounded free-form generation is silently enabled by relay.
            return ['В прикреплённых заметках нет информации об этом. Прикрепите заметку с нужными данными.']
        packet = [dict(source_id=i, title=s['title'], text=s['excerpt'], source_kind='attachment')
                  for i, s in enumerate(sources, 1)]
        messages = fit_messages([{'role': 'system', 'content': _SYSTEM +
            '\nЭтот канал только для чтения. Не создавайте задачи, не сохраняйте записи и не обещайте выполненных действий.'},
            {'role': 'user', 'content': json.dumps({'mode': 'memory', 'question': request['prompt'], 'sources': packet}, ensure_ascii=False)}])
        if cancel.is_set():
            raise LocalAICancelled('Запрос отменён.')
        client = self.client_factory(base_url=self.base_url, model=self.model)
        client.cancel_event, client.deadline = cancel, time.monotonic() + 30
        validator = StreamSegmentValidator(sources=sources, max_buffer_chars=65536,
            revision_check=lambda: not cancel.is_set())
        client.on_stream_content = validator.feed
        try:
            raw = client.chat_stream(messages, cancel_event=cancel, json_mode='memory')
            if cancel.is_set():
                raise LocalAICancelled('Запрос отменён.')
            # Validate the whole response, not just an apparently valid prefix.
            return validator.validate_packet(raw, sources, strict_memory=True)
        finally:
            client.on_stream_content = None
            validator.abort()

    async def __call__(self, request):
        validate_request(request)
        cancel = threading.Event()
        task = asyncio.create_task(asyncio.to_thread(self._answer, request, cancel))
        try:
            chunks = await asyncio.shield(task)
            for text in chunks:
                if cancel.is_set():
                    return
                yield text
        finally:
            cancel.set()
            # OllamaClient polls cancellation/deadline; do not orphan its worker.
            if not task.done():
                try:
                    await asyncio.wait_for(asyncio.shield(task), 2)
                except (Exception, asyncio.CancelledError):
                    # Observe a late exception without publishing its contents.
                    task.add_done_callback(lambda future: None if future.cancelled() else future.exception())


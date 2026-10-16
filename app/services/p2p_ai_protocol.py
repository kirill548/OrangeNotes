"""Opt-in, read-only AI relay on a separate mutual-TLS endpoint.

Wire format is length-prefixed JSON, not HTTP/SSE. Discovery and sync grants
never authorize AI requests. Backend adapters must enforce grounded generation.
"""
from __future__ import annotations

import asyncio
import json
import struct
from uuid import uuid4

from .p2p_transport import (PeerIdentity, TransportError, _check_context,
                           _close, _fingerprint, _pairs, _uuid)

MAX_FRAME = 65536
MAX_OUTPUT = 65536
MAX_PROMPT = 8192
MAX_CONTEXT = 32768


class AIRelayError(TransportError):
    pass


def _text(value, limit):
    if not isinstance(value, str) or len(value.encode('utf-8')) > limit:
        raise AIRelayError('Text limit exceeded')
    return value


def validate_request(value):
    if not isinstance(value, dict) or set(value) != {
            'version', 'type', 'request_id', 'actor_id', 'workspace_id', 'prompt', 'contexts'}:
        raise AIRelayError('Invalid request fields')
    if type(value['version']) is not int or value['version'] != 1 or value['type'] != 'ai.request':
        raise AIRelayError('Invalid request version/type')
    for field in ('request_id', 'actor_id', 'workspace_id'):
        _uuid(value[field])
    if not _text(value['prompt'], MAX_PROMPT).strip():
        raise AIRelayError('Empty prompt')
    contexts = value['contexts']
    if not isinstance(contexts, list) or len(contexts) > 8:
        raise AIRelayError('Context count exceeded')
    total, seen = 0, set()
    for note in contexts:
        if not isinstance(note, dict) or set(note) != {'note_id', 'title', 'body'}:
            raise AIRelayError('Invalid note context')
        note_id = _uuid(note['note_id'])
        if note_id in seen:
            raise AIRelayError('Duplicate note context')
        seen.add(note_id)
        total += len(_text(note['title'], 1024).encode('utf-8'))
        total += len(_text(note['body'], MAX_CONTEXT).encode('utf-8'))
    if total > MAX_CONTEXT:
        raise AIRelayError('Context byte limit exceeded')
    return value


async def read_frame(reader):
    size = struct.unpack('!I', await reader.readexactly(4))[0]
    if not 0 < size <= MAX_FRAME:
        raise AIRelayError('Frame limit exceeded')
    try:
        value = json.loads((await reader.readexactly(size)).decode('utf-8'), object_pairs_hook=_pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(AIRelayError('Invalid number')))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise AIRelayError('Malformed frame') from exc
    if not isinstance(value, dict):
        raise AIRelayError('Object required')
    return value


async def write_frame(writer, value):
    data = json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')
    if len(data) > MAX_FRAME:
        raise AIRelayError('Frame limit exceeded')
    writer.write(struct.pack('!I', len(data)) + data)
    await writer.drain()


class AIRelayServer:
    def __init__(self, context, ai_peers, backend, *, timeout=30, max_connections=4):
        _check_context(context)
        if not 0 < timeout <= 30 or not 1 <= max_connections <= 4:
            raise AIRelayError('Invalid relay limits')
        self.context, self.backend = context, backend
        self.timeout, self.max_connections = timeout, max_connections
        self.ai_peers = {(p.fingerprint_sha256, p.actor_id, p.workspace_id): p for p in ai_peers}
        self._server, self._tasks = None, set()
        self._active_grants = {}
        self.rejected = 0

    @property
    def address(self):
        return self._server.sockets[0].getsockname()[:2] if self._server else None

    def revoke_peer(self, actor_id, workspace_id):
        self.ai_peers = {k: p for k, p in self.ai_peers.items()
                         if (p.actor_id, p.workspace_id) != (actor_id, workspace_id)}
        for task, grant in tuple(self._active_grants.items()):
            if grant[1:] == (actor_id, workspace_id):
                task.cancel()

    async def start(self, host='127.0.0.1', port=0):
        if self._server is not None:
            raise AIRelayError('Already started')
        self._server = await asyncio.start_server(self._accept, host, port, ssl=self.context,
            ssl_handshake_timeout=self.timeout, ssl_shutdown_timeout=1, limit=MAX_FRAME + 4)
        return self.address

    async def _accept(self, reader, writer):
        task = asyncio.current_task()
        if len(self._tasks) >= self.max_connections:
            self.rejected += 1
            await _close(writer)
            return
        self._tasks.add(task)
        stream = None
        disconnected = None
        request = None
        sequence = 0
        try:
            async with asyncio.timeout(self.timeout):
                pin = _fingerprint(writer)
                if not any(k[0] == pin for k in self.ai_peers):
                    raise AIRelayError('AI access denied')
                request = validate_request(await read_frame(reader))
                grant = (pin, request['actor_id'], request['workspace_id'])
                if grant not in self.ai_peers:
                    raise AIRelayError('AI access denied')
                self._active_grants[task] = grant
                stream = self.backend(request)
                # A single request per connection. EOF or extra input cancels generation.
                disconnected = asyncio.create_task(reader.read(1))
                total = 0
                while True:
                    if grant not in self.ai_peers:
                        raise AIRelayError('AI access revoked')
                    next_token = asyncio.create_task(anext(stream))
                    try:
                        done, _ = await asyncio.wait((next_token, disconnected), return_when=asyncio.FIRST_COMPLETED)
                        if disconnected in done:
                            raise AIRelayError('Client disconnected')
                        try:
                            token = next_token.result()
                        except StopAsyncIteration:
                            break
                    finally:
                        if not next_token.done():
                            next_token.cancel()
                        await asyncio.gather(next_token, return_exceptions=True)
                    if grant not in self.ai_peers:
                        raise AIRelayError('AI access revoked')
                    total += len(_text(token, MAX_OUTPUT).encode('utf-8'))
                    if total > MAX_OUTPUT:
                        raise AIRelayError('Output limit exceeded')
                    if not token:
                        continue
                    await write_frame(writer, dict(version=1, type='ai.token', request_id=request['request_id'],
                                                   sequence=sequence, text=token))
                    sequence += 1
                if grant not in self.ai_peers:
                    raise AIRelayError('AI access revoked')
                await write_frame(writer, dict(version=1, type='ai.done', request_id=request['request_id'], sequence=sequence))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.rejected += 1
            if request is not None:
                try:
                    code = 'deadline' if isinstance(exc, TimeoutError) else 'relay_failed'
                    await asyncio.wait_for(write_frame(writer, dict(version=1, type='ai.error',
                        request_id=request['request_id'], sequence=sequence, error_code=code)), .25)
                except Exception:
                    pass
        finally:
            if disconnected is not None:
                disconnected.cancel()
                await asyncio.gather(disconnected, return_exceptions=True)
            try:
                if stream is not None and hasattr(stream, 'aclose'):
                    try:
                        await asyncio.wait_for(stream.aclose(), 1)
                    except (Exception, asyncio.CancelledError):
                        pass
            finally:
                await _close(writer)
                self._active_grants.pop(task, None)
                self._tasks.discard(task)

    async def close(self):
        server = self._server
        if server is not None:
            server.close()
            self._server = None
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if server is not None:
            await server.wait_closed()


class AIRelayClient:
    def __init__(self, context, local_actor_id, workspace_id, peer: PeerIdentity, *, timeout=30):
        _check_context(context)
        if not context.check_hostname or not 0 < timeout <= 30:
            raise AIRelayError('Verified hostname and bounded deadline required')
        self.context, self.actor, self.workspace, self.peer = context, _uuid(local_actor_id), _uuid(workspace_id), peer
        if peer.workspace_id != workspace_id:
            raise AIRelayError('Peer workspace mismatch')
        self.timeout = timeout

    async def stream(self, host, port, prompt, contexts=(), *, request_id=None, server_hostname='localhost'):
        request = validate_request(dict(version=1, type='ai.request', request_id=request_id or str(uuid4()),
            actor_id=self.actor, workspace_id=self.workspace, prompt=prompt, contexts=list(contexts)))
        writer = None
        try:
            deadline = asyncio.get_running_loop().time() + self.timeout
            reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port, ssl=self.context,
                server_hostname=server_hostname, ssl_handshake_timeout=self.timeout, ssl_shutdown_timeout=1,
                limit=MAX_FRAME + 4), self.timeout)
            if _fingerprint(writer) != self.peer.fingerprint_sha256:
                raise AIRelayError('Server certificate pin mismatch')
            await asyncio.wait_for(write_frame(writer, request), max(0, deadline - asyncio.get_running_loop().time()))
            sequence, total = 0, 0
            while True:
                frame = await asyncio.wait_for(read_frame(reader), max(0, deadline - asyncio.get_running_loop().time()))
                kind = frame.get('type')
                extra = {'text'} if kind == 'ai.token' else {'error_code'} if kind == 'ai.error' else set()
                if (set(frame) != {'version', 'type', 'request_id', 'sequence'} | extra or
                    type(frame.get('version')) is not int or frame['version'] != 1 or
                    frame.get('request_id') != request['request_id'] or
                    type(frame.get('sequence')) is not int or frame['sequence'] != sequence):
                    raise AIRelayError('Invalid stream envelope')
                if kind == 'ai.done':
                    return
                if kind == 'ai.error':
                    raise AIRelayError('Relay failed: ' + _text(frame['error_code'], 64))
                if kind != 'ai.token':
                    raise AIRelayError('Unknown stream frame')
                token = _text(frame['text'], MAX_OUTPUT)
                total += len(token.encode('utf-8'))
                if total > MAX_OUTPUT:
                    raise AIRelayError('Output limit exceeded')
                sequence += 1
                yield token
        finally:
            if writer is not None:
                await _close(writer)

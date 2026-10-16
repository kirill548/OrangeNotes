"""Actual TLS sockets for the opt-in mobile read-only AI relay."""
import asyncio
import struct
from uuid import uuid4

import pytest

from test_p2p_network import certificates
from app.services.p2p_transport import PeerIdentity, client_context, server_context
from app.services.p2p_ai_protocol import (AIRelayServer, AIRelayClient, AIRelayError,
                                         validate_request, write_frame, read_frame)


def setup(certs, backend, timeout=2):
    a, b, space = str(uuid4()), str(uuid4()), str(uuid4())
    ap = PeerIdentity(a, space, certs['a'][2])
    bp = PeerIdentity(b, space, certs['b'][2])
    server = AIRelayServer(server_context(*certs['b'][:2], certs['ca']), [ap], backend, timeout=timeout)
    client = AIRelayClient(client_context(*certs['a'][:2], certs['ca']), a, space, bp, timeout=timeout)
    return server, client


def test_real_mobile_prompt_and_grounded_token_stream(certificates):
    received = []
    async def backend(request):
        received.append(request)
        yield 'В заметке: '
        yield request['contexts'][0]['body']
    async def run():
        server, client = setup(certificates, backend)
        try:
            address = await server.start()
            note = dict(note_id=str(uuid4()), title='План', body='Созвон в 14:00')
            text = ''.join([token async for token in client.stream(*address, 'Что запланировано?', [note])])
            assert text == 'В заметке: Созвон в 14:00'
            assert received[0]['contexts'] == [note]
        finally:
            await server.close()
        assert not server._tasks
    asyncio.run(run())


@pytest.mark.parametrize('failure', ['server_pin', 'client_pin', 'actor', 'workspace', 'revoked', 'hostname'])
def test_identity_and_explicit_ai_grant_fail_closed(certificates, failure):
    called = []
    async def backend(request):
        called.append(request)
        yield 'never'
    async def run():
        server, client = setup(certificates, backend)
        if failure == 'server_pin':
            client.peer = PeerIdentity(client.peer.actor_id, client.workspace, '0' * 64)
        elif failure == 'client_pin':
            server.ai_peers = {('0' * 64, client.actor, client.workspace): client.peer}
        elif failure == 'actor':
            client.actor = str(uuid4())
        elif failure == 'workspace':
            client.workspace = str(uuid4())
        elif failure == 'revoked':
            server.revoke_peer(client.actor, client.workspace)
        try:
            address = await server.start()
            with pytest.raises(Exception):
                _ = [t async for t in client.stream(*address, 'Привет', server_hostname='wrong.invalid' if failure == 'hostname' else 'localhost')]
            assert not called
        finally:
            await server.close()
    asyncio.run(run())


@pytest.mark.parametrize('failure', ['extra', 'version_bool', 'prompt_limit', 'contexts_limit', 'note_extra', 'duplicate', 'invalid_uuid'])
def test_strict_request_schema(failure):
    request = dict(version=1, type='ai.request', request_id=str(uuid4()), actor_id=str(uuid4()),
                   workspace_id=str(uuid4()), prompt='Привет', contexts=[])
    note = dict(note_id=str(uuid4()), title='Заметка', body='Контекст')
    if failure == 'extra': request['tools'] = [{'name': 'delete_note'}]
    elif failure == 'version_bool': request['version'] = True
    elif failure == 'prompt_limit': request['prompt'] = 'я' * 4097
    elif failure == 'contexts_limit': request['contexts'] = [dict(note, note_id=str(uuid4())) for _ in range(9)]
    elif failure == 'note_extra': request['contexts'] = [dict(note, workspace_id=str(uuid4()))]
    elif failure == 'duplicate': request['contexts'] = [note, note]
    elif failure == 'invalid_uuid': request['request_id'] = '../../private'
    with pytest.raises(ValueError): validate_request(request)


@pytest.mark.parametrize('failure', ['deadline', 'output_limit', 'backend_error'])
def test_backend_failures_never_complete_successfully(certificates, failure):
    closed = []
    async def backend(request):
        try:
            if failure == 'deadline': await asyncio.sleep(10)
            elif failure == 'backend_error': raise RuntimeError('secret internal message')
            else:
                yield 'a' * 40000
                yield 'b' * 40000
        finally:
            closed.append(True)
    async def run():
        server, client = setup(certificates, backend, timeout=.2 if failure == 'deadline' else 2)
        try:
            address = await server.start()
            with pytest.raises((AIRelayError, TimeoutError, asyncio.IncompleteReadError)):
                _ = [t async for t in client.stream(*address, 'Привет')]
        finally:
            await server.close()
        assert closed and not server._tasks
    asyncio.run(run())


def test_client_disconnect_cancels_waiting_backend(certificates):
    closed = asyncio.Event()
    async def backend(request):
        try:
            yield 'Первый'
            await asyncio.sleep(10)
        finally:
            closed.set()
    async def run():
        server, client = setup(certificates, backend)
        try:
            address = await server.start()
            stream = client.stream(*address, 'Привет')
            assert await anext(stream) == 'Первый'
            await stream.aclose()
            await asyncio.wait_for(closed.wait(), 1)
        finally:
            await server.close()
        assert not server._tasks
    asyncio.run(run())


def test_revocation_during_generation_stops_further_tokens(certificates):
    async def run():
        proceed = asyncio.Event()
        async def backend(request):
            yield 'Первый'
            await proceed.wait()
            yield 'Секрет'
        server, client = setup(certificates, backend)
        try:
            address = await server.start()
            stream = client.stream(*address, 'Привет')
            assert await anext(stream) == 'Первый'
            server.revoke_peer(client.actor, client.workspace)
            proceed.set()
            with pytest.raises((AIRelayError, asyncio.IncompleteReadError)): await anext(stream)
            await stream.aclose()
        finally:
            await server.close()
    asyncio.run(run())


def test_revocation_cancels_backend_without_waiting_for_next_token(certificates):
    async def run():
        closed = asyncio.Event()
        async def backend(request):
            try:
                yield 'Первый'
                await asyncio.sleep(100)
            finally:
                closed.set()
        server, client = setup(certificates, backend)
        try:
            address = await server.start()
            stream = client.stream(*address, 'Привет')
            assert await anext(stream) == 'Первый'
            server.revoke_peer(client.actor, client.workspace)
            await asyncio.wait_for(closed.wait(), 1)
            with pytest.raises(asyncio.IncompleteReadError):
                await anext(stream)
            await stream.aclose()
        finally:
            await server.close()
        assert not server._active_grants and not server._tasks
    asyncio.run(run())


def test_concurrent_connection_limit_and_shutdown_cancel_backend(certificates):
    async def run():
        closed = asyncio.Event()
        async def backend(request):
            try:
                yield 'Первый'
                await asyncio.sleep(100)
            finally:
                closed.set()
        server, client = setup(certificates, backend)
        server.max_connections = 1
        try:
            address = await server.start()
            first = client.stream(*address, 'Привет')
            assert await anext(first) == 'Первый'
            with pytest.raises((asyncio.IncompleteReadError, ConnectionError)):
                _ = [t async for t in client.stream(*address, 'Второй')]
            await server.close()
            await asyncio.wait_for(closed.wait(), 1)
            await first.aclose()
        finally:
            await server.close()
        assert not server._tasks
    asyncio.run(run())


def test_wire_extra_fields_rejected_before_backend(certificates):
    calls = []
    async def backend(request):
        calls.append(request)
        yield 'never'
    async def run():
        server, client = setup(certificates, backend)
        writer = None
        try:
            address = await server.start()
            reader, writer = await asyncio.open_connection(*address, ssl=client.context, server_hostname='localhost')
            await write_frame(writer, dict(version=1, type='ai.request', request_id=str(uuid4()), actor_id=client.actor,
                workspace_id=client.workspace, prompt='Привет', contexts=[], tools=['delete_note']))
            with pytest.raises(asyncio.IncompleteReadError): await read_frame(reader)
            assert calls == []
        finally:
            if writer:
                writer.close()
                await writer.wait_closed()
            await server.close()
    asyncio.run(run())


@pytest.mark.parametrize('payload', [b'{"version":1,"version":1}', b'[]', b'{', b'\xff', b'NaN'])
def test_malformed_wire_never_reaches_backend(certificates, payload):
    calls = []
    async def backend(request):
        calls.append(request)
        yield 'never'
    async def run():
        server, client = setup(certificates, backend)
        writer = None
        try:
            address = await server.start()
            reader, writer = await asyncio.open_connection(*address, ssl=client.context, server_hostname='localhost')
            writer.write(struct.pack('!I', len(payload)) + payload)
            await writer.drain()
            with pytest.raises(asyncio.IncompleteReadError):
                await read_frame(reader)
            assert not calls
        finally:
            if writer:
                writer.close()
                await writer.wait_closed()
            await server.close()
    asyncio.run(run())


def test_paused_stream_does_not_cancel_unrelated_consumer_work(certificates):
    async def run():
        async def backend(request):
            yield 'Первый'
            await asyncio.sleep(10)
        server, client = setup(certificates, backend, timeout=.1)
        try:
            address = await server.start()
            stream = client.stream(*address, 'Привет')
            assert await anext(stream) == 'Первый'
            # Deadline may expire while the consumer renders a token. It must
            # never inject cancellation into that unrelated rendering task.
            await asyncio.sleep(.2)
            with pytest.raises(TimeoutError):
                await anext(stream)
            await stream.aclose()
        finally:
            await server.close()
    asyncio.run(run())

"""Opt-in TLS 1.3 transport. Discovery never grants trust; pins are paired out of band.

ACK is emitted only after the receiver's durable transaction callback completes.
One bounded delta per connection makes replay/retry semantics explicit in the store.
"""
from __future__ import annotations
import asyncio
from dataclasses import dataclass
import hashlib
import inspect
import json
import re
import ssl
import struct
from uuid import UUID
from .p2p_sync import MAX_BYTES

MAX_FRAME = 2 * MAX_BYTES + 4096

class TransportError(ValueError):
    pass

def _uuid(value):
    try:
        parsed = UUID(value)
        if parsed.version != 4 or str(parsed) != value:
            raise ValueError()
    except (ValueError, TypeError, AttributeError) as exc:
        raise TransportError('Canonical UUIDv4 required') from exc
    return value

@dataclass(frozen=True)
class PeerIdentity:
    actor_id: str
    workspace_id: str
    fingerprint_sha256: str

    def __post_init__(self):
        _uuid(self.actor_id)
        _uuid(self.workspace_id)
        if not isinstance(self.fingerprint_sha256, str) or not re.fullmatch('[0-9a-f]{64}', self.fingerprint_sha256):
            raise TransportError('Full lowercase SHA256 certificate fingerprint required')

def certificate_fingerprint(der: bytes) -> str:
    return hashlib.sha256(der).hexdigest()

def server_context(cert, key, ca):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = context.maximum_version = ssl.TLSVersion.TLSv1_3
    context.verify_mode = ssl.CERT_REQUIRED
    context.load_verify_locations(cafile=str(ca))
    context.load_cert_chain(str(cert), str(key))
    return context

def client_context(cert, key, ca):
    context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH, cafile=str(ca))
    context.minimum_version = context.maximum_version = ssl.TLSVersion.TLSv1_3
    context.load_cert_chain(str(cert), str(key))
    return context

def _check_context(context):
    if context.minimum_version != ssl.TLSVersion.TLSv1_3 or context.maximum_version != ssl.TLSVersion.TLSv1_3 or context.verify_mode != ssl.CERT_REQUIRED:
        raise TransportError('Verified TLS 1.3 context required')

def _fingerprint(writer):
    tls = writer.get_extra_info('ssl_object')
    if tls is None or tls.version() != 'TLSv1.3':
        raise TransportError('TLS 1.3 required')
    return certificate_fingerprint(tls.getpeercert(binary_form=True))

def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise TransportError('Duplicate JSON field')
        result[key] = value
    return result

async def _read(reader):
    size = struct.unpack('!I', await reader.readexactly(4))[0]
    if not 0 < size <= MAX_FRAME:
        raise TransportError('Frame limit exceeded')
    try:
        value = json.loads((await reader.readexactly(size)).decode('utf-8'), object_pairs_hook=_pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(TransportError('Invalid number')))
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise TransportError('Malformed frame') from exc
    if not isinstance(value, dict):
        raise TransportError('Object required')
    return value

async def _write(writer, value):
    data = json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')
    if len(data) > MAX_FRAME:
        raise TransportError('Frame limit exceeded')
    writer.write(struct.pack('!I', len(data)) + data)
    await writer.drain()

def _delta(wire, actor, workspace):
    if not isinstance(wire, str) or len(wire.encode('utf-8')) > MAX_BYTES:
        raise TransportError('Delta byte limit exceeded')
    try:
        value = json.loads(wire, object_pairs_hook=_pairs)
    except (ValueError, RecursionError) as exc:
        raise TransportError('Malformed delta') from exc
    if not isinstance(value, dict) or value.get('actor_id') != actor or value.get('workspace_id') != workspace:
        raise TransportError('Delta identity mismatch')
    return hashlib.sha256(wire.encode('utf-8')).hexdigest()

async def _close(writer):
    writer.close()
    try:
        await asyncio.wait_for(writer.wait_closed(), 1)
    except (Exception, asyncio.CancelledError):
        writer.transport.abort()

class TLS13Server:
    def __init__(self, context, peers, on_delta, *, local_actor_id, timeout=5, max_connections=16):
        _check_context(context)
        self.context, self.on_delta = context, on_delta
        self.local_actor_id = _uuid(local_actor_id)
        self.peers = {(p.fingerprint_sha256, p.actor_id, p.workspace_id): p for p in peers}
        if not 0 < timeout <= 30 or not 1 <= max_connections <= 128:
            raise TransportError('Invalid transport limits')
        self.timeout, self.max_connections = timeout, max_connections
        self._server = None
        self._tasks = set()
        self.rejected = 0

    async def start(self, host='127.0.0.1', port=0):
        if self._server is not None:
            raise TransportError('Already started')
        self._server = await asyncio.start_server(self._accept, host, port, ssl=self.context,
            ssl_handshake_timeout=self.timeout, ssl_shutdown_timeout=1, limit=MAX_FRAME + 4)
        return self.address

    @property
    def address(self):
        return self._server.sockets[0].getsockname()[:2] if self._server else None

    async def _accept(self, reader, writer):
        task = asyncio.current_task()
        if len(self._tasks) >= self.max_connections:
            self.rejected += 1
            await _close(writer)
            return
        self._tasks.add(task)
        try:
            deadline = asyncio.get_running_loop().time() + self.timeout
            async with asyncio.timeout_at(deadline):
                pin = _fingerprint(writer)
                if not any(key[0] == pin for key in self.peers):
                    raise TransportError('Unpaired certificate')
                message = await _read(reader)
                if set(message) != {'version', 'actor_id', 'workspace_id', 'wire'} or type(message['version']) is not int or message['version'] != 1:
                    raise TransportError('Invalid envelope')
                actor, workspace, wire = message['actor_id'], message['workspace_id'], message['wire']
                if (pin, actor, workspace) not in self.peers:
                    raise TransportError('Unauthorized actor or workspace')
                digest = _delta(wire, actor, workspace)
                result = self.on_delta(actor, workspace, wire)
                if inspect.isawaitable(result):
                    await result
                # SQLite/synchronous callbacks cannot be preempted by asyncio.
                # Never acknowledge an operation that overran its wall budget.
                if asyncio.get_running_loop().time() >= deadline:
                    raise TimeoutError('Commit exceeded request deadline')
                await asyncio.sleep(0)
                if asyncio.get_running_loop().time() >= deadline:
                    raise TimeoutError('Request deadline expired before ACK')
                await _write(writer, {'version': 1, 'status': 'committed', 'sha256': digest,
                    'actor_id': self.local_actor_id, 'workspace_id': workspace})
        except asyncio.CancelledError:
            raise
        except Exception:
            self.rejected += 1
        finally:
            await _close(writer)
            self._tasks.discard(task)

    async def close(self):
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

class TLS13Client:
    def __init__(self, context, local_actor_id, workspace_id, peer, *, timeout=5):
        _check_context(context)
        if not context.check_hostname:
            raise TransportError('Hostname verification required')
        if not 0 < timeout <= 30:
            raise TransportError('Invalid deadline')
        self.context, self.peer, self.timeout = context, peer, timeout
        self.actor, self.workspace = _uuid(local_actor_id), _uuid(workspace_id)
        if peer.workspace_id != self.workspace:
            raise TransportError('Peer workspace mismatch')

    async def send_delta(self, host, port, wire, *, server_hostname='localhost'):
        digest = _delta(wire, self.actor, self.workspace)
        writer = None
        try:
            async with asyncio.timeout(self.timeout):
                reader, writer = await asyncio.open_connection(host, port, ssl=self.context,
                    server_hostname=server_hostname, ssl_handshake_timeout=self.timeout,
                    ssl_shutdown_timeout=1, limit=MAX_FRAME + 4)
                if _fingerprint(writer) != self.peer.fingerprint_sha256:
                    raise TransportError('Unpaired server certificate')
                await _write(writer, {'version': 1, 'actor_id': self.actor, 'workspace_id': self.workspace, 'wire': wire})
                ack = await _read(reader)
                expected = {'version': 1, 'status': 'committed', 'sha256': digest,
                    'actor_id': self.peer.actor_id, 'workspace_id': self.workspace}
                if ack != expected or type(ack.get('version')) is not int:
                    raise TransportError('Invalid durable ACK')
                return ack
        finally:
            if writer:
                await _close(writer)


def pair_peer_qr(actor_id, workspace_id, certificate_der, qr_fingerprint, *, confirmed=False):
    """Explicit out-of-band QR/full-fingerprint pairing, not PIN authentication.

    The caller persists the returned trust record only after the user confirms
    both devices. A short comparison code alone never creates a trust record.
    The certificate must still pass chain/hostname verification on every session.
    """
    if confirmed is not True:
        raise TransportError('Explicit pairing confirmation required')
    identity = PeerIdentity(actor_id, workspace_id, qr_fingerprint)
    if certificate_fingerprint(certificate_der) != identity.fingerprint_sha256:
        raise TransportError('QR certificate fingerprint mismatch')
    return identity

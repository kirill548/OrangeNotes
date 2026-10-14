"""Opt-in discovery experiment. Announcements are untrusted, never sync authority.

No sockets are created until start(); no note content or persistent device identity
is advertised. DiscoverySession is the legacy caller-driven UDP experiment;
MdnsDiscovery owns a background worker. Both require explicit shutdown.
"""
from dataclasses import dataclass
import errno
import ipaddress
import json
import select
import socket
import time
import uuid

MAX_PACKET_BYTES = 512
MAX_SCAN_PACKETS = 64
MAX_AGE_SECONDS = 30


@dataclass(frozen=True)
class Candidate:
    session_id: str
    host: str
    tls_port: int
    discovered_at: float


def announcement(session_id: str, tls_port: int, *, now=None) -> bytes:
    """Only advertise a temporary session ID and a proposed pairing endpoint."""
    if str(uuid.UUID(session_id)) != session_id:
        raise ValueError('Canonical session UUID required')
    if type(tls_port) is not int or not 1 <= tls_port <= 65535:
        raise ValueError('Invalid pairing port')
    stamp = int(time.time() if now is None else now)
    return json.dumps({'protocol': 'orange-notes-discovery', 'version': 1,
                       'session': session_id, 'tls_port': tls_port,
                       'timestamp': stamp}, separators=(',', ':')).encode('ascii')


def parse_announcement(packet: bytes, host: str, *, now=None):
    """Reject oversized, stale, unexpected-field, non-local or malformed packets."""
    if not packet or len(packet) > MAX_PACKET_BYTES:
        return None
    current = time.time() if now is None else now
    try:
        address = ipaddress.ip_address(host)
        if address.version != 4 or address.is_multicast or address.is_unspecified:
            return None
        if not (address.is_private or address.is_loopback):
            return None
        def unique_fields(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError('Duplicate field')
                result[key] = value
            return result
        value = json.loads(packet.decode('ascii'), object_pairs_hook=unique_fields)
        if not isinstance(value, dict) or set(value) != {
                'protocol', 'version', 'session', 'tls_port', 'timestamp'}:
            return None
        if value['protocol'] != 'orange-notes-discovery' or type(value['version']) is not int or value['version'] != 1:
            return None
        stamp, port, session = value['timestamp'], value['tls_port'], value['session']
        if type(stamp) is not int or abs(current - stamp) > MAX_AGE_SECONDS:
            return None
        if type(port) is not int or not 1 <= port <= 65535:
            return None
        if not isinstance(session, str) or str(uuid.UUID(session)) != session:
            return None
        return Candidate(session, host, port, current)
    except (ValueError, TypeError, UnicodeError, OverflowError):
        return None


class DiscoverySession:
    """A bounded IPv4 UDP pairing window; explicit start/advertise/poll/close."""
    def __init__(self):
        self.session_id = str(uuid.uuid4())
        self._socket = None
        self._last_advertised = None

    def start(self, bind_host='127.0.0.1', port=0):
        if self._socket is not None:
            raise RuntimeError('Discovery already running')
        address = ipaddress.IPv4Address(bind_host)
        if not (address.is_loopback or address.is_private or address.is_unspecified):
            raise ValueError('Bind a local IPv4 interface')
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.bind((str(address), port))
            sock.setblocking(False)
        except BaseException:
            sock.close()
            raise
        self._socket = sock
        return sock.getsockname()

    def advertise(self, target, tls_port):
        if self._socket is None:
            raise RuntimeError('Start discovery explicitly first')
        host, port = target
        address = ipaddress.IPv4Address(host)
        if not (address.is_private or address.is_loopback or str(address) == '255.255.255.255') or address.is_multicast:
            raise ValueError('Discovery destination must be local IPv4')
        now = time.monotonic()
        if self._last_advertised is not None and now - self._last_advertised < 1:
            raise RuntimeError('Announcement rate limited to one per second')
        result = self._socket.sendto(announcement(self.session_id, tls_port), (str(address), port))
        self._last_advertised = now
        return result

    def poll(self, timeout=0):
        if self._socket is None:
            raise RuntimeError('Discovery not started')
        if not 0 <= timeout <= 1:
            raise ValueError('Poll timeout must be between zero and one second')
        found = {}
        if not select.select([self._socket], [], [], timeout)[0]:
            return []
        for _ in range(MAX_SCAN_PACKETS):
            try:
                packet, source = self._socket.recvfrom(MAX_PACKET_BYTES + 1)
            except BlockingIOError:
                break
            except OSError as exc:
                # Winsock discards oversized datagrams with WSAEMSGSIZE;
                # POSIX normally returns truncated bytes, rejected by length.
                if exc.errno == errno.EMSGSIZE or getattr(exc, 'winerror', None) == 10040:
                    continue
                raise
            candidate = parse_announcement(packet, source[0])
            if candidate and candidate.session_id != self.session_id:
                found[(candidate.session_id, candidate.host, candidate.tls_port)] = candidate
        return list(found.values())

    def close(self):
        if self._socket is not None:
            self._socket.close()
            self._socket = None
        self._last_advertised = None
        self.session_id = str(uuid.uuid4())

# The UDP DiscoverySession above remains a compatibility experiment. Production
# LAN discovery is opt-in mDNS below; advertisements are never authorization.
import importlib
import queue
import threading

SERVICE_TYPE = '_orangenotes._tcp.local.'


class DiscoveryError(RuntimeError):
    """Discovery cannot start or stop safely; suitable for a settings message."""


@dataclass(frozen=True)
class MdnsCandidate:
    session_id: str
    host: str
    tls_port: int
    discovered_at: float
    pairing_hint: str
    # Local registry presentation only; transport must authenticate again.
    paired: bool = False
    workspace_ids: frozenset = frozenset()


class MdnsDiscovery:
    """Explicit background Zeroconf service with bounded records and work queue.

    trusted_scopes(candidate) consults local trust state and returns granted
    workspace IDs, or None for unknown devices. Advertised pairing hints and
    endpoints are untrusted and cannot grant access. No workspace/device UUIDs
    are broadcast. A certificate must still be verified by the TLS transport.
    """
    def __init__(self, *, trusted_scopes=None, backend=None, interfaces=None):
        self._trusted_scopes = trusted_scopes or (lambda candidate: None)
        self._backend = backend
        self._interfaces = interfaces
        self._thread = None
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._events = queue.Queue(maxsize=64)
        self._lock = threading.Lock()
        self._records = {}
        self._error = None
        self.session_id = str(uuid.uuid4())

    @property
    def running(self):
        return self._thread is not None and self._thread.is_alive()

    @property
    def error(self):
        return self._error

    def start(self, tls_port, *, addresses, pairing_hint='pairable'):
        if self.running:
            raise DiscoveryError('Обнаружение устройств уже запущено')
        if type(tls_port) is not int or not 1 <= tls_port <= 65535:
            raise ValueError('Invalid TLS port')
        if pairing_hint not in ('pairable', 'paired'):
            raise ValueError('Invalid pairing hint')
        packed = []
        for host in addresses:
            address = ipaddress.ip_address(host)
            if address.is_multicast or address.is_unspecified or not (address.is_private or address.is_loopback or address.is_link_local):
                raise ValueError('Advertise only local interface addresses')
            packed.append(address.packed)
        if not packed or len(packed) > 8:
            raise ValueError('One to eight interface addresses required')
        self._stop.clear()
        self._ready.clear()
        self._error = None
        self._records.clear()
        self._events = queue.Queue(maxsize=64)
        self.session_id = str(uuid.uuid4())
        self._thread = threading.Thread(target=self._run,
            args=(tls_port, packed, pairing_hint), name='OrangeNotes-mDNS', daemon=True)
        self._thread.start()
        # Startup/import/network errors are delivered by error/ready; GUI never
        # waits for probing multicast interfaces. Call stop on window shutdown.

    def wait_ready(self, timeout=3):
        if not self._ready.wait(timeout):
            raise DiscoveryError('Обнаружение устройств ещё запускается')
        if self._error:
            raise DiscoveryError(self._error)

    def _enqueue(self, kind, service_type, name):
        if service_type != SERVICE_TYPE or not isinstance(name, str) or len(name) > 255:
            return
        try:
            self._events.put_nowait((kind, name))
        except queue.Full:
            pass

    def add_service(self, zc, service_type, name):
        self._enqueue('add', service_type, name)

    def update_service(self, zc, service_type, name):
        self._enqueue('add', service_type, name)

    def remove_service(self, zc, service_type, name):
        self._enqueue('remove', service_type, name)

    def _run(self, port, addresses, hint):
        zc = browser = info = None
        registered = False
        try:
            backend = self._backend or importlib.import_module('zeroconf')
            options = {} if self._interfaces is None else {'interfaces': self._interfaces}
            zc = backend.Zeroconf(**options)
            name = self.session_id + '.' + SERVICE_TYPE
            info = backend.ServiceInfo(SERVICE_TYPE, name, addresses=addresses,
                port=port, properties={'version': '1', 'session': self.session_id,
                                       'pairing': hint}, host_ttl=30, other_ttl=30)
            zc.register_service(info)
            registered = True
            browser = backend.ServiceBrowser(zc, SERVICE_TYPE, listener=self)
            self._ready.set()
            last_resolved = {}
            while not self._stop.is_set():
                try:
                    kind, name = self._events.get(timeout=.1)
                except queue.Empty:
                    continue
                if kind == 'remove':
                    with self._lock:
                        self._records.pop(name, None)
                    last_resolved.pop(name, None)
                    continue
                now = time.monotonic()
                last_resolved = {key: stamp for key, stamp in last_resolved.items() if now - stamp < 1}
                if name in last_resolved or len(last_resolved) >= 64:
                    continue
                last_resolved[name] = now
                remote = zc.get_service_info(SERVICE_TYPE, name, timeout=250)
                candidate = self._parse(remote)
                if candidate and candidate.session_id != self.session_id:
                    with self._lock:
                        self._expire(now)
                        if len(self._records) < 64 or name in self._records:
                            self._records[name] = candidate
        except Exception:
            # Avoid exposing network endpoints or dependency internals in UI.
            self._error = 'Не удалось запустить поиск устройств. Проверьте наличие Zeroconf и доступ к локальной сети.'
        finally:
            self._ready.set()
            try:
                if browser is not None:
                    browser.cancel()
                if registered:
                    zc.unregister_service(info)
            except Exception:
                pass
            finally:
                if zc is not None:
                    zc.close()

    def _parse(self, info):
        if info is None or type(info.port) is not int or not 1 <= info.port <= 65535:
            return None
        try:
            props = info.properties
            if set(props) != {b'version', b'session', b'pairing'}:
                return None
            if sum(len(k) + len(v) for k, v in props.items()) > MAX_PACKET_BYTES:
                return None
            session = props[b'session'].decode('ascii')
            hint = props[b'pairing'].decode('ascii')
            if props[b'version'] != b'1' or str(uuid.UUID(session)) != session or hint not in ('paired', 'pairable'):
                return None
            for host in info.parsed_addresses()[:8]:
                address = ipaddress.ip_address(host)
                if address.is_multicast or address.is_unspecified or not (address.is_private or address.is_loopback or address.is_link_local):
                    continue
                return MdnsCandidate(session, str(address), info.port, time.monotonic(), hint)
        except (ValueError, TypeError, UnicodeError, AttributeError):
            return None
        return None

    def _expire(self, now):
        self._records = {key: item for key, item in self._records.items()
                         if now - item.discovered_at <= MAX_AGE_SECONDS}

    def candidates(self, *, workspace_id=None, paired_only=False):
        with self._lock:
            self._expire(time.monotonic())
            records = list(self._records.values())
        result = []
        for candidate in records:
            try:
                scopes = self._trusted_scopes(candidate)
                paired = scopes is not None
                scopes = frozenset(scopes or ())
            except Exception:
                continue
            if paired_only and not paired:
                continue
            if workspace_id is not None and workspace_id not in scopes:
                continue
            result.append(MdnsCandidate(candidate.session_id, candidate.host,
                candidate.tls_port, candidate.discovered_at, candidate.pairing_hint,
                paired, scopes))
        return result

    def stop(self, timeout=5):
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
            if thread.is_alive():
                raise DiscoveryError('Поиск устройств ещё завершает работу')
        self._thread = None
        with self._lock:
            self._records.clear()

    close = stop

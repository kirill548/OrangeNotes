"""Opt-in discovery experiment. Announcements are untrusted, never sync authority.

No sockets are created until start(); no note content or persistent device identity
is advertised. Caller owns the loop/thread and must stop after the pairing window.
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

# Local P2P: initial discovery experiment

Status: an opt-in IPv4 UDP discovery prototype, **not a sync implementation**.
Nothing is connected to application startup or the notes database. No new
dependencies, servers, downloads, certificate services or paid infrastructure.

`app/services/p2p_discovery.py` supports explicit `start`, `advertise`, `poll`,
and `close`. The default bind is loopback. LAN experiments must explicitly bind
a local interface or `0.0.0.0`, choose a UDP port and supply a subnet broadcast
destination. There is no built-in permanent listening port. Each session has a
random UUID which rotates when closed; announcements contain only protocol
version, session UUID, timestamp and proposed TLS pairing port.

Packets are capped at 512 bytes, unknown/duplicate fields and malformed values
are rejected, receive processing is capped at 64 datagrams per poll, and sends
are limited to one per second per session. Discovery candidates are untrusted:
private source addresses, UUIDs and timestamps do not authenticate a device.
There is no automatic reply, connection, note transfer or synchronization.
Call poll outside the GUI thread; explicit waits are bounded at one second.
The caller must limit the pairing window and close its session on cancellation.

## Pairing and synchronization design (not implemented)

1. Both users explicitly enable a short discovery window. The UI explains that
   devices on that LAN may observe the temporary identifier.
2. A user selects a candidate. Its UDP metadata is only a locator.
3. Establish TLS with mutual device authentication. First pairing requires
   out-of-band verification (QR exchange of public-key fingerprints or a
   cryptographically derived short authentication string confirmed on both
   screens). A simple unverified six-digit code is insufficient. Bind the
   displayed identity to the TLS handshake, then pin approved public keys.
4. Store device trust in OS-protected storage; provide revocation. Never use
   `CERT_NONE` for note transfer. Production TLS setup needs security review and
   tested certificate/key generation on both mobile and desktop targets.
5. Only after explicit approval of device and workspace exchange revision-based
   operations over the authenticated channel. Include tombstones, conflicts,
   operation IDs, transactional application, bounded payloads and resumable
   acknowledgements. Do not copy a live SQLite file over the network.

Python's [SSL documentation](https://docs.python.org/3.12/library/ssl.html)
documents certificate verification and TLS contexts. The TLS specification
describes authentication and out-of-band trust as part of the security model:
[TLS 1.3](https://www.rfc-editor.org/info/rfc8446/).

Direct TLS is the simplest first LAN transport to evaluate. WebRTC adds ICE,
signalling and deployment complexity; it is not implemented here and should
not be described as completed E2EE. LAN broadcast can fail under AP isolation,
VPNs, firewalls, multicast restrictions, Flatpak sandboxing and mobile OS
permissions. Future QR/manual-address pairing should cover discovery failure.
IPv6 and real iOS/Android lifecycle/permission checks remain open work.

## Verification

`python -m unittest discover -s tests -p test_p2p_discovery.py -v`

Tests use actual ephemeral loopback UDP sockets, including Windows oversized
datagram handling. They check malformed/stale packets, no network on
construction, no note fields, destination bounds, own-announcement filtering,
send throttling and socket shutdown. They do not establish TLS or validate Wi-Fi
discovery on physical smartphones. No performance or mobile compatibility claim
is implied by these tests.

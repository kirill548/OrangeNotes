import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:crypto/crypto.dart';

import 'crdt_engine.dart';

const maxTransportFrame = 2 * 262144 + 4096;

class TransportError implements Exception {
  TransportError(this.message);
  final String message;
  @override
  String toString() => 'TransportError: $message';
}

class PeerIdentity {
  PeerIdentity(this.actorId, this.workspaceId, this.fingerprintSha256) {
    validUuid(actorId);
    validUuid(workspaceId);
    if (!RegExp(r'^[0-9a-f]{64}$').hasMatch(fingerprintSha256)) {
      throw TransportError('Full lowercase certificate fingerprint required');
    }
  }
  final String actorId, workspaceId, fingerprintSha256;
}

/// PEM on desktop/Android; iOS callers supply PKCS12 identity and DER CA.
SecurityContext createTlsContext({
  List<int>? caBytes,
  List<int>? caPem,
  List<int>? certPem,
  List<int>? keyPem,
  List<int>? identityPkcs12,
  String? password,
}) {
  final context = SecurityContext()
    ..minimumTlsProtocolVersion = TlsProtocolVersion.tls1_3;
  final ca = caBytes ?? caPem;
  if (ca == null) throw TransportError('CA certificate required');
  var trusted = ca;
  if (Platform.isIOS &&
      utf8.decode(ca, allowMalformed: true).contains('BEGIN CERTIFICATE')) {
    trusted = base64.decode(
      utf8.decode(ca).replaceAll(RegExp(r'-----[^-]+-----|\s'), ''),
    );
  }
  context.setTrustedCertificatesBytes(trusted);
  if (identityPkcs12 != null) {
    context.usePrivateKeyBytes(identityPkcs12, password: password);
    if (!Platform.isIOS) {
      context.useCertificateChainBytes(identityPkcs12, password: password);
    }
  } else {
    if (Platform.isIOS || certPem == null || keyPem == null) {
      throw TransportError('A local identity is required (PKCS12 on iOS)');
    }
    context.useCertificateChainBytes(certPem);
    context.usePrivateKeyBytes(keyPem, password: password);
  }
  return context;
}

void _context(SecurityContext c, Duration timeout) {
  if (c.minimumTlsProtocolVersion != TlsProtocolVersion.tls1_3 ||
      timeout <= Duration.zero ||
      timeout > const Duration(seconds: 30)) {
    throw TransportError('Verified TLS 1.3 and deadline <=30 seconds required');
  }
}

String _pin(SecureSocket socket) {
  final cert = socket.peerCertificate;
  if (cert == null) throw TransportError('Mutual certificate required');
  return sha256.convert(cert.der).toString();
}

String _delta(String wire, String actor, String workspace) {
  if (utf8.encode(wire).length > 262144) {
    throw TransportError('Delta byte limit');
  }
  final value = strictJson(wire);
  if (value is! Map ||
      value['actor_id'] != actor ||
      value['workspace_id'] != workspace) {
    throw TransportError('Delta identity mismatch');
  }
  return sha256.convert(utf8.encode(wire)).toString();
}

class _FrameReader {
  _FrameReader(Stream<Uint8List> stream) : _iterator = StreamIterator(stream);
  final StreamIterator<Uint8List> _iterator;
  final List<int> _buffer = [];
  Future<Map<String, dynamic>> read() async {
    int? size;
    while (true) {
      if (size == null && _buffer.length >= 4) {
        size = ByteData.sublistView(
          Uint8List.fromList(_buffer.take(4).toList()),
        ).getUint32(0);
        if (size < 1 || size > maxTransportFrame) {
          throw TransportError('Frame byte limit');
        }
      }
      if (size != null && _buffer.length >= size + 4) {
        final decoded = strictJson(utf8.decode(_buffer.sublist(4, size + 4)));
        if (_buffer.length != size + 4 || decoded is! Map<String, dynamic>) {
          throw TransportError('One object frame required');
        }
        return decoded;
      }
      if (!await _iterator.moveNext()) {
        throw TransportError('Connection closed before ACK');
      }
      _buffer.addAll(_iterator.current);
      if (_buffer.length > maxTransportFrame + 4) {
        throw TransportError('Frame byte limit');
      }
    }
  }

  Future<void> close() => _iterator.cancel();
}

Future<void> _write(SecureSocket socket, Map<String, dynamic> value) async {
  final data = utf8.encode(jsonEncode(value));
  if (data.length > maxTransportFrame) throw TransportError('Frame byte limit');
  final prefix = ByteData(4)..setUint32(0, data.length);
  socket.add(prefix.buffer.asUint8List());
  socket.add(data);
  await socket.flush();
}

class Tls13Client {
  Tls13Client(
    this.context,
    this.localActorId,
    this.workspaceId,
    this.peer, {
    this.timeout = const Duration(seconds: 5),
  }) {
    _context(context, timeout);
    validUuid(localActorId);
    validUuid(workspaceId);
    if (peer.workspaceId != workspaceId) {
      throw TransportError('Peer workspace mismatch');
    }
  }
  final SecurityContext context;
  final String localActorId, workspaceId;
  final PeerIdentity peer;
  final Duration timeout;
  Future<Map<String, dynamic>> sendDelta(
    String host,
    int port,
    String wire, {
    String serverHostname = 'localhost',
  }) async {
    final digest = _delta(wire, localActorId, workspaceId);
    Socket? raw;
    SecureSocket? socket;
    _FrameReader? reader;
    final watch = Stopwatch()..start();
    Duration remaining() {
      final value = timeout - watch.elapsed;
      if (value <= Duration.zero) throw TimeoutException('P2P deadline');
      return value;
    }

    try {
      raw = await Socket.connect(host, port, timeout: remaining());
      // secure(host:) performs chain and hostname verification. Never bypass it.
      final handshake = SecureSocket.secure(
        raw,
        host: serverHostname,
        context: context,
      );
      socket = await handshake.timeout(
        remaining(),
        onTimeout: () {
          raw?.destroy();
          throw TimeoutException('TLS handshake deadline');
        },
      );
      if (_pin(socket) != peer.fingerprintSha256) {
        throw TransportError('Unpaired server certificate');
      }
      reader = _FrameReader(socket);
      await _write(socket, {
        'version': 1,
        'actor_id': localActorId,
        'workspace_id': workspaceId,
        'wire': wire,
      }).timeout(remaining());
      final ack = await reader.read().timeout(remaining());
      final expected = {
        'version': 1,
        'status': 'committed',
        'sha256': digest,
        'actor_id': peer.actorId,
        'workspace_id': workspaceId,
      };
      if (canonicalJson(ack) != canonicalJson(expected)) {
        throw TransportError('Invalid durable ACK');
      }
      return ack;
    } finally {
      socket?.destroy();
      raw?.destroy();
      await reader?.close();
    }
  }
}

typedef DeltaCommit = Future<void> Function(
  String actor,
  String workspace,
  String wire,
);

class Tls13Server {
  Tls13Server(
    this.context,
    List<PeerIdentity> peers,
    this.onDelta, {
    required this.localActorId,
    this.timeout = const Duration(seconds: 5),
    this.maxConnections = 16,
  }) : peers = List.of(peers) {
    _context(context, timeout);
    validUuid(localActorId);
    if (maxConnections < 1 || maxConnections > 128) {
      throw TransportError('Connection limit');
    }
  }
  final SecurityContext context;
  final List<PeerIdentity> peers;
  final DeltaCommit onDelta;
  final String localActorId;
  final Duration timeout;
  final int maxConnections;
  ServerSocket? _server;
  StreamSubscription<Socket>? _subscription;
  final Set<Socket> _sockets = {};
  final Set<Future<void>> _tasks = {};
  int rejected = 0;
  int get port => _server?.port ?? 0;
  Future<int> start({String host = '127.0.0.1', int port = 0}) async {
    if (_server != null) throw TransportError('Already started');
    _server = await ServerSocket.bind(host, port);
    _subscription = _server!.listen(
      (raw) {
        if (_sockets.length >= maxConnections) {
          rejected++;
          raw.destroy();
          return;
        }
        _sockets.add(raw);
        late Future<void> task;
        task = _accept(raw).whenComplete(() => _tasks.remove(task));
        _tasks.add(task);
      },
      onError: (Object error) {
        rejected++;
      },
    );
    return this.port;
  }

  Future<void> _accept(Socket raw) async {
    SecureSocket? socket;
    _FrameReader? reader;
    final watch = Stopwatch()..start();
    Duration remaining() {
      final d = timeout - watch.elapsed;
      if (d <= Duration.zero) throw TimeoutException('P2P deadline');
      return d;
    }

    try {
      socket =
          await SecureSocket.secureServer(
            raw,
            context,
            requireClientCertificate: true,
          ).timeout(
            remaining(),
            onTimeout: () {
              raw.destroy();
              throw TimeoutException('TLS handshake deadline');
            },
          );
      final pin = _pin(socket);
      if (!peers.any((p) => p.fingerprintSha256 == pin)) {
        throw TransportError('Unpaired certificate');
      }
      reader = _FrameReader(socket);
      final message = await reader.read().timeout(remaining());
      if (message.length != 4 ||
          message['version'] is! int ||
          message['version'] != 1 ||
          message['wire'] is! String) {
        throw TransportError('Invalid envelope');
      }
      final actor = validUuid(message['actor_id']),
          workspace = validUuid(message['workspace_id']),
          wire = message['wire'] as String;
      bool authorized() => peers.any(
        (p) =>
            p.actorId == actor &&
            p.workspaceId == workspace &&
            p.fingerprintSha256 == pin,
      );
      if (!authorized()) throw TransportError('Unauthorized actor/workspace');
      final digest = _delta(wire, actor, workspace);
      await onDelta(actor, workspace, wire).timeout(remaining());
      remaining();
      if (!authorized()) throw TransportError('Peer revoked before ACK');
      await _write(socket, {
        'version': 1,
        'status': 'committed',
        'sha256': digest,
        'actor_id': localActorId,
        'workspace_id': workspace,
      }).timeout(remaining());
    } catch (_) {
      rejected++;
    } finally {
      socket?.destroy();
      raw.destroy();
      _sockets.remove(raw);
      await reader?.close();
    }
  }

  Future<void> close() async {
    await _server?.close();
    _server = null;
    await _subscription?.cancel();
    _subscription = null;
    for (final socket in _sockets.toList()) {
      socket.destroy();
    }
    await Future.wait(_tasks.toList());
  }
}

import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:file_picker/file_picker.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import '../data/mobile_store.dart';
import '../presentation/pairing_screen.dart';
import 'crdt_engine.dart';
import 'ai_gateway.dart';
import 'p2p_transport.dart';

/// Explicit foreground synchronization. No background networking at startup.
class MobileSync extends PairingController {
  MobileSync(this.store, {FlutterSecureStorage? storage})
    : storage = storage ?? const FlutterSecureStorage();
  final MobileStore store;
  final FlutterSecureStorage storage;
  final Map<String, Map<String, dynamic>> _peers = {};
  Map<String, dynamic>? _identity;
  bool _busy = false, _disposed = false;
  String _status = 'Сохранено на устройстве. Сеть не подключена.';
  Tls13Server? _server;
  Future<void>? _initialization, _shutdown;
  Future<void> _tail = Future<void>.value();
  final Set<Future<void>> _receiving = {};
  late final AiGateway aiGateway = _SessionAiGateway(this);

  AiGateway _gatewayForRequest() {
    _ensureAlive();
    final local = LocalAiGateway(store);
    final candidates = _peers.values.where((p) => p['ai_port'] is int);
    if (_identity == null || candidates.isEmpty) return local;
    final p = candidates.first;
    try {
      if (_identity!['actor_id'] != store.actorId ||
          p['workspace_id'] != store.workspaceId) {
        return local;
      }
      return FallbackAiGateway(
        DesktopAiGateway(
          context: _context(_identity!),
          localActorId: store.actorId,
          workspaceId: store.workspaceId,
          peer: PeerIdentity(
            p['actor_id'] as String,
            store.workspaceId,
            p['fingerprint_sha256'] as String,
          ),
          host: p['host'] as String,
          port: p['ai_port'] as int,
          serverHostname: p['server_hostname'] as String,
        ),
        local,
      );
    } catch (_) {
      // Invalid or unavailable device credentials never disable offline notes.
      return local;
    }
  }

  void _ensureAlive() {
    if (_disposed) throw StateError('Подключение закрыто.');
  }

  Future<void> _run(Future<void> Function() action) {
    if (_disposed) {
      return Future<void>.error(StateError('Подключение закрыто.'));
    }
    final result = _tail.then((_) async {
      await initialize();
      _ensureAlive();
      await action();
    });
    _tail = result.catchError((Object _) {});
    return result;
  }

  @override
  String get status =>
      '$_status\nУстройство: ${store.actorId}\nПространство: ${store.workspaceId}';
  @override
  List<TrustedDevice> get devices => _peers.values
      .map(
        (p) => TrustedDevice(
          p['actor_id'] as String,
          p['name'] as String,
          p['fingerprint_sha256'] as String,
        ),
      )
      .toList();
  void _changed() {
    if (!_disposed) notifyListeners();
  }

  Future<void> initialize() => _initialization ??= _load();
  Future<void> _load() async {
    try {
      final identity = await storage.read(key: 'p2p_identity_${store.actorId}');
      if (_disposed) return;
      if (identity != null) {
        _identity = jsonDecode(identity) as Map<String, dynamic>;
      }
      final peers = await storage.read(key: 'p2p_peers_${store.actorId}');
      if (_disposed) return;
      if (peers != null) {
        for (final item in jsonDecode(peers) as List) {
          final p = _validatePeer(Map<String, dynamic>.from(item as Map));
          if (p['workspace_id'] == store.workspaceId) {
            _peers[p['actor_id'] as String] = p;
            if (_disposed) return;
            await store.grantPeer(p['actor_id'] as String);
          }
        }
      }
      _status = _identity == null
          ? 'Для обмена подключите сертификат устройства.'
          : 'Готово к обмену. Сеть включается по запросу.';
    } catch (_) {
      _status = 'Не удалось прочитать настройки подключения. Заметки доступны.';
    }
    _changed();
  }

  Map<String, dynamic> _validatePeer(Map<String, dynamic> p) {
    validUuid(p['actor_id']);
    validUuid(p['workspace_id']);
    if (p['actor_id'] == store.actorId ||
        p['host'] is! String ||
        p['server_hostname'] is! String ||
        p['port'] is! int ||
        (p['port'] as int) < 1 ||
        (p['port'] as int) > 65535 ||
        p['fingerprint_sha256'] is! String ||
        !RegExp(r'^[a-f0-9]{64}$')
            .hasMatch(p['fingerprint_sha256'] as String)) {
      throw const FormatException('Неверные данные устройства.');
    }
    final address = InternetAddress.tryParse(p['host'] as String);
    if (address == null ||
        address.isMulticast ||
        address.isLoopback ||
        address.isLinkLocal) {
      throw const FormatException('Укажите локальный IP-адрес устройства.');
    }
    final bytes = address.rawAddress;
    final local = address.type == InternetAddressType.IPv4
        ? bytes[0] == 10 ||
              (bytes[0] == 192 && bytes[1] == 168) ||
              (bytes[0] == 172 && bytes[1] >= 16 && bytes[1] <= 31)
        : (bytes[0] & 0xfe) == 0xfc;
    if (!local) {
      throw const FormatException(
        'Устройство должно находиться в локальной сети.',
      );
    }
    if (p.containsKey('ai_port') &&
        (p['ai_port'] is! int ||
            p['ai_port'] < 1 ||
            p['ai_port'] > 65535 ||
            p['ai_port'] == p['port'])) {
      throw const FormatException('Неверный отдельный порт помощника.');
    }
    return {
      if (p.containsKey('ai_port')) 'ai_port': p['ai_port'],
      for (final key in [
        'actor_id',
        'workspace_id',
        'host',
        'port',
        'server_hostname',
        'fingerprint_sha256',
      ])
        key: p[key],
      'name': p['name'] is String
          ? (p['name'] as String).substring(
              0,
              (p['name'] as String).length.clamp(0, 80),
            )
          : 'Компьютер',
    };
  }

  Future<void> _savePeers() => storage.write(
    key: 'p2p_peers_${store.actorId}',
    value: jsonEncode(_peers.values.toList()),
  );
  @override
  Future<void> importIdentity() => _run(_importIdentity);
  Future<void> _importIdentity() async {
    final result = await FilePicker.platform.pickFiles(
      type: FileType.custom,
      allowedExtensions: ['json'],
      withData: true,
    );
    _ensureAlive();
    if (result == null) return;
    final file = result.files.single;
    if (file.size > 128 * 1024) {
      throw const FormatException('Файл сертификата слишком большой.');
    }
    final bytes = file.bytes ?? await File(file.path!).readAsBytes();
    final value = jsonDecode(utf8.decode(bytes));
    if (value is! Map<String, dynamic> ||
        value['actor_id'] != store.actorId ||
        value['ca_pem'] is! String) {
      throw const FormatException(
        'Сертификат должен принадлежать этому устройству.',
      );
    }
    _context(value); // Validate before replacing the active identity.
    await storage.write(
      key: 'p2p_identity_${store.actorId}',
      value: jsonEncode(value),
    );
    aiGateway.cancel();
    _identity = value;
    await _server?.close();
    _server = null;
    _status = 'Сертификат подключён. Можно добавить устройство по QR-коду.';
    _changed();
  }

  SecurityContext _context(Map<String, dynamic> v) => createTlsContext(
    certPem: utf8.encode(v['certificate_pem'] as String? ?? ''),
    keyPem: utf8.encode(v['private_key_pem'] as String? ?? ''),
    caBytes: utf8.encode(v['ca_pem'] as String),
    identityPkcs12: v['pkcs12_base64'] == null
        ? null
        : Uint8List.fromList(base64Decode(v['pkcs12_base64'] as String)),
    password: v['password'] as String?,
  );
  @override
  Future<void> pairQr(String raw, {required bool confirmed}) =>
      _run(() => _pairQr(raw, confirmed: confirmed));
  Future<void> _pairQr(String raw, {required bool confirmed}) async {
    if (!confirmed || raw.length > 16384) {
      throw const FormatException('Подтвердите полный отпечаток.');
    }
    if (_busy) throw StateError('Дождитесь завершения обмена.');
    final p = _validatePeer(Map<String, dynamic>.from(jsonDecode(raw) as Map));
    aiGateway.cancel();
    if (p['workspace_id'] != store.workspaceId) {
      await store.joinEmptyWorkspace(p['workspace_id'] as String);
    }
    final actor = p['actor_id'] as String;
    final previous = _peers[actor];
    await store.grantPeer(actor);
    _peers[actor] = p;
    try {
      await _savePeers();
    } catch (_) {
      if (previous == null) {
        _peers.remove(actor);
        await store.revokePeer(actor);
      } else {
        _peers[actor] = previous;
      }
      rethrow;
    }
    await _server?.close();
    _server = null;
    _status = 'Устройство подтверждено. Нажмите его, чтобы начать обмен.';
    _changed();
  }

  @override
  Future<void> revoke(String actorId) => _run(() => _revoke(actorId));
  Future<void> _revoke(String actorId) async {
    if (_busy) throw StateError('Дождитесь завершения обмена.');
    aiGateway.cancel();
    final previous = _peers.remove(actorId);
    try {
      await _savePeers();
    } catch (_) {
      if (previous != null) _peers[actorId] = previous;
      rethrow;
    }
    aiGateway.cancel();
    await store.revokePeer(actorId);
    await _server?.close();
    _server = null;
    _status = 'Устройство отключено.';
    _changed();
  }

  @override
  Future<void> syncNow(String actorId) => _run(() => _syncNow(actorId));
  Future<void> _syncNow(String actorId) async {
    if (_busy) throw StateError('Обмен уже выполняется.');
    final p = _peers[actorId];
    if (p == null || _identity == null) {
      throw StateError('Подключите сертификат и доверенное устройство.');
    }
    _busy = true;
    _status = 'Передаю изменения…';
    _changed();
    try {
      final context = _context(_identity!);
      _server ??= Tls13Server(
        context,
        _peers.values
            .map(
              (p) => PeerIdentity(
                p['actor_id'] as String,
                p['workspace_id'] as String,
                p['fingerprint_sha256'] as String,
              ),
            )
            .toList(),
        (actor, workspace, wire) async {
          if (workspace != store.workspaceId || !_peers.containsKey(actor)) {
            throw StateError('Нет доступа к пространству.');
          }
          _ensureAlive();
          final transaction = store.receiveDelta(wire, actor).then<void>((_) {
            _changed();
          });
          _receiving.add(transaction);
          try {
            await transaction;
          } finally {
            _receiving.remove(transaction);
          }
        },
        localActorId: store.actorId,
      );
      final port = _server!.port == 0
          ? await _server!.start(host: '0.0.0.0')
          : _server!.port;
      final client = Tls13Client(
        context,
        store.actorId,
        store.workspaceId,
        PeerIdentity(
          actorId,
          store.workspaceId,
          p['fingerprint_sha256'] as String,
        ),
      );
      final deadline = DateTime.now().add(const Duration(seconds: 30));
      final pending = await store.pendingOutbox(actorId);
      for (final wire in pending.take(32)) {
        final remaining = deadline.difference(DateTime.now());
        if (remaining.isNegative) throw StateError('Время ожидания истекло.');
        final ack = await client
            .sendDelta(
              p['host'] as String,
              p['port'] as int,
              wire,
              serverHostname: p['server_hostname'] as String,
            )
            .timeout(remaining);
        _ensureAlive();
        if (!_peers.containsKey(actorId)) {
          throw StateError('Устройство отключено.');
        }
        await store.acknowledge(actorId, ack);
      }
      final left = await store.pendingOutbox(actorId);
      _status =
          '${left.isEmpty ? 'Исходящие изменения подтверждены' : 'Часть изменений передана; повторите обмен'}. Приём доступен на порту $port, пока открыт экран приложения.';
    } catch (_) {
      _status = 'Обмен не завершён. Изменения сохранены на устройстве.';
      rethrow;
    } finally {
      if (_disposed) await _server?.close();
      _busy = false;
      _changed();
    }
  }

  Future<void> shutdown() => _shutdown ??= _stop();
  Future<void> _stop() async {
    _disposed = true;
    aiGateway.cancel();
    await (aiGateway as _SessionAiGateway).idle;
    await _initialization;
    await _server?.close();
    await _tail;
    await _server?.close();
    await Future.wait(_receiving.toList());
  }

  @override
  void dispose() {
    if (!_disposed) {
      shutdown();
    }
    super.dispose();
  }
}

/// Resolves current trust for each explicit request; no network at construction.
class _SessionAiGateway implements AiGateway {
  _SessionAiGateway(this.owner);
  final MobileSync owner;
  AiGateway? _active;
  Completer<void>? _request;
  Future<void> get idle => _request?.future ?? Future<void>.value();
  int _generation = 0;
  @override
  void cancel() {
    _generation++;
    _active?.cancel();
  }

  @override
  Stream<AiEvent> ask(
    String prompt, {
    List<AiContext> contexts = const [],
  }) async* {
    if (_request != null) throw StateError('Дождитесь ответа помощника.');
    final request = Completer<void>();
    _request = request;
    final generation = ++_generation;
    try {
      await owner.initialize();
      owner._ensureAlive();
      if (generation != _generation) return;
      // Context must still belong to the current active workspace.
      final notes = await owner.store.listNotes();
      for (final context in contexts) {
        if (!notes.any(
          (n) =>
              n.uuid == context.noteId &&
              n.title == context.title &&
              n.body == context.body,
        )) {
          yield const AiEvent(
            AiEventKind.error,
            text: 'Заметка изменилась. Прикрепите её заново.',
          );
          return;
        }
      }
      if (generation != _generation) return;
      final gateway = owner._gatewayForRequest();
      _active = gateway;
      try {
        yield* gateway.ask(prompt, contexts: contexts);
      } finally {
        gateway.cancel();
        if (identical(_active, gateway)) _active = null;
      }
    } finally {
      if (identical(_request, request)) _request = null;
      request.complete();
    }
  }
}

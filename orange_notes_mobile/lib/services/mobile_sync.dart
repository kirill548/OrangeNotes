import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:file_picker/file_picker.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import '../data/mobile_store.dart';
import '../presentation/pairing_screen.dart';
import 'crdt_engine.dart';
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

  Future<void> initialize() async {
    try {
      final identity = await storage.read(key: 'p2p_identity_${store.actorId}');
      if (identity != null) {
        _identity = jsonDecode(identity) as Map<String, dynamic>;
      }
      final peers = await storage.read(key: 'p2p_peers_${store.actorId}');
      if (peers != null) {
        for (final item in jsonDecode(peers) as List) {
          final p = _validatePeer(Map<String, dynamic>.from(item as Map));
          if (p['workspace_id'] == store.workspaceId) {
            _peers[p['actor_id'] as String] = p;
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
    return {
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
  Future<void> importIdentity() async {
    final result = await FilePicker.platform.pickFiles(
      type: FileType.custom,
      allowedExtensions: ['json'],
      withData: true,
    );
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
  Future<void> pairQr(String raw, {required bool confirmed}) async {
    if (!confirmed || raw.length > 16384) {
      throw const FormatException('Подтвердите полный отпечаток.');
    }
    if (_busy) throw StateError('Дождитесь завершения обмена.');
    final p = _validatePeer(Map<String, dynamic>.from(jsonDecode(raw) as Map));
    if (p['workspace_id'] != store.workspaceId) {
      await store.joinEmptyWorkspace(p['workspace_id'] as String);
    }
    final actor = p['actor_id'] as String;
    _peers[actor] = p;
    try {
      await _savePeers();
      await store.grantPeer(actor);
    } catch (_) {
      _peers.remove(actor);
      rethrow;
    }
    await _server?.close();
    _server = null;
    _status = 'Устройство подтверждено. Нажмите его, чтобы начать обмен.';
    _changed();
  }

  @override
  Future<void> revoke(String actorId) async {
    if (_busy) throw StateError('Дождитесь завершения обмена.');
    final previous = _peers.remove(actorId);
    try {
      await _savePeers();
    } catch (_) {
      if (previous != null) _peers[actorId] = previous;
      rethrow;
    }
    await store.revokePeer(actorId);
    await _server?.close();
    _server = null;
    _status = 'Устройство отключено.';
    _changed();
  }

  @override
  Future<void> syncNow(String actorId) async {
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
          await store.receiveDelta(wire, actor);
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
      _busy = false;
      _changed();
    }
  }

  @override
  void dispose() {
    _disposed = true;
    _server?.close();
    super.dispose();
  }
}

import 'dart:convert';

import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';
import 'package:orange_notes_mobile/data/mobile_store.dart';
import 'package:orange_notes_mobile/services/mobile_sync.dart';
import 'package:orange_notes_mobile/services/ai_gateway.dart';

class FailingStorage extends FlutterSecureStorage {
  bool failWrites = false;
  @override
  Future<void> write({
    required String key,
    required String? value,
    IOSOptions? iOptions,
    AndroidOptions? aOptions,
    LinuxOptions? lOptions,
    WebOptions? webOptions,
    MacOsOptions? mOptions,
    WindowsOptions? wOptions,
  }) {
    if (failWrites) {
      return Future<void>.error(StateError('Keychain unavailable'));
    }
    return super.write(
      key: key,
      value: value,
      iOptions: iOptions,
      aOptions: aOptions,
      lOptions: lOptions,
      webOptions: webOptions,
      mOptions: mOptions,
      wOptions: wOptions,
    );
  }
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  sqfliteFfiInit();
  const peer = '20000000-0000-4000-8000-000000000002';
  const space = '10000000-0000-4000-8000-000000000001';
  late MobileStore store;
  late MobileSync sync;
  String qr({String host = '192.168.1.20'}) => jsonEncode({
    'actor_id': peer,
    'workspace_id': space,
    'host': host,
    'port': 7443,
    'server_hostname': 'desktop.local',
    'fingerprint_sha256': 'a' * 64,
    'name': 'Компьютер',
  });
  setUp(() async {
    FlutterSecureStorage.setMockInitialValues({});
    store = await MobileStore.open(
      databaseFactory: databaseFactoryFfi,
      path: inMemoryDatabasePath,
    );
    sync = MobileSync(store);
    await sync.initialize();
  });
  tearDown(() async {
    sync.dispose();
    await store.close();
  });
  test('offline startup does not imply successful synchronization', () {
    expect(sync.status, contains('сертификат'));
    expect(sync.devices, isEmpty);
  });
  test('QR requires explicit confirmation and local address', () async {
    await expectLater(
      sync.pairQr(qr(), confirmed: false),
      throwsFormatException,
    );
    await expectLater(
      sync.pairQr(qr(host: '8.8.8.8'), confirmed: true),
      throwsFormatException,
    );
    expect(sync.devices, isEmpty);
  });
  test(
    'confirmed QR joins empty workspace, persists grant, revokes durably',
    () async {
      await sync.pairQr(qr(), confirmed: true);
      expect(store.workspaceId, space);
      expect(sync.devices.single.actorId, peer);
      expect(await store.pendingOutbox(peer), isEmpty);
      final reopened = MobileSync(store);
      await reopened.initialize();
      expect(reopened.devices.single.actorId, peer);
      await reopened.revoke(peer);
      reopened.dispose();
      final again = MobileSync(store);
      await again.initialize();
      expect(again.devices, isEmpty);
      await expectLater(store.pendingOutbox(peer), throwsA(isA<Exception>()));
      again.dispose();
    },
  );
  test('pairing never moves existing notes into another workspace', () async {
    final note = await store.saveNote(title: 'Личное', body: 'Не перемещать');
    final original = store.workspaceId;
    await expectLater(
      sync.pairQr(qr(), confirmed: true),
      throwsA(isA<Exception>()),
    );
    expect(store.workspaceId, original);
    expect((await store.getNote(note.id))!.body, 'Не перемещать');
  });
  test(
    'without local identity sync fails without claiming completion',
    () async {
      await sync.pairQr(qr(), confirmed: true);
      await expectLater(sync.syncNow(peer), throwsStateError);
      expect(sync.status, isNot(contains('Исходящие изменения подтверждены')));
    },
  );
  test('shutdown blocks queued changes before SQLite is closed', () async {
    await sync.shutdown();
    await expectLater(sync.pairQr(qr(), confirmed: true), throwsStateError);
    expect(sync.devices, isEmpty);
  });
  test('failed replacement preserves previous durable trust', () async {
    final vault = FailingStorage();
    final controller = MobileSync(store, storage: vault);
    await controller.initialize();
    await controller.pairQr(qr(), confirmed: true);
    vault.failWrites = true;
    await expectLater(
      controller.pairQr(qr(host: '192.168.1.30'), confirmed: true),
      throwsStateError,
    );
    expect(controller.devices.single.actorId, peer);
    expect(await store.pendingOutbox(peer), isEmpty);
    vault.failWrites = false;
    final saved = jsonDecode(
      (await vault.read(key: 'p2p_peers_${store.actorId}'))!,
    );
    expect(saved.single['host'], '192.168.1.20');
    await controller.shutdown();
    controller.dispose();
  });
  test('failed new trust write leaves no database grant', () async {
    final vault = FailingStorage()..failWrites = true;
    final controller = MobileSync(store, storage: vault);
    await controller.initialize();
    await expectLater(
      controller.pairQr(qr(), confirmed: true),
      throwsStateError,
    );
    expect(controller.devices, isEmpty);
    await expectLater(store.pendingOutbox(peer), throwsA(isA<Exception>()));
    await controller.shutdown();
    controller.dispose();
  });
  test(
    'AI without identity uses local search without contacting a peer',
    () async {
      await store.saveNote(title: 'Отчёт', body: 'Сдать отчёт в пятницу');
      final events = await sync.aiGateway.ask('отчёт').toList();
      expect(
        events.any(
          (e) => e.kind == AiEventKind.status && e.text == 'local_search',
        ),
        isTrue,
      );
      expect(
        events
            .where((e) => e.kind == AiEventKind.token)
            .map((e) => e.text)
            .join(),
        contains('пятницу'),
      );
      expect(
        events.any(
          (e) => e.kind == AiEventKind.status && e.text == 'connecting',
        ),
        isFalse,
      );
    },
  );
  test(
    'AI rejects foreign or stale attached snapshots before fallback',
    () async {
      final note = await store.saveNote(title: 'Личное', body: 'Точный текст');
      for (final context in [
        const AiContext(
          noteId: '30000000-0000-4000-8000-000000000099',
          title: 'Чужая',
          body: 'Секрет',
        ),
        AiContext(
          noteId: note.uuid,
          title: note.title,
          body: 'Устаревший текст',
        ),
      ]) {
        final events = await sync.aiGateway
            .ask('Покажи', contexts: [context])
            .toList();
        expect(events.single.kind, AiEventKind.error);
        expect(events.any((e) => e.kind == AiEventKind.token), isFalse);
      }
      expect((await store.getNote(note.id))!.body, 'Точный текст');
    },
  );
  test('AI rejects deleted attachment and shutdown cancels access', () async {
    final note = await store.saveNote(title: 'Удаляемая', body: 'Текст');
    await store.deleteNote(note.id);
    final events = await sync.aiGateway
        .ask(
          'Покажи',
          contexts: [
            AiContext(noteId: note.uuid, title: note.title, body: note.body),
          ],
        )
        .toList();
    expect(events.single.kind, AiEventKind.error);
    await sync.shutdown();
    await expectLater(sync.aiGateway.ask('Текст').toList(), throwsStateError);
  });
  test(
    'AI endpoint is explicit and cannot reuse synchronization port',
    () async {
      for (final port in [0, -1, 65536, 7443, '7444']) {
        final payload = jsonDecode(qr()) as Map<String, dynamic>;
        payload['ai_port'] = port;
        await expectLater(
          sync.pairQr(jsonEncode(payload), confirmed: true),
          throwsFormatException,
        );
        expect(sync.devices, isEmpty);
      }
      final payload = jsonDecode(qr()) as Map<String, dynamic>;
      payload['ai_port'] = 7444;
      await sync.pairQr(jsonEncode(payload), confirmed: true);
      expect(sync.devices.single.actorId, peer);
      // A grant without a local TLS identity still cannot initiate remote AI.
      final events = await sync.aiGateway.ask('Текст').toList();
      expect(events.first.text, 'local_search');
    },
  );
}

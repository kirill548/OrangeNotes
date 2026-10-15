import 'dart:convert';

import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';
import 'package:orange_notes_mobile/data/mobile_store.dart';
import 'package:orange_notes_mobile/services/mobile_sync.dart';

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
}

import 'dart:async';
import 'dart:convert';

import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';
import 'package:orange_notes_mobile/data/mobile_store.dart';
import 'package:orange_notes_mobile/services/ai_gateway.dart';
import 'package:orange_notes_mobile/services/mobile_sync.dart';

class GatedStorage extends FlutterSecureStorage {
  final gate = Completer<void>(), began = Completer<void>();
  @override
  Future<String?> read({
    required String key,
    IOSOptions? iOptions,
    AndroidOptions? aOptions,
    LinuxOptions? lOptions,
    WebOptions? webOptions,
    MacOsOptions? mOptions,
    WindowsOptions? wOptions,
  }) async {
    if (!began.isCompleted) began.complete();
    await gate.future;
    return null;
  }
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  sqfliteFfiInit();
  const actor = '20000000-0000-4000-8000-000000000001';
  const space = '10000000-0000-4000-8000-000000000001';
  late MobileStore store;
  late MobileSync sync;
  setUp(() async {
    FlutterSecureStorage.setMockInitialValues({});
    store = await MobileStore.open(
      databaseFactory: databaseFactoryFfi,
      path: inMemoryDatabasePath,
      actorId: actor,
      workspaceId: space,
    );
    sync = MobileSync(store);
  });
  tearDown(() async {
    await sync.shutdown();
    sync.dispose();
    await store.close();
  });

  test('simultaneous requests are rejected during initialization, not silently replaced', () async {
    final storage = GatedStorage();
    sync.dispose();
    sync = MobileSync(store, storage: storage);
    final first = sync.aiGateway.ask('заметка').toList();
    await storage.began.future;
    await expectLater(sync.aiGateway.ask('вторая').toList(), throwsStateError);
    storage.gate.complete();
    expect((await first).last.kind, AiEventKind.done);
  });
  test(
    'shutdown cancels an initializing AI request before database close',
    () async {
      final storage = GatedStorage();
      sync.dispose();
      sync = MobileSync(store, storage: storage);
      final first = sync.aiGateway.ask('заметка').toList();
      final checked = expectLater(first, throwsStateError);
      await storage.began.future;
      final stopped = sync.shutdown();
      storage.gate.complete();
      await stopped;
      await checked;
    },
  );
  test('stale or foreign attachment cannot enter relay context', () async {
    final note = await store.saveNote(title: 'План', body: 'Точный текст');
    final events = await sync.aiGateway
        .ask(
          'План?',
          contexts: [
            AiContext(
              noteId: note.uuid,
              title: note.title,
              body: 'Подменённый текст',
            ),
          ],
        )
        .toList();
    expect(events.single.kind, AiEventKind.error);
    expect(events.single.text, contains('Прикрепите'));
  });
  test('corrupt stored identity falls back to scoped local search', () async {
    await store.saveNote(title: 'Молоко', body: 'Купить молоко');
    FlutterSecureStorage.setMockInitialValues({
      'p2p_identity_$actor': jsonEncode({
        'actor_id': actor,
        'ca_pem': 'broken',
      }),
      'p2p_peers_$actor': jsonEncode([
        {
          'actor_id': '20000000-0000-4000-8000-000000000002',
          'workspace_id': space,
          'host': '192.168.1.20',
          'port': 7443,
          'ai_port': 7444,
          'server_hostname': 'desktop.local',
          'fingerprint_sha256': 'a' * 64,
        },
      ]),
    });
    final events = await sync.aiGateway.ask('Найди молоко').toList();
    expect(events.first.text, 'local_search');
    expect(
      events
          .singleWhere((e) => e.kind == AiEventKind.token)
          .sources
          .single
          .title,
      'Молоко',
    );
  });
}

import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:flutter_test/flutter_test.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';
import 'package:orange_notes_mobile/data/mobile_store.dart';
import 'package:orange_notes_mobile/services/ai_gateway.dart';
import 'package:orange_notes_mobile/services/p2p_transport.dart';

const space = '10000000-0000-4000-8000-000000000001';
const actorA = '20000000-0000-4000-8000-000000000001';
const actorB = '20000000-0000-4000-8000-000000000002';
const noteId = '30000000-0000-4000-8000-000000000001';

Future<Map<String, dynamic>> readRequest(SecureSocket socket) async {
  final buffer = <int>[];
  await for (final bytes in socket) {
    buffer.addAll(bytes);
    if (buffer.length < 4) continue;
    final length = ByteData.sublistView(
      Uint8List.fromList(buffer.take(4).toList()),
    ).getUint32(0);
    if (buffer.length >= 4 + length) {
      return jsonDecode(utf8.decode(buffer.sublist(4, 4 + length)))
          as Map<String, dynamic>;
    }
  }
  throw StateError('No request');
}

Future<void> writeEvent(SecureSocket socket, Map<String, dynamic> frame) async {
  final bytes = utf8.encode(jsonEncode(frame));
  final prefix = ByteData(4)..setUint32(0, bytes.length);
  socket.add(prefix.buffer.asUint8List());
  socket.add(bytes);
  await socket.flush();
}

class StubGateway implements AiGateway {
  StubGateway(this.events, {this.failure = false});
  final List<AiEvent> events;
  final bool failure;
  bool cancelled = false;
  @override
  void cancel() {
    cancelled = true;
  }

  @override
  Stream<AiEvent> ask(
    String prompt, {
    List<AiContext> contexts = const [],
  }) async* {
    yield* Stream.fromIterable(events);
    if (failure) throw StateError('offline');
  }
}

void main() {
  final fixtures = jsonDecode(
    File('test/fixtures/PUBLIC_TEST_TLS.json').readAsStringSync(),
  ) as Map;
  SecurityContext context(String who) => createTlsContext(
    caPem: utf8.encode(fixtures['ca'] as String),
    certPem: utf8.encode(fixtures[who]['cert'] as String),
    keyPem: utf8.encode(fixtures[who]['key'] as String),
  );
  final sockets = <SecureSocket>[];
  final tasks = <Future<void>>[];
  late SecureServerSocket server;
  late DesktopAiGateway gateway;
  Future<void> start(
    Future<void> Function(SecureSocket, Map<String, dynamic>) handler, {
    String? pin,
    String hostname = 'localhost',
    Duration timeout = const Duration(seconds: 2),
  }) async {
    server = await SecureServerSocket.bind(
      '127.0.0.1',
      0,
      context('b'),
      requireClientCertificate: true,
    );
    server.listen((socket) {
      sockets.add(socket);
      tasks.add(() async {
        try {
          await handler(socket, await readRequest(socket));
        } catch (_) {
          /* Rejected/cancelled sockets are expected. */
        } finally {
          socket.destroy();
        }
      }());
    }, onError: (Object _) {});
    gateway = DesktopAiGateway(
      context: context('a'),
      localActorId: actorA,
      workspaceId: space,
      peer: PeerIdentity(actorB, space, pin ?? fixtures['b']['pin'] as String),
      host: '127.0.0.1',
      port: server.port,
      serverHostname: hostname,
      timeout: timeout,
    );
    addTearDown(() async {
      gateway.cancel();
      for (final socket in sockets) {
        socket.destroy();
      }
      await server.close();
      await Future.wait(tasks);
      sockets.clear();
      tasks.clear();
    });
  }

  Map<String, dynamic> event(
    Map request,
    String type,
    int sequence, [
    String? text,
  ]) => {
    'version': 1,
    'type': type,
    'request_id': request['request_id'],
    'sequence': sequence,
    'text': ?text,
  };

  test(
    'real mTLS sends bounded context and streams UTF8 token frames',
    () async {
      await start((socket, request) async {
        expect(request['actor_id'], actorA);
        expect(request['workspace_id'], space);
        expect(request['type'], 'ai.request');
        expect((request['contexts'] as List).single['body'], 'План 🐝');
        await writeEvent(socket, event(request, 'ai.token', 0, 'Привет 🐝'));
        await writeEvent(socket, event(request, 'ai.token', 1, '!'));
        await writeEvent(socket, event(request, 'ai.done', 2));
      });
      final events = await gateway
          .ask(
            'План?',
            contexts: const [
              AiContext(noteId: noteId, title: 'План', body: 'План 🐝'),
            ],
          )
          .toList();
      expect(
        events
            .where((e) => e.kind == AiEventKind.token)
            .map((e) => e.text)
            .join(),
        'Привет 🐝!',
      );
      expect(events.last.kind, AiEventKind.done);
    },
  );
  for (final mode in ['pin', 'hostname']) {
    test('untrusted $mode prevents sending any prompt', () async {
      var received = false;
      await start(
        (socket, request) async {
          received = true;
        },
        pin: mode == 'pin' ? fixtures['a']['pin'] as String : null,
        hostname: mode == 'hostname' ? 'not-localhost.invalid' : 'localhost',
      );
      await expectLater(gateway.ask('secret').toList(), throwsA(anything));
      expect(received, false);
    });
  }
  for (final mode in ['sequence', 'request', 'extra', 'version']) {
    test('rejects malformed streamed $mode', () async {
      await start((socket, request) async {
        final frame = event(request, 'ai.token', 0, 'text');
        if (mode == 'sequence') frame['sequence'] = 9;
        if (mode == 'request') frame['request_id'] = noteId;
        if (mode == 'extra') frame['tool_call'] = 'delete';
        if (mode == 'version') frame['version'] = 1.0;
        await writeEvent(socket, frame);
      });
      await expectLater(
        gateway.ask('question').toList(),
        throwsA(isA<TransportError>()),
      );
    });
  }
  test('deadline closes stalled connection', () async {
    final released = Completer<void>();
    await start((socket, request) async {
      await released.future;
    }, timeout: const Duration(milliseconds: 150));
    try {
      await expectLater(gateway.ask('question').toList(), throwsA(anything));
    } finally {
      released.complete();
    }
  });
  test(
    'explicit cancel closes active request without fallback content',
    () async {
      final began = Completer<void>(), released = Completer<void>();
      await start((socket, request) async {
        began.complete();
        await released.future;
      });
      final result = gateway.ask('question').toList();
      await began.future;
      gateway.cancel();
      try {
        await result;
      } catch (_) {
        /* Stream close is cancellation. */
      }
      released.complete();
    },
  );
  test('context and prompt limits reject before network use', () async {
    await start((socket, request) async {});
    await expectLater(
      gateway.ask('x' * 8193).toList(),
      throwsA(isA<TransportError>()),
    );
    await expectLater(
      gateway
          .ask(
            'x',
            contexts: const [
              AiContext(noteId: noteId, title: '', body: ''),
              AiContext(noteId: noteId, title: '', body: ''),
            ],
          )
          .toList(),
      throwsA(isA<TransportError>()),
    );
  });
  test(
    'fallback uses explicitly labeled local result on connection failure',
    () async {
      final local = StubGateway(const [
        AiEvent(AiEventKind.status, text: 'local_search'),
        AiEvent(AiEventKind.done),
      ]);
      final events = await FallbackAiGateway(
        StubGateway([], failure: true),
        local,
      ).ask('hello').toList();
      expect(events.first.text, 'local_search');
    },
  );
  test('partial generated answer never mixes with local fallback', () async {
    final local = StubGateway(const [
      AiEvent(AiEventKind.token, text: 'local'),
    ]);
    final events = await FallbackAiGateway(
      StubGateway(const [
        AiEvent(AiEventKind.token, text: 'partial'),
      ], failure: true),
      local,
    ).ask('hello').toList();
    expect(events.last.kind, AiEventKind.error);
    expect(events.any((e) => e.text == 'local'), false);
  });
  test(
    'offline search returns actual scoped SQLite citations without mutation',
    () async {
      sqfliteFfiInit();
      final directory = await Directory.systemTemp.createTemp(
        'orange-ai-search',
      );
      final store = await MobileStore.open(
        databaseFactory: databaseFactoryFfi,
        path: '${directory.path}/notes.db',
        actorId: actorA,
        workspaceId: space,
      );
      try {
        final note = await store.saveNote(
          title: 'Отчёт',
          body: 'Сдать отчёт в пятницу',
        );
        final events = await LocalAiGateway(store)
            .ask('Найди мои заметки про отчёт')
            .toList();
        final answer = events.singleWhere((e) => e.kind == AiEventKind.token);
        expect(events.first.text, 'local_search');
        expect(answer.text, contains('без генерации'));
        expect(answer.sources.single.noteId, note.uuid);
        expect((await store.listNotes()).length, 1);
        final missing = await LocalAiGateway(store)
            .ask('несуществующее')
            .toList();
        expect(
          missing.singleWhere((e) => e.kind == AiEventKind.token).text,
          contains('совпадений нет'),
        );
      } finally {
        await store.close();
        await directory.delete(recursive: true);
      }
    },
  );
  test(
    'FTS tracks edits and excludes archived deleted and foreign notes',
    () async {
      sqfliteFfiInit();
      final directory = await Directory.systemTemp.createTemp('orange-ai-fts');
      final store = await MobileStore.open(
        databaseFactory: databaseFactoryFfi,
        path: '${directory.path}/notes.db',
        actorId: actorA,
        workspaceId: space,
      );
      try {
        final note = await store.saveNote(
          title: 'Отчет',
          body: 'Космонавт готов',
        );
        expect(
          (await store.searchNotes('найди про космонавт')).single.uuid,
          note.uuid,
        );
        await store.saveNote(id: note.id, title: 'Отчет', body: 'План встречи');
        expect(await store.searchNotes('космонавт'), isEmpty);
        expect(
          (await store.searchNotes('несуществующее или встреч')).single.uuid,
          note.uuid,
        );
        expect(await store.searchNotes('" OR * --'), isEmpty);
        await store.db.update(
          'notes',
          {'archived': 1},
          where: 'id=?',
          whereArgs: [note.id],
        );
        expect(await store.searchNotes('встреч'), isEmpty);
        await store.db.update(
          'notes',
          {'archived': 0, 'deleted': 1},
          where: 'id=?',
          whereArgs: [note.id],
        );
        expect(await store.searchNotes('встреч'), isEmpty);
        await store.db.insert('workspaces', {'id': 2, 'name': 'Другое'});
        await store.db.update(
          'notes',
          {'deleted': 0, 'workspace_id': 2},
          where: 'id=?',
          whereArgs: [note.id],
        );
        expect(await store.searchNotes('встреч'), isEmpty);
      } finally {
        await store.close();
        await directory.delete(recursive: true);
      }
    },
  );
  test(
    'v1 migration rebuilds FTS preserving sync history and scan fallback',
    () async {
      sqfliteFfiInit();
      final directory = await Directory.systemTemp.createTemp(
        'orange-ai-migrate',
      );
      final path = '${directory.path}/notes.db';
      var store = await MobileStore.open(
        databaseFactory: databaseFactoryFfi,
        path: path,
        actorId: actorA,
        workspaceId: space,
      );
      try {
        final note = await store.saveNote(
          title: 'Молоко',
          body: 'Купить молоко',
        );
        final history = await store.db.query('sync_events');
        for (final action in ['insert', 'update', 'delete']) {
          await store.db.execute('DROP TRIGGER IF EXISTS note_search_$action');
        }
        await store.db.execute('DROP TABLE IF EXISTS note_search');
        expect(
          (await store.searchNotes('Покажи заметки про молоко')).single.uuid,
          note.uuid,
        );
        await store.db.execute('PRAGMA user_version=1');
        await store.close();
        store = await MobileStore.open(
          databaseFactory: databaseFactoryFfi,
          path: path,
        );
        expect((await store.searchNotes('молок')).single.uuid, note.uuid);
        expect(await store.db.query('sync_events'), history);
        expect(
          (await store.db.rawQuery('PRAGMA user_version')).single.values.single,
          2,
        );
      } finally {
        await store.close();
        await directory.delete(recursive: true);
      }
    },
  );
}

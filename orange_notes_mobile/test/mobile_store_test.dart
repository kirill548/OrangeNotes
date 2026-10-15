import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';
import 'package:orange_notes_mobile/data/mobile_store.dart';
import 'package:orange_notes_mobile/services/crdt_engine.dart';

const space = '10000000-0000-4000-8000-000000000001',
    actorA = '20000000-0000-4000-8000-000000000001',
    actorB = '20000000-0000-4000-8000-000000000002';
void main() {
  sqfliteFfiInit();
  late Directory dir;
  late MobileStore a, b;
  setUp(() async {
    dir = await Directory.systemTemp.createTemp('orange-mobile-test');
    a = await MobileStore.open(
      databaseFactory: databaseFactoryFfi,
      path: '${dir.path}/a.db',
      workspaceId: space,
      actorId: actorA,
    );
    b = await MobileStore.open(
      databaseFactory: databaseFactoryFfi,
      path: '${dir.path}/b.db',
      workspaceId: space,
      actorId: actorB,
    );
    await a.grantPeer(actorB);
    await b.grantPeer(actorA);
  });
  tearDown(() async {
    await a.close();
    await b.close();
    await dir.delete(recursive: true);
  });
  test('offline CRUD search tags wiki backlinks graph', () async {
    final target = await a.saveNote(
      title: 'Отчёт',
      body: 'План',
      tags: ['Работа'],
    );
    final source = await a.saveNote(
      title: 'Встреча',
      body: 'См. [[Отчёт|план]]',
    );
    expect((await a.listNotes(query: 'план')).length, 2);
    expect((await a.listNotes(tag: 'Работа')).single.id, target.id);
    expect((await a.backlinks(target.id)).single.id, source.id);
    expect((await a.linkedNotes(source.id)).single.id, target.id);
    expect((await a.graph()).length, 1);
    await a.deleteNote(target.id);
    expect(await a.backlinks(target.id), isEmpty);
    expect((await a.graph()), isEmpty);
    expect((await a.listNotes()).length, 1);
  });
  test('atomic outbox and invalid mutation rollback', () async {
    await a.saveNote(title: 'Первое', body: 'Текст');
    final before = await a.pendingOutbox(actorB);
    await expectLater(
      a.saveNote(title: List.filled(1025, 'a').join(), body: 'x'),
      throwsA(isA<DeltaError>()),
    );
    expect(await a.pendingOutbox(actorB), before);
    expect((await a.listNotes()).length, 1);
    await expectLater(
      a.saveNote(
        title: 'Второе',
        body: 'x',
        tags: [List.filled(129, 'a').join()],
      ),
      throwsA(isA<DeltaError>()),
    );
    expect(await a.pendingOutbox(actorB), before);
    expect((await a.listNotes()).length, 1);
  });
  test(
    'two replicas retry ACK and restart preserve notes and history',
    () async {
      final note = await a.saveNote(title: 'Задача', body: 'Купить молоко');
      final wire = (await a.pendingOutbox(actorB)).single;
      final ack = await b.receiveDelta(wire, actorA);
      expect(await b.receiveDelta(wire, actorA), ack);
      expect((await b.listNotes()).single.uuid, note.uuid);
      expect((await a.pendingOutbox(actorB)).length, 1);
      await a.acknowledge(actorB, ack);
      expect(await a.pendingOutbox(actorB), isEmpty);
      await a.close();
      a = await MobileStore.open(
        databaseFactory: databaseFactoryFfi,
        path: '${dir.path}/a.db',
      );
      expect((await a.listNotes()).single.body, 'Купить молоко');
      expect(await a.pendingOutbox(actorB), isEmpty);
    },
  );
  test(
    'untrusted actor workspace mismatch and equivocation leave inbox unchanged',
    () async {
      await a.saveNote(title: 'Секрет', body: 'Текст');
      final wire = (await a.pendingOutbox(actorB)).single;
      await b.revokePeer(actorA);
      await expectLater(
        b.receiveDelta(wire, actorA),
        throwsA(isA<DeltaError>()),
      );
      expect(await b.listNotes(), isEmpty);
      await b.grantPeer(actorA);
      await b.receiveDelta(wire, actorA);
      final data = jsonDecode(wire) as Map;
      ((data['ops'] as List).single['fields'] as Map)['title'] = 'Подмена';
      await expectLater(
        b.receiveDelta(canonicalJson(data), actorA),
        throwsA(isA<DeltaError>()),
      );
      expect((await b.listNotes()).single.title, 'Секрет');
    },
  );
  test('desktop HTML body exact roundtrip readonly', () async {
    final replica = Replica(space, actorA),
        wire = replica.makeDelta('30000000-0000-4000-8000-000000000001', {
          'title': 'HTML',
          'body': '<p><b>Сохранить</b> [[Отчёт]]</p>',
          'lifecycle': 'active',
        });
    await b.receiveDelta(wire, actorA);
    final note = (await b.listNotes()).single;
    expect(note.readOnlyHtml, isTrue);
    expect(note.body, '<p><b>Сохранить</b> [[Отчёт]]</p>');
    await expectLater(
      b.saveNote(id: note.id, title: 'Правка', body: 'Потеря'),
      throwsA(isA<DeltaError>()),
    );
    expect((await b.getNote(note.id))!.body, note.body);
  });
  test('ambiguous title links are not resolved arbitrarily', () async {
    await a.saveNote(title: 'План', body: '1');
    await a.saveNote(title: 'План', body: '2');
    final source = await a.saveNote(title: 'Ссылка', body: '[[План]]');
    expect(await a.linkedNotes(source.id), isEmpty);
    expect(await a.graph(), isEmpty);
  });
  test(
    'concurrent fields converge and body conflict history survives replay',
    () async {
      final note = await a.saveNote(title: 'Исходное', body: 'Основа');
      final first = (await a.pendingOutbox(actorB)).single;
      await b.receiveDelta(first, actorA);
      await a.acknowledge(actorB, await b.receiveDelta(first, actorA));
      final remote = (await b.listNotes()).single;
      await a.saveNote(id: note.id, title: 'Заголовок A', body: 'Версия A');
      await b.saveNote(id: remote.id, title: 'Заголовок B', body: 'Версия B');
      final aw = (await a.pendingOutbox(actorB)).single,
          bw = (await b.pendingOutbox(actorA)).single;
      await a.receiveDelta(bw, actorB);
      await b.receiveDelta(aw, actorA);
      expect(
        (await a.getNote(note.id))!.body,
        (await b.getNote(remote.id))!.body,
      );
      expect(
        (await a.bodyConflicts(note.id)).map((c) => c['body']),
        contains('Версия A'),
      );
      await a.close();
      a = await MobileStore.open(
        databaseFactory: databaseFactoryFfi,
        path: '${dir.path}/a.db',
      );
      expect(
        (await a.bodyConflicts(note.id)).map((c) => c['body']),
        contains('Версия A'),
      );
    },
  );
  test('inbox and projected note rollback together on SQL failure', () async {
    await a.saveNote(title: 'Атомарно', body: 'Текст');
    final wire = (await a.pendingOutbox(actorB)).single;
    await b.db.execute(
      "CREATE TRIGGER test_fail BEFORE INSERT ON sync_inbox BEGIN SELECT RAISE(ABORT,'injected'); END",
    );
    await expectLater(
      b.receiveDelta(wire, actorA),
      throwsA(isA<DatabaseException>()),
    );
    expect(await b.listNotes(), isEmpty);
    expect(await b.db.query('sync_events'), isEmpty);
    expect(await b.db.query('sync_inbox'), isEmpty);
    await b.db.execute('DROP TRIGGER test_fail');
    await b.receiveDelta(wire, actorA);
    expect((await b.listNotes()).length, 1);
  });
  test('out-of-band note edits and enrollment mismatch fail closed', () async {
    final note = await a.saveNote(title: 'Оригинал', body: 'Текст');
    await a.db.update(
      'notes',
      {'body': 'В обход'},
      where: 'id=?',
      whereArgs: [note.id],
    );
    await expectLater(
      a.saveNote(id: note.id, title: 'Новый', body: 'Текст'),
      throwsA(isA<DeltaError>()),
    );
    expect((await a.pendingOutbox(actorB)).length, 1);
  });
  test(
    'explicit empty workspace join persists and nonempty join rejects',
    () async {
      const other = '10000000-0000-4000-8000-000000000002';
      await a.revokePeer(actorB);
      await a.joinEmptyWorkspace(other);
      expect(a.workspaceId, other);
      await a.close();
      a = await MobileStore.open(
        databaseFactory: databaseFactoryFfi,
        path: '${dir.path}/a.db',
      );
      expect(a.workspaceId, other);
      await a.saveNote(title: 'Локальная', body: 'Сохранить');
      await expectLater(
        a.joinEmptyWorkspace(space),
        throwsA(isA<DeltaError>()),
      );
      expect(a.workspaceId, other);
      expect((await a.listNotes()).single.title, 'Локальная');
    },
  );
  test(
    'remote deletion rejects stale editor save without outbox resurrection',
    () async {
      final note = await a.saveNote(
        title: 'Открытый редактор',
        body: 'Исходное',
      );
      final initial = (await a.pendingOutbox(actorB)).single;
      await b.receiveDelta(initial, actorA);
      final remote = (await b.listNotes()).single;
      await b.deleteNote(remote.id);
      await a.receiveDelta((await b.pendingOutbox(actorA)).single, actorB);
      final before = await a.pendingOutbox(actorB);
      await expectLater(
        a.saveNote(
          id: note.id,
          title: 'Несохранённое',
          body: 'Запоздалый автосейв',
        ),
        throwsA(isA<DeltaError>()),
      );
      expect(await a.getNote(note.id), isNull);
      expect(await a.pendingOutbox(actorB), before);
      expect(
        (await a.db.query(
          'notes',
          where: 'id=?',
          whereArgs: [note.id],
        )).single['deleted'],
        1,
      );
    },
  );
  test(
    'edits omit lifecycle so concurrent body delivery cannot restore deletion',
    () async {
      final note = await a.saveNote(title: 'Заметка', body: 'Исходное');
      await b.receiveDelta((await a.pendingOutbox(actorB)).single, actorA);
      final remote = (await b.listNotes()).single;
      await a.saveNote(id: note.id, title: 'Правка', body: 'Новый текст');
      final edit = (await a.pendingOutbox(actorB)).last;
      final fields =
          ((jsonDecode(edit)['ops'] as List).single['fields'] as Map);
      expect(fields.containsKey('lifecycle'), isFalse);
      await b.deleteNote(remote.id);
      await b.receiveDelta(edit, actorA);
      expect(await b.getNote(remote.id), isNull);
      expect(
        (await b.db.query(
          'notes',
          where: 'id=?',
          whereArgs: [remote.id],
        )).single['body'],
        'Новый текст',
      );
      expect(
        (await b.db.query(
          'notes',
          where: 'id=?',
          whereArgs: [remote.id],
        )).single['deleted'],
        1,
      );
    },
  );
}

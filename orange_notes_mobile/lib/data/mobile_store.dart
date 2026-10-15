import 'dart:convert';

import 'package:crypto/crypto.dart';
import 'package:html/parser.dart' as html;
import 'package:path/path.dart' as p;
import 'package:sqflite/sqflite.dart';
import 'package:sqflite/sqflite.dart' as sqlite;
import 'package:uuid/uuid.dart';

import '../domain/note.dart';
import '../services/crdt_engine.dart';

/// One workspace per database. Wire fields intentionally match desktop v1;
/// tags remain device-local until a negotiated protocol adds tag operations.
class MobileStore {
  final Database db;
  String workspaceId;
  final String actorId;
  MobileStore._(this.db, this.workspaceId, this.actorId);
  static Future<MobileStore> open({
    DatabaseFactory? databaseFactory,
    String? path,
    String? actorId,
    String? workspaceId,
  }) async {
    final factory = databaseFactory ?? sqlite.databaseFactory;
    final location =
        path ?? p.join(await factory.getDatabasesPath(), 'orange_notes.db');
    final database = await factory.openDatabase(
      location,
      options: OpenDatabaseOptions(
        version: 1,
        onConfigure: (db) async {
          await db.execute('PRAGMA foreign_keys=ON');
          await db.rawQuery('PRAGMA journal_mode=WAL');
          await db.execute('PRAGMA synchronous=FULL');
        },
        onCreate: (db, version) async {
          for (final sql in _schema) {
            await db.execute(sql);
          }
          final actor = validUuid(actorId ?? const Uuid().v4()),
              space = validUuid(workspaceId ?? const Uuid().v4());
          await db.insert('sync_device', {
            'singleton': 1,
            'device_uuid': actor,
          });
          await db.insert('workspaces', {'id': 1, 'name': 'Личное'});
          await db.insert('sync_workspaces', {
            'local_id': 1,
            'workspace_uuid': space,
          });
          await db.insert('sync_p2p_schema', {
            'singleton': 1,
            'version': 1,
            'workspace_id': space,
            'local_workspace_id': 1,
            'actor_id': actor,
          });
        },
      ),
    );
    try {
      final binding = (await database.query('sync_p2p_schema')).single;
      final actor = validUuid(binding['actor_id']),
          space = validUuid(binding['workspace_id']);
      if ((actorId != null && actorId != actor) ||
          (workspaceId != null && workspaceId != space)) {
        throw DeltaError('Store enrollment mismatch');
      }
      final store = MobileStore._(database, space, actor);
      final replica = await store._replay(database);
      await store._verify(database, replica);
      return store;
    } catch (_) {
      await database.close();
      rethrow;
    }
  }

  static const _schema = [
    'CREATE TABLE workspaces(id INTEGER PRIMARY KEY,name TEXT NOT NULL)',
    'CREATE TABLE notes(id INTEGER PRIMARY KEY AUTOINCREMENT,title TEXT NOT NULL,body TEXT NOT NULL,workspace_id INTEGER NOT NULL REFERENCES workspaces(id),deleted INTEGER NOT NULL DEFAULT 0,archived INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)',
    'CREATE TABLE sync_device(singleton INTEGER PRIMARY KEY CHECK(singleton=1),device_uuid TEXT NOT NULL)',
    'CREATE TABLE sync_workspaces(local_id INTEGER PRIMARY KEY REFERENCES workspaces(id),workspace_uuid TEXT UNIQUE NOT NULL)',
    'CREATE TABLE sync_identities(entity TEXT NOT NULL,local_id INTEGER NOT NULL,entity_uuid TEXT UNIQUE NOT NULL,tombstone INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(entity,local_id))',
    'CREATE TABLE sync_p2p_schema(singleton INTEGER PRIMARY KEY CHECK(singleton=1),version INTEGER NOT NULL CHECK(version=1),workspace_id TEXT NOT NULL,local_workspace_id INTEGER NOT NULL,actor_id TEXT NOT NULL)',
    'CREATE TABLE sync_events(actor_id TEXT NOT NULL,clock INTEGER NOT NULL,wire TEXT NOT NULL,PRIMARY KEY(actor_id,clock))',
    'CREATE TABLE sync_outbox(sha256 TEXT PRIMARY KEY,wire TEXT NOT NULL)',
    'CREATE TABLE sync_outbox_ack(sha256 TEXT NOT NULL REFERENCES sync_outbox(sha256) ON DELETE CASCADE,peer_actor TEXT NOT NULL,PRIMARY KEY(sha256,peer_actor))',
    'CREATE TABLE sync_inbox(sha256 TEXT PRIMARY KEY,actor_id TEXT NOT NULL,clock INTEGER NOT NULL,UNIQUE(actor_id,clock))',
    'CREATE TABLE note_links(source_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,target_id INTEGER REFERENCES notes(id) ON DELETE CASCADE,link_text TEXT NOT NULL,context_snippet TEXT NOT NULL)',
    'CREATE TABLE note_tags(note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,tag TEXT NOT NULL,PRIMARY KEY(note_id,tag))',
    'CREATE TABLE sync_peers(actor_id TEXT PRIMARY KEY,trusted INTEGER NOT NULL DEFAULT 1)',
  ];
  Future<Replica> _replay(DatabaseExecutor tx) async {
    final r = Replica(workspaceId, actorId);
    for (final row in await tx.query(
      'sync_events',
      orderBy: 'clock,actor_id',
    )) {
      final wire = row['wire'] as String;
      if (!r.applyDelta(wire) || canonicalJson(strictJson(wire)) != wire) {
        throw DeltaError('Corrupt event history');
      }
      final d = strictJson(wire) as Map;
      if (d['actor_id'] != row['actor_id'] || d['clock'] != row['clock']) {
        throw DeltaError('Corrupt event identity');
      }
    }
    return r;
  }

  Future<void> _verify(DatabaseExecutor tx, Replica replica) async {
    final rows = await tx.rawQuery(
      "SELECT n.id,n.title,n.body,n.deleted,i.entity_uuid FROM notes n LEFT JOIN sync_identities i ON i.entity='notes' AND i.local_id=n.id AND i.tombstone=0 WHERE n.workspace_id=1",
    );
    final found = <String>{};
    for (final row in rows) {
      final entity = row['entity_uuid'];
      if (entity is! String || !found.add(entity)) {
        throw DeltaError('Corrupt note identity');
      }
      final expected = replica.get(entity);
      if (expected == null ||
          row['title'] != (expected['title'] ?? '') ||
          row['body'] != (expected['body'] ?? '') ||
          row['deleted'] != (expected['lifecycle'] == 'deleted' ? 1 : 0)) {
        throw DeltaError('Out-of-band note change');
      }
    }
    if (found.length != replica.entityIds.length) {
      throw DeltaError('Missing materialized note');
    }
  }

  /// Explicit user-confirmed enrollment only; never rebind existing content.
  Future<void> joinEmptyWorkspace(String remoteUuid) async {
    final remote = validUuid(remoteUuid);
    if (remote == workspaceId) return;
    await db.transaction((tx) async {
      for (final table in [
        'notes',
        'sync_events',
        'sync_outbox',
        'sync_inbox',
        'sync_peers',
      ]) {
        if ((await tx.query(table, limit: 1)).isNotEmpty) {
          throw DeltaError(
            'Пространство содержит данные или доверенные устройства. Для подключения нужно пустое пространство.',
          );
        }
      }
      await tx.update('sync_workspaces', {
        'workspace_uuid': remote,
      }, where: 'local_id=1');
      await tx.update('sync_p2p_schema', {
        'workspace_id': remote,
      }, where: 'singleton=1');
    });
    workspaceId = remote;
  }

  Future<void> grantPeer(String actor) async {
    validUuid(actor);
    if (actor == actorId) throw DeltaError('Self peer');
    await db.insert('sync_peers', {
      'actor_id': actor,
      'trusted': 1,
    }, conflictAlgorithm: ConflictAlgorithm.replace);
  }

  Future<void> revokePeer(String actor) async {
    await db.delete(
      'sync_peers',
      where: 'actor_id=?',
      whereArgs: [validUuid(actor)],
    );
  }

  Future<void> _peer(DatabaseExecutor tx, String actor) async {
    validUuid(actor);
    if ((await tx.query(
      'sync_peers',
      where: 'actor_id=? AND trusted=1',
      whereArgs: [actor],
    )).isEmpty) {
      throw DeltaError('Untrusted actor');
    }
  }

  Future<String> _entity(DatabaseExecutor tx, int id) async {
    final rows = await tx.rawQuery(
      "SELECT i.entity_uuid FROM sync_identities i JOIN notes n ON n.id=i.local_id WHERE i.entity='notes' AND i.local_id=? AND i.tombstone=0 AND n.workspace_id=1",
      [id],
    );
    if (rows.length != 1) throw DeltaError('Unknown note');
    return rows.single['entity_uuid'] as String;
  }

  Future<int> _persist(
    DatabaseExecutor tx,
    Replica r,
    String wire, {
    required bool outgoing,
  }) async {
    final d = strictJson(wire) as Map;
    var last = 0;
    for (final op in d['ops'] as List) {
      final entity = op['entity_id'] as String, state = r.get(entity)!;
      final mappings = await tx.query(
        'sync_identities',
        where: "entity='notes' AND entity_uuid=?",
        whereArgs: [entity],
      );
      final stamp = DateTime.now().toUtc().toIso8601String();
      final values = <String, Object?>{
        'title': state['title'] ?? '',
        'body': state['body'] ?? '',
        'deleted': state['lifecycle'] == 'deleted' ? 1 : 0,
        'updated_at': stamp,
      };
      if (mappings.isEmpty) {
        last = await tx.insert('notes', {
          ...values,
          'workspace_id': 1,
          'created_at': stamp,
        });
        await tx.insert('sync_identities', {
          'entity': 'notes',
          'local_id': last,
          'entity_uuid': entity,
        });
      } else {
        if (mappings.single['tombstone'] != 0) {
          throw DeltaError('Removed identity');
        }
        last = mappings.single['local_id'] as int;
        final changed = await tx.update(
          'notes',
          values,
          where: 'id=? AND workspace_id=1',
          whereArgs: [last],
        );
        if (changed != 1) throw DeltaError('Foreign workspace');
      }
    }
    await tx.insert('sync_events', {
      'actor_id': d['actor_id'],
      'clock': d['clock'],
      'wire': wire,
    });
    final digest = sha256.convert(utf8.encode(wire)).toString();
    if (outgoing) {
      await tx.insert('sync_outbox', {'sha256': digest, 'wire': wire});
    } else {
      await tx.insert('sync_inbox', {
        'sha256': digest,
        'actor_id': d['actor_id'],
        'clock': d['clock'],
      });
    }
    return last;
  }

  Future<NoteRecord> saveNote({
    int? id,
    required String title,
    required String body,
    List<String> tags = const [],
  }) async {
    final result = await db.transaction((tx) async {
      if (id != null) {
        final existing = (await tx.query(
          'notes',
          where: 'id=? AND workspace_id=1',
          whereArgs: [id],
        )).single;
        if (_isHtml(existing['body'] as String)) {
          throw DeltaError(
            'HTML note is read-only; original formatting is preserved',
          );
        }
      }
      final r = await _replay(tx);
      await _verify(tx, r);
      final entity = id == null ? const Uuid().v4() : await _entity(tx, id);
      final wire = r.makeDelta(entity, {
        'title': title,
        'body': body,
        'lifecycle': 'active',
      });
      final local = await _persist(tx, r, wire, outgoing: true);
      await tx.delete('note_tags', where: 'note_id=?', whereArgs: [local]);
      for (final tag
          in tags.map((t) => t.trim()).where((t) => t.isNotEmpty).toSet()) {
        if (tag.length > 128) throw DeltaError('Tag too long');
        await tx.insert('note_tags', {'note_id': local, 'tag': tag});
      }
      await _index(tx);
      return local;
    });
    return (await getNote(result))!;
  }

  Future<void> deleteNote(int id) async {
    await db.transaction((tx) async {
      final r = await _replay(tx);
      await _verify(tx, r);
      final wire = r.makeDelta(await _entity(tx, id), {'lifecycle': 'deleted'});
      await _persist(tx, r, wire, outgoing: true);
      await _index(tx);
    });
  }

  Future<List<String>> pendingOutbox(String peer) async {
    await _peer(db, peer);
    final rows = await db.rawQuery(
      'SELECT o.wire FROM sync_outbox o WHERE NOT EXISTS(SELECT 1 FROM sync_outbox_ack a WHERE a.sha256=o.sha256 AND a.peer_actor=?) ORDER BY o.rowid',
      [peer],
    );
    return rows.map((r) => r['wire'] as String).toList();
  }

  Future<Map<String, String>> receiveDelta(
    String wire,
    String authorizedActor,
  ) async {
    return db.transaction((tx) async {
      await _peer(tx, authorizedActor);
      final r = await _replay(tx);
      await _verify(tx, r);
      final accepted = r.applyDelta(wire), data = strictJson(wire) as Map;
      if (data['actor_id'] != authorizedActor) {
        throw DeltaError('Actor differs from authenticated peer');
      }
      final canonical = canonicalJson(data);
      if (accepted) {
        await _persist(tx, r, canonical, outgoing: false);
        await _index(tx);
      }
      return {
        'sha256': sha256.convert(utf8.encode(canonical)).toString(),
        'actor_id': actorId,
        'workspace_id': workspaceId,
      };
    });
  }

  Future<void> acknowledge(String peer, Map<String, dynamic> receipt) async {
    await db.transaction((tx) async {
      await _peer(tx, peer);
      final digest = receipt['sha256'];
      if (receipt['actor_id'] != peer ||
          receipt['workspace_id'] != workspaceId ||
          digest is! String ||
          !RegExp(r'^[0-9a-f]{64}$').hasMatch(digest)) {
        throw DeltaError('Invalid ACK');
      }
      if ((await tx.query(
        'sync_outbox',
        where: 'sha256=?',
        whereArgs: [digest],
      )).isEmpty) {
        throw DeltaError('Unknown ACK');
      }
      await tx.insert('sync_outbox_ack', {
        'sha256': digest,
        'peer_actor': peer,
      }, conflictAlgorithm: ConflictAlgorithm.ignore);
    });
  }

  static bool _isHtml(String body) => RegExp(
    r'<\s*(?:!doctype|/?[a-z][\w:-]*(?:\s|/?>))',
    caseSensitive: false,
  ).hasMatch(body);
  static String visibleBody(String body) =>
      _isHtml(body) ? html.parse(body).body?.text ?? '' : body;
  Future<NoteRecord> _record(Map<String, Object?> row) async {
    final id = row['id'] as int,
        tags = await db.query(
          'note_tags',
          where: 'note_id=?',
          whereArgs: [id],
          orderBy: 'tag',
        );
    return NoteRecord(
      id: id,
      uuid: row['entity_uuid'] as String,
      title: row['title'] as String,
      body: row['body'] as String,
      tags: tags.map((r) => r['tag'] as String).toList(),
      readOnlyHtml: _isHtml(row['body'] as String),
    );
  }

  static const _select =
      "SELECT n.*,i.entity_uuid FROM notes n JOIN sync_identities i ON i.entity='notes' AND i.local_id=n.id AND i.tombstone=0 WHERE n.workspace_id=1 AND n.deleted=0 AND n.archived=0";
  Future<List<NoteRecord>> listNotes({String query = '', String? tag}) async {
    final rows = await db.rawQuery(
      '$_select ORDER BY n.updated_at DESC,n.id DESC',
    );
    final result = <NoteRecord>[];
    for (final row in rows) {
      final note = await _record(row);
      if (tag != null && !note.tags.contains(tag)) continue;
      final hay = '${note.title}\n${visibleBody(note.body)}'.toLowerCase();
      if (!hay.contains(query.toLowerCase())) continue;
      result.add(note);
    }
    return result;
  }

  Future<NoteRecord?> getNote(int id) async {
    final rows = await db.rawQuery('$_select AND n.id=?', [id]);
    return rows.isEmpty ? null : _record(rows.single);
  }

  Future<List<String>> allTags() async => (await db.rawQuery(
    'SELECT DISTINCT t.tag FROM note_tags t JOIN notes n ON n.id=t.note_id WHERE n.deleted=0 AND n.archived=0 AND n.workspace_id=1 ORDER BY t.tag',
  )).map((r) => r['tag'] as String).toList();
  Future<void> _index(DatabaseExecutor tx) async {
    final notes = await tx.query(
      'notes',
      where: 'workspace_id=1 AND deleted=0 AND archived=0',
    );
    await tx.delete('note_links');
    final titles = <String, List<int>>{};
    for (final row in notes) {
      titles
          .putIfAbsent((row['title'] as String).trim().toLowerCase(), () => [])
          .add(row['id'] as int);
    }
    final pattern = RegExp(r'\[\[([^\[\]\n]{1,1024})\]\]');
    for (final row in notes) {
      final text = visibleBody(row['body'] as String);
      for (final match in pattern.allMatches(text)) {
        final title = match[1]!.split('|').first.trim(),
            targets = titles[title.toLowerCase()] ?? [];
        await tx.insert('note_links', {
          'source_id': row['id'],
          'target_id': targets.length == 1 ? targets.single : null,
          'link_text': match[1],
          'context_snippet': text.substring(
            match.start > 60 ? match.start - 60 : 0,
            match.end + 60 < text.length ? match.end + 60 : text.length,
          ),
        });
      }
    }
  }

  Future<List<NoteRecord>> _relations(int id, bool incoming) async {
    final rows = await db.rawQuery(
      'SELECT DISTINCT ${incoming ? 'source_id' : 'target_id'} AS id FROM note_links WHERE ${incoming ? 'target_id' : 'source_id'}=? AND target_id IS NOT NULL',
      [id],
    );
    final result = <NoteRecord>[];
    for (final row in rows) {
      final note = await getNote(row['id'] as int);
      if (note != null) result.add(note);
    }
    return result;
  }

  Future<List<NoteRecord>> backlinks(int id) => _relations(id, true);
  Future<List<NoteRecord>> linkedNotes(int id) => _relations(id, false);
  Future<List<NoteRecord>> unlinkedMentions(int id) async {
    final target = await getNote(id);
    if (target == null || target.title.trim().isEmpty) return [];
    final linked = (await backlinks(id)).map((n) => n.id).toSet();
    return (await listNotes())
        .where(
          (n) =>
              n.id != id &&
              !linked.contains(n.id) &&
              visibleBody(n.body)
                  .toLowerCase()
                  .contains(target.title.toLowerCase()),
        )
        .toList();
  }

  Future<List<Map<String, Object?>>> graph() => db.rawQuery(
    'SELECT DISTINCT source_id,target_id FROM note_links WHERE target_id IS NOT NULL',
  );
  Future<List<Map<String, Object>>> bodyConflicts(int id) async =>
      (await _replay(db)).bodyConflicts(await _entity(db, id));
  Future<void> close() => db.close();
}

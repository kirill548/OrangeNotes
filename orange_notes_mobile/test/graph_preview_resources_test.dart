import 'package:flutter_test/flutter_test.dart';
import 'package:orange_notes_mobile/data/mobile_store.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';

void main() {
  sqfliteFfiInit();
  test(
    'graph projection excludes bodies and opens full notes only on demand',
    () async {
      final store = await MobileStore.open(
        databaseFactory: databaseFactoryFfi,
        path: inMemoryDatabasePath,
      );
      try {
        final target = await store.saveNote(
          title: 'Target',
          body: 'important body',
        );
        await store.saveNote(title: 'Source', body: '[[Target]]');
        final (nodes, edges, total) = await store.graphPreview();
        expect(total, 2);
        expect(nodes.every((n) => n.body.isEmpty && n.tags.isEmpty), isTrue);
        expect(edges, hasLength(1));
        expect((await store.getNote(target.id))!.body, 'important body');
        final (limited, limitedEdges, count) = await store.graphPreview(
          limit: 1,
        );
        expect(limited, hasLength(1));
        expect(limitedEdges, isEmpty);
        expect(count, 2);
        await store.deleteNote(target.id);
        final (_, afterDelete, afterCount) = await store.graphPreview();
        expect(afterCount, 1);
        expect(afterDelete, isEmpty);
        await expectLater(store.graphPreview(limit: 301), throwsArgumentError);
        await expectLater(
          store.graphPreview(edgeLimit: 3001),
          throwsArgumentError,
        );
      } finally {
        await store.close();
      }
    },
  );
}

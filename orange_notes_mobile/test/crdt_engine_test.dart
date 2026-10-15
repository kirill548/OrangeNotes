import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:orange_notes_mobile/services/crdt_engine.dart';

void main() {
  final fixture = jsonDecode(
    File('test/fixtures/protocol_v1.json').readAsStringSync(),
  ) as Map;
  for (final c in fixture['cases'] as List) {
    test('desktop interoperability ${c['name']}', () {
      final replica = Replica(
        fixture['workspace_id'] as String,
        (fixture['actors'] as Map)['receiver'] as String,
      );
      final wires = c['wires'] as List, accepted = c['accepted'] as List;
      for (var i = 0; i < wires.length; i++) {
        if (accepted[i] == 'error') {
          expect(
            () => replica.applyDelta(wires[i] as String),
            throwsA(isA<DeltaError>()),
          );
        } else {
          expect(replica.applyDelta(wires[i] as String), accepted[i]);
        }
      }
      expect(replica.get(fixture['entity_id'] as String), c['expected']);
      expect(
        replica.bodyConflicts(fixture['entity_id'] as String),
        c['body_conflicts'],
      );
    });
  }
  test('reject duplicate escaped keys and nonfinite JSON', () {
    final r = Replica(
      fixture['workspace_id'] as String,
      (fixture['actors'] as Map)['receiver'] as String,
    );
    expect(
      () => r.applyDelta(r'{"version":1,"vers\u0069on":1}'),
      throwsA(isA<DeltaError>()),
    );
    expect(() => r.applyDelta('{"clock":NaN}'), throwsA(isA<DeltaError>()));
  });
  test('Unicode byte limits reject atomically', () {
    final r = Replica(
      fixture['workspace_id'] as String,
      (fixture['actors'] as Map)['receiver'] as String,
    );
    expect(
      () => r.makeDelta(fixture['entity_id'] as String, {
        'title': List.filled(400, '🐝').join(),
      }),
      throwsA(isA<DeltaError>()),
    );
    expect(r.clock, 0);
    expect(r.get(fixture['entity_id'] as String), isNull);
  });
}

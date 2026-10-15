import 'dart:convert';
import 'dart:io';

import 'package:flutter_test/flutter_test.dart';
import 'package:orange_notes_mobile/services/crdt_engine.dart';
import 'package:orange_notes_mobile/services/p2p_transport.dart';

void main() {
  final f = jsonDecode(
    File('test/fixtures/PUBLIC_TEST_TLS.json').readAsStringSync(),
  ) as Map;
  const w = '10000000-0000-4000-8000-000000000001',
      a = '20000000-0000-4000-8000-000000000001',
      b = '20000000-0000-4000-8000-000000000002',
      e = '30000000-0000-4000-8000-000000000001';
  SecurityContext context(String who) => createTlsContext(
    caPem: utf8.encode(f['ca'] as String),
    certPem: utf8.encode(f[who]['cert'] as String),
    keyPem: utf8.encode(f[who]['key'] as String),
  );
  PeerIdentity peer(String who, String actor) =>
      PeerIdentity(actor, w, f[who]['pin'] as String);
  String delta() =>
      Replica(w, a).makeDelta(e, {'title': 'Привет 🐝', 'body': '[[План]]'});
  Future<Tls13Server> server(
    DeltaCommit callback, {
    Duration timeout = const Duration(seconds: 2),
  }) async {
    final s = Tls13Server(
      context('b'),
      [peer('a', a)],
      callback,
      localActorId: b,
      timeout: timeout,
    );
    await s.start();
    addTearDown(s.close);
    return s;
  }

  test(
    'real mTLS commits before ACK and retransmission is idempotent',
    () async {
      final r = Replica(w, b);
      var commits = 0;
      final s = await server((a, w, d) async {
        await Future<void>.delayed(const Duration(milliseconds: 20));
        if (r.applyDelta(d)) commits++;
      });
      final c = Tls13Client(context('a'), a, w, peer('b', b));
      final d = delta();
      final ack = await c.sendDelta('127.0.0.1', s.port, d);
      expect(ack['status'], 'committed');
      expect(commits, 1);
      expect(r.get(e)!['title'], 'Привет 🐝');
      await c.sendDelta('127.0.0.1', s.port, d);
      expect(commits, 1);
    },
  );
  test('wrong pin sends no delta', () async {
    var writes = 0;
    final s = await server((a, w, d) async {
      writes++;
    });
    final c = Tls13Client(context('a'), a, w, peer('a', b));
    await expectLater(
      c.sendDelta('127.0.0.1', s.port, delta()),
      throwsA(isA<TransportError>()),
    );
    expect(writes, 0);
  });
  test('valid CA unpaired client rejected', () async {
    var writes = 0;
    final s = await server((a, w, d) async {
      writes++;
    });
    final c = Tls13Client(context('unpaired'), a, w, peer('b', b));
    await expectLater(
      c.sendDelta('127.0.0.1', s.port, delta()),
      throwsA(anything),
    );
    expect(writes, 0);
  });
  test('hostname verification enforced', () async {
    var writes = 0;
    final s = await server((a, w, d) async {
      writes++;
    });
    final c = Tls13Client(context('a'), a, w, peer('b', b));
    await expectLater(
      c.sendDelta(
        '127.0.0.1',
        s.port,
        delta(),
        serverHostname: 'wrong.invalid',
      ),
      throwsA(anything),
    );
    expect(writes, 0);
  });
  test('failed commit has no ACK', () async {
    final s = await server((a, w, d) async {
      throw StateError('rollback');
    });
    final c = Tls13Client(context('a'), a, w, peer('b', b));
    await expectLater(
      c.sendDelta('127.0.0.1', s.port, delta()),
      throwsA(anything),
    );
  });
  test('deadline rejects stalled commit', () async {
    final s = await server(
      (a, w, d) => Future<void>.delayed(const Duration(milliseconds: 300)),
      timeout: const Duration(milliseconds: 60),
    );
    final c = Tls13Client(
      context('a'),
      a,
      w,
      peer('b', b),
      timeout: const Duration(milliseconds: 120),
    );
    await expectLater(
      c.sendDelta('127.0.0.1', s.port, delta()),
      throwsA(anything),
    );
  });
  test('revoked peer gets no ACK', () async {
    late Tls13Server s;
    s = await server((a, w, d) async {
      s.peers.clear();
    });
    final c = Tls13Client(context('a'), a, w, peer('b', b));
    await expectLater(
      c.sendDelta('127.0.0.1', s.port, delta()),
      throwsA(anything),
    );
  });
  test('insecure contexts and excessive deadline rejected', () {
    expect(
      () => Tls13Client(SecurityContext(), a, w, peer('b', b)),
      throwsA(isA<TransportError>()),
    );
    expect(
      () => Tls13Client(
        context('a'),
        a,
        w,
        peer('b', b),
        timeout: const Duration(seconds: 31),
      ),
      throwsA(isA<TransportError>()),
    );
  });
}

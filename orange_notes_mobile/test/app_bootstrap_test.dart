import 'dart:async';

import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';
import 'package:orange_notes_mobile/main.dart';
import 'package:orange_notes_mobile/data/mobile_store.dart';
import 'package:orange_notes_mobile/services/mobile_sync.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  sqfliteFfiInit();
  testWidgets(
    'Riverpod startup error is recoverable without unhandled future',
    (tester) async {
      await tester.pumpWidget(
        ProviderScope(
          overrides: [
            mobileSessionProvider.overrideWith((ref) async {
              throw StateError('Disk unavailable');
            }),
          ],
          child: const OrangeNotesApp(),
        ),
      );
      await tester.pumpAndSettle();
      expect(
        find.text('Не удалось открыть заметки. Перезапустите приложение.'),
        findsOneWidget,
      );
      expect(tester.takeException(), isNull);
    },
  );
  test('Session owner closes sync before SQLite exactly once', () async {
    FlutterSecureStorage.setMockInitialValues({});
    final store = await MobileStore.open(
      databaseFactory: databaseFactoryFfi,
      path: inMemoryDatabasePath,
    );
    final sync = MobileSync(store);
    await sync.initialize();
    final session = MobileSession(store, sync);
    final closed = Completer<void>();
    final container = ProviderContainer(
      overrides: [
        mobileSessionProvider.overrideWith((ref) async {
          ref.onDispose(() {
            session.close().then((_) => closed.complete());
          });
          return session;
        }),
      ],
    );
    final subscription = container.listen(
      mobileSessionProvider,
      (previous, next) {},
    );
    expect(await container.read(mobileSessionProvider.future), same(session));
    container.dispose();
    await closed.future.timeout(const Duration(seconds: 5));
    expect(store.db.isOpen, isFalse);
    await session.close();
    subscription.close();
  });
}

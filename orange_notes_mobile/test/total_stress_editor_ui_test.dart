import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';
import 'package:orange_notes_mobile/data/mobile_store.dart';
import 'package:orange_notes_mobile/presentation/mobile_home.dart';
import 'package:orange_notes_mobile/presentation/note_editor.dart';
import 'package:orange_notes_mobile/presentation/pairing_screen.dart';
import 'package:orange_notes_mobile/presentation/graph_screen.dart';

class _Pairing extends PairingController {
  @override
  String get status => 'Нет соединения. Заметки доступны без сети.';
  @override
  List<TrustedDevice> get devices => const [];
  @override
  Future<void> pairQr(String raw, {required bool confirmed}) async {}
  @override
  Future<void> revoke(String actorId) async {}
  @override
  Future<void> syncNow(String actorId) async {}
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  late MobileStore store;
  late _Pairing pairing;
  setUp(() async {
    sqfliteFfiInit();
    store = await MobileStore.open(
      databaseFactory: databaseFactoryFfi,
      path: inMemoryDatabasePath,
    );
    pairing = _Pairing();
  });
  tearDown(() async {
    pairing.dispose();
    await store.close();
  });

  Future<void> settle(WidgetTester tester) async {
    await tester.runAsync(
      () => Future<void>.delayed(const Duration(milliseconds: 120)),
    );
    await tester.pumpAndSettle();
  }

  for (final size in [const Size(320, 568), const Size(640, 320)]) {
    for (final scale in [1.0, 2.0]) {
      testWidgets('Editor keyboard and rotation $size scale $scale', (
        tester,
      ) async {
        tester.view.physicalSize = size;
        tester.view.devicePixelRatio = 1;
        addTearDown(tester.view.resetPhysicalSize);
        addTearDown(tester.view.resetDevicePixelRatio);
        await tester.pumpWidget(
          MaterialApp(
            builder: (context, child) => MediaQuery(
              data: MediaQuery.of(context).copyWith(
                textScaler: TextScaler.linear(scale),
                padding: const EdgeInsets.only(left: 28, bottom: 24),
                viewInsets: EdgeInsets.only(bottom: size.height / 3),
              ),
              child: child!,
            ),
            home: NoteEditor(store: store),
          ),
        );
        await tester.enterText(find.byType(TextField).first, 'План 👩🏽‍💻');
        await tester.pump(const Duration(milliseconds: 600));
        await settle(tester);
        expect(tester.takeException(), isNull);
        await tester.ensureVisible(find.byTooltip('Сохранить'));
        await tester.tap(find.byTooltip('Сохранить'));
        await settle(tester);
        final notes = await tester.runAsync(() => store.listNotes());
        expect(notes!.single.title, 'План 👩🏽‍💻');
        await tester.pumpWidget(const SizedBox.shrink());
      });
    }
  }

  for (final screen in ['home', 'pairing', 'graph']) {
    testWidgets('$screen respects landscape notch safe area', (tester) async {
      tester.view.physicalSize = const Size(640, 320);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      final child = switch (screen) {
        'home' => MobileHome(store: store, pairing: pairing),
        'pairing' => PairingScreen(controller: pairing),
        _ => GraphScreen(store: store),
      };
      await tester.pumpWidget(
        MaterialApp(
          builder: (context, child) => MediaQuery(
            data: MediaQuery.of(context).copyWith(
              padding: const EdgeInsets.only(left: 44, right: 44, bottom: 24),
            ),
            child: child!,
          ),
          home: child,
        ),
      );
      await settle(tester);
      final body = tester.widget<Scaffold>(find.byType(Scaffold)).body!;
      expect(body, isA<SafeArea>());
      final safeChild = find.byWidget((body as SafeArea).child);
      expect(tester.getTopLeft(safeChild).dx, greaterThanOrEqualTo(44));
      expect(tester.getBottomRight(safeChild).dx, lessThanOrEqualTo(596));
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox.shrink());
    });
  }
}

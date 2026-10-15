import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:sqflite_common_ffi/sqflite_ffi.dart';
import 'package:orange_notes_mobile/data/mobile_store.dart';
import 'package:orange_notes_mobile/presentation/mobile_home.dart';
import 'package:orange_notes_mobile/presentation/note_editor.dart';
import 'package:orange_notes_mobile/presentation/pairing_screen.dart';
import 'package:orange_notes_mobile/presentation/graph_screen.dart';

class TestPairing extends PairingController {
  @override
  String get status => 'Не подключено';
  @override
  List<TrustedDevice> get devices => [];
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
  setUp(() async {
    sqfliteFfiInit();
    store = await MobileStore.open(
      databaseFactory: databaseFactoryFfi,
      path: inMemoryDatabasePath,
    );
  });
  tearDown(() async {
    await store.db.close();
  });
  Future<void> settle(WidgetTester tester) async {
    // SQLite FFI runs outside the fake clock used by widget tests.
    await tester.runAsync(() async {
      await Future<void>.delayed(const Duration(milliseconds: 100));
    });
    await tester.pumpAndSettle();
  }

  testWidgets('List searches local notes and filters tags', (tester) async {
    await tester.runAsync(() async {
      await store.saveNote(
        title: 'Рабочий отчёт',
        body: 'План',
        tags: ['Работа'],
      );
      await store.saveNote(title: 'Покупки', body: 'Молоко', tags: ['Дом']);
    });
    await tester.pumpWidget(
      MaterialApp(
        home: MobileHome(store: store, pairing: TestPairing()),
      ),
    );
    await settle(tester);
    expect(find.text('Рабочий отчёт'), findsOneWidget);
    await tester.tap(find.text('Дом'));
    await settle(tester);
    expect(find.text('Рабочий отчёт'), findsNothing);
    expect(find.text('Покупки'), findsOneWidget);
    await tester.tap(find.text('Все'));
    await settle(tester);
    await tester.enterText(find.byType(TextField).first, 'отчёт');
    await settle(tester);
    expect(find.text('Рабочий отчёт'), findsOneWidget);
    expect(find.text('Покупки'), findsNothing);
  });

  testWidgets('Editor saves offline and offers wiki autocomplete', (
    tester,
  ) async {
    await tester.runAsync(
      () => store.saveNote(title: 'Проект', body: 'Описание'),
    );
    await tester.pumpWidget(MaterialApp(home: NoteEditor(store: store)));
    await tester.enterText(find.byType(TextField).at(0), 'План');
    await tester.enterText(find.byType(TextField).at(1), 'Ссылка [[Про');
    await settle(tester);
    expect(find.text('Проект'), findsOneWidget);
    await tester.ensureVisible(find.text('Проект'));
    await tester.tap(find.text('Проект'));
    await tester.pump(const Duration(milliseconds: 600));
    await settle(tester);
    final notes = (await tester.runAsync(
      () => store.listNotes(query: 'План'),
    ))!;
    expect(notes.single.body, 'Ссылка [[Проект]]');
    await tester.runAsync(() async {
      expect(
        (await store.backlinks(
          (await store.listNotes(query: 'Проект'))
              .singleWhere((n) => n.title == 'Проект')
              .id,
        )).single.title,
        'План',
      );
    });
    await tester.pumpWidget(const SizedBox.shrink());
  });

  testWidgets('Desktop HTML is readonly and never overwritten by editor', (
    tester,
  ) async {
    final note = (await tester.runAsync(
      () => store.saveNote(
        title: 'HTML',
        body: '<p><strong>Сохранить оформление</strong></p>',
      ),
    ))!;
    await tester.pumpWidget(
      MaterialApp(
        home: NoteEditor(store: store, note: note),
      ),
    );
    expect(find.textContaining('редактирование отключено'), findsOneWidget);
    expect(
      tester.widget<TextField>(find.byType(TextField).at(0)).readOnly,
      isTrue,
    );
    expect(
      tester.widget<TextField>(find.byType(TextField).at(1)).readOnly,
      isTrue,
    );
    expect(find.byTooltip('Сохранить'), findsNothing);
    await tester.runAsync(() async {
      expect((await store.getNote(note.id))!.body, note.body);
    });
  });

  testWidgets('Graph handles empty workspace and exposes touch nodes', (
    tester,
  ) async {
    await tester.pumpWidget(MaterialApp(home: GraphScreen(store: store)));
    await settle(tester);
    expect(find.textContaining('Создайте заметки'), findsOneWidget);
    await tester.pumpWidget(const SizedBox.shrink());
    await tester.runAsync(() => store.saveNote(title: 'Узел', body: 'Текст'));
    await tester.pumpWidget(MaterialApp(home: GraphScreen(store: store)));
    await settle(tester);
    expect(find.byType(InteractiveViewer), findsOneWidget);
    expect(find.text('Узел'), findsOneWidget);
    await tester.tap(find.text('Узел'));
    await settle(tester);
    expect(find.text('Заметка'), findsOneWidget);
  });

  testWidgets('Back saves a fresh draft before leaving editor', (tester) async {
    await tester.pumpWidget(
      MaterialApp(
        home: MobileHome(store: store, pairing: TestPairing()),
      ),
    );
    await settle(tester);
    await tester.tap(find.text('Новая заметка'));
    await tester.pumpAndSettle();
    await tester.enterText(find.byType(TextField).at(0), 'Новая офлайн');
    await tester.enterText(find.byType(TextField).at(1), 'Не потерять текст');
    await tester.pageBack();
    await settle(tester);
    await settle(tester);
    expect(find.text('Orange Notes'), findsOneWidget);
    await tester.runAsync(() async {
      final notes = await store.listNotes();
      expect(notes, hasLength(1));
      expect(notes.single.body, 'Не потерять текст');
    });
  });

  testWidgets('Delete confirms and returns to remaining notes', (tester) async {
    await tester.runAsync(() async {
      await store.saveNote(title: 'Удаляемая', body: 'Текст');
      await store.saveNote(title: 'Оставшаяся', body: 'Текст');
    });
    await tester.pumpWidget(
      MaterialApp(
        home: MobileHome(store: store, pairing: TestPairing()),
      ),
    );
    await settle(tester);
    await tester.tap(find.text('Удаляемая'));
    await settle(tester);
    await tester.tap(find.byTooltip('Удалить заметку'));
    await tester.pumpAndSettle();
    expect(find.text('Удалить заметку?'), findsOneWidget);
    await tester.tap(find.widgetWithText(FilledButton, 'Удалить'));
    await settle(tester);
    await settle(tester);
    expect(find.text('Orange Notes'), findsOneWidget);
    expect(find.text('Оставшаяся'), findsOneWidget);
    expect(find.text('Удаляемая'), findsNothing);
  });

  testWidgets('Unavailable system speech shows helpful fallback', (
    tester,
  ) async {
    const channel = MethodChannel('plugin.csdcorp.com/speech_to_text');
    tester.binding.defaultBinaryMessenger.setMockMethodCallHandler(
      channel,
      (call) async => call.method == 'initialize' ? false : null,
    );
    addTearDown(
      () => tester.binding.defaultBinaryMessenger.setMockMethodCallHandler(
        channel,
        null,
      ),
    );
    await tester.pumpWidget(MaterialApp(home: NoteEditor(store: store)));
    await tester.ensureVisible(find.text('Надиктовать'));
    await tester.tap(find.text('Надиктовать'));
    await tester.pumpAndSettle();
    expect(find.textContaining('Голосовой ввод недоступен.'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });
}

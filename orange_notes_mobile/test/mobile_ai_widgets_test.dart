import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:orange_notes_mobile/presentation/ai_chat_screen.dart';
import 'package:orange_notes_mobile/services/ai_gateway.dart';

class FakeGateway implements AiGateway {
  final controller = StreamController<AiEvent>.broadcast();
  List<AiContext> contexts = [];
  String? prompt;
  int cancelled = 0;
  @override
  Stream<AiEvent> ask(String prompt, {List<AiContext> contexts = const []}) {
    this.prompt = prompt;
    this.contexts = contexts;
    return controller.stream;
  }

  @override
  void cancel() => cancelled++;
}

void main() {
  late FakeGateway gateway;
  setUp(() => gateway = FakeGateway());
  tearDown(() => gateway.controller.close());
  const note = AiContext(
    noteId: 'note',
    title: 'Встреча',
    body: 'Частный текст',
  );
  Future<void> open(WidgetTester tester, {AiContext? context}) async {
    await tester.pumpWidget(
      MaterialApp(
        home: AiChatScreen(gateway: gateway, noteContext: context),
      ),
    );
  }

  Future<void> send(WidgetTester tester) async {
    await tester.enterText(find.byType(TextField), 'Какой план?');
    await tester.tap(find.byTooltip('Отправить'));
    await tester.pump();
  }

  testWidgets('no context or request is sent merely by opening chat', (
    tester,
  ) async {
    await open(tester);
    expect(gateway.prompt, isNull);
    await send(tester);
    expect(gateway.contexts, isEmpty);
    expect(gateway.prompt, 'Какой план?');
  });
  testWidgets('explicit note attachment can be removed before sending', (
    tester,
  ) async {
    await open(tester, context: note);
    expect(gateway.prompt, isNull);
    expect(find.text('Встреча'), findsOneWidget);
    await tester.tap(find.byTooltip('Убрать заметку из запроса'));
    await send(tester);
    expect(gateway.contexts, isEmpty);
  });
  testWidgets('attached snapshot is delivered and tokens streamed', (
    tester,
  ) async {
    await open(tester, context: note);
    await send(tester);
    expect(gateway.contexts.single.body, 'Частный текст');
    gateway.controller.add(
      const AiEvent(AiEventKind.status, text: 'generating'),
    );
    gateway.controller.add(const AiEvent(AiEventKind.token, text: 'План '));
    gateway.controller.add(const AiEvent(AiEventKind.token, text: 'встречи'));
    await tester.pump();
    expect(find.text('План встречи'), findsOneWidget);
    expect(find.text('Подключён · Готовлю ответ…'), findsOneWidget);
    gateway.controller.add(const AiEvent(AiEventKind.done));
    await tester.pump();
    expect(find.byTooltip('Отправить'), findsOneWidget);
  });
  testWidgets(
    'fallback clearly states local search and hides technical status',
    (tester) async {
      await open(tester);
      await send(tester);
      gateway.controller.add(
        const AiEvent(AiEventKind.status, text: 'local_search'),
      );
      gateway.controller.add(
        const AiEvent(AiEventKind.token, text: 'Найденная заметка'),
      );
      gateway.controller.add(const AiEvent(AiEventKind.done));
      await tester.pump();
      expect(
        find.text('Недоступен · Локальный поиск на телефоне'),
        findsOneWidget,
      );
      expect(find.text('local_search'), findsNothing);
      expect(find.text('Результаты поиска · на телефоне'), findsOneWidget);
    },
  );
  testWidgets('stop cancels request and late tokens cannot change response', (
    tester,
  ) async {
    await open(tester);
    await send(tester);
    await tester.tap(find.byTooltip('Остановить ответ'));
    await tester.pump();
    expect(gateway.cancelled, 1);
    gateway.controller.add(
      const AiEvent(AiEventKind.token, text: 'Поздний ответ'),
    );
    await tester.pump();
    expect(find.text('Поздний ответ'), findsNothing);
    expect(find.text('Остановлено'), findsOneWidget);
  });
  testWidgets('protocol exceptions never appear in chat', (tester) async {
    await open(tester);
    await send(tester);
    gateway.controller.addError(StateError('secret certificate debug payload'));
    await tester.pump();
    expect(find.textContaining('secret certificate'), findsNothing);
    expect(
      find.text('Не удалось получить ответ. Попробуйте ещё раз.'),
      findsOneWidget,
    );
    expect(find.byTooltip('Отправить'), findsOneWidget);
  });
  testWidgets('narrow screen and large text retain input and send', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(640, 1100);
    tester.view.devicePixelRatio = 2;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(
      MaterialApp(
        builder: (context, child) => MediaQuery(
          data: MediaQuery.of(context)
              .copyWith(textScaler: const TextScaler.linear(2)),
          child: child!,
        ),
        home: AiChatScreen(gateway: gateway, noteContext: note),
      ),
    );
    expect(tester.takeException(), isNull);
    expect(find.byTooltip('Отправить'), findsOneWidget);
    await send(tester);
    expect(tester.takeException(), isNull);
  });
}

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:orange_notes_mobile/presentation/ai_chat_screen.dart';
import 'package:orange_notes_mobile/services/ai_gateway.dart';

class IdleGateway implements AiGateway {
  @override
  void cancel() {}
  @override
  Stream<AiEvent> ask(String prompt, {List<AiContext> contexts = const []}) =>
      const Stream.empty();
}

void main() {
  testWidgets('long lived chat keeps only last 100 messages', (tester) async {
    await tester.pumpWidget(
      MaterialApp(home: AiChatScreen(gateway: IdleGateway())),
    );
    for (var i = 0; i < 55; i++) {
      await tester.enterText(find.byType(TextField), 'Запрос $i');
      await tester.tap(find.byTooltip('Отправить'));
      await tester.pump();
    }
    expect(
      find.text('Показаны последние 100 сообщений. Заметки не изменены.'),
      findsOneWidget,
    );
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
  });
  for (final scenario in [
    (const Size(320, 568), 0.0, 1.0),
    (const Size(320, 568), 240.0, 2.0),
    (const Size(640, 320), 120.0, 2.0),
    (const Size(280, 400), 140.0, 2.0),
    (const Size(1024, 768), 0.0, 1.0),
  ]) {
    testWidgets('chat viewport $scenario', (tester) async {
      tester.view.physicalSize = scenario.$1;
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      await tester.pumpWidget(
        MaterialApp(
          builder: (context, child) => MediaQuery(
            data: MediaQuery.of(context).copyWith(
              viewInsets: EdgeInsets.only(bottom: scenario.$2),
              padding: const EdgeInsets.only(top: 24, bottom: 20),
              textScaler: TextScaler.linear(scenario.$3),
            ),
            child: child!,
          ),
          home: AiChatScreen(
            gateway: IdleGateway(),
            noteContext: const AiContext(
              noteId: 'test',
              title: 'Очень длинный заголовок заметки для проверки переноса и доступности',
              body: 'Текст',
            ),
          ),
        ),
      );
      expect(tester.takeException(), isNull);
      await tester.enterText(find.byType(TextField), 'Проверка');
      await tester.pump();
      expect(tester.takeException(), isNull);
      expect(find.byTooltip('Отправить').hitTestable(), findsOneWidget);
      for (var i = 0; i < 6; i++) {
        tester.view.physicalSize = i.isEven
            ? Size(scenario.$1.height, scenario.$1.width)
            : scenario.$1;
        await tester.pump();
        expect(tester.takeException(), isNull);
      }
      await tester.pumpWidget(const SizedBox());
    });
  }
}

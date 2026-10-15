import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'data/mobile_store.dart';
import 'presentation/mobile_home.dart';
import 'services/mobile_sync.dart';

class MobileSession {
  MobileSession(this.store, this.sync);
  final MobileStore store;
  final MobileSync sync;
  Future<void>? _closing;
  Future<void> close() => _closing ??= _close();
  Future<void> _close() async {
    try {
      await sync.shutdown();
    } finally {
      sync.dispose();
      await store.close();
    }
  }
}

// One owner ensures shutdown completes before SQLite is closed.
final mobileSessionProvider = FutureProvider.autoDispose<MobileSession>((
  ref,
) async {
  final store = await MobileStore.open();
  final session = MobileSession(store, MobileSync(store));
  if (!ref.mounted) {
    await session.close();
    throw StateError('Startup canceled');
  }
  ref.onDispose(() {
    session.close().catchError((Object _) {
      debugPrint('Orange Notes: session cleanup failed.');
    });
  });
  await session.sync.initialize();
  return session;
});

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  runApp(const ProviderScope(child: OrangeNotesApp()));
}

class OrangeNotesApp extends ConsumerWidget {
  const OrangeNotesApp({super.key});
  @override
  Widget build(BuildContext context, WidgetRef ref) => MaterialApp(
    title: 'Orange Notes',
    debugShowCheckedModeBanner: false,
    theme: ThemeData(
      colorScheme: ColorScheme.fromSeed(seedColor: Colors.orange),
      useMaterial3: true,
    ),
    home: ref
        .watch(mobileSessionProvider)
        .when(
          data: (session) =>
              MobileHome(store: session.store, pairing: session.sync),
          loading: () =>
              const Scaffold(body: Center(child: CircularProgressIndicator())),
          error: (error, stack) => const Scaffold(
            body: Center(
              child: Text(
                'Не удалось открыть заметки. Перезапустите приложение.',
              ),
            ),
          ),
        ),
  );
}

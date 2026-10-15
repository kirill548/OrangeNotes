import 'package:flutter/material.dart';
import 'package:flutter_riverpod/flutter_riverpod.dart';

import 'data/mobile_store.dart';
import 'presentation/mobile_home.dart';
import 'services/mobile_sync.dart';

void main() {
  WidgetsFlutterBinding.ensureInitialized();
  runApp(const ProviderScope(child: OrangeNotesApp()));
}

class OrangeNotesApp extends StatefulWidget {
  const OrangeNotesApp({super.key});
  @override
  State<OrangeNotesApp> createState() => _AppState();
}

class _AppState extends State<OrangeNotesApp> {
  late final Future<MobileStore> _store = MobileStore.open();
  MobileSync? _sync;
  @override
  void dispose() {
    _closeStorage();
    super.dispose();
  }

  Future<void> _closeStorage() async {
    try {
      final store = await _store;
      await _sync?.shutdown();
      _sync?.dispose();
      await store.close();
    } catch (_) {
      /* Opening failure is already displayed by FutureBuilder. */
    }
  }

  @override
  Widget build(BuildContext context) => MaterialApp(
    title: 'Orange Notes',
    debugShowCheckedModeBanner: false,
    theme: ThemeData(
      colorScheme: ColorScheme.fromSeed(seedColor: Colors.orange),
      useMaterial3: true,
    ),
    home: FutureBuilder<MobileStore>(
      future: _store,
      builder: (context, snapshot) {
        if (snapshot.hasError) {
          return const Scaffold(
            body: Center(
              child: Text(
                'Не удалось открыть заметки. Перезапустите приложение.',
              ),
            ),
          );
        }
        if (!snapshot.hasData) {
          return const Scaffold(
            body: Center(child: CircularProgressIndicator()),
          );
        }
        _sync ??= MobileSync(snapshot.data!)..initialize();
        return MobileHome(store: snapshot.data!, pairing: _sync!);
      },
    ),
  );
}

import 'package:flutter/material.dart';

import '../data/mobile_store.dart';
import '../domain/note.dart';
import 'note_editor.dart';
import 'ai_chat_screen.dart';
import '../services/ai_gateway.dart';
import 'graph_screen.dart';
import 'pairing_screen.dart';

class MobileHome extends StatefulWidget {
  const MobileHome({
    super.key,
    required this.store,
    required this.pairing,
    this.aiGateway,
  });
  final AiGateway? aiGateway;
  final MobileStore store;
  final PairingController pairing;
  @override
  State<MobileHome> createState() => _MobileHomeState();
}

class _MobileHomeState extends State<MobileHome> with WidgetsBindingObserver {
  final _search = TextEditingController();
  String? _tag;
  late Future<List<NoteRecord>> _notes;
  late Future<List<String>> _tags;
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    widget.pairing.addListener(_refresh);
    _reload();
  }

  void _refresh() {
    if (mounted) setState(_reload);
  }

  @override
  void didUpdateWidget(covariant MobileHome oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.pairing != widget.pairing) {
      oldWidget.pairing.removeListener(_refresh);
      widget.pairing.addListener(_refresh);
    }
    if (oldWidget.store != widget.store) _reload();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed) _refresh();
  }

  void _reload() {
    _notes = widget.store.listNotes(query: _search.text, tag: _tag);
    _tags = widget.store.allTags();
  }

  @override
  void dispose() {
    widget.pairing.removeListener(_refresh);
    WidgetsBinding.instance.removeObserver(this);
    _search.dispose();
    super.dispose();
  }

  Future<void> _open([NoteRecord? note]) async {
    await Navigator.push(
      context,
      MaterialPageRoute<void>(
        builder: (_) => NoteEditor(
          store: widget.store,
          note: note,
          aiGateway: widget.aiGateway,
        ),
      ),
    );
    if (mounted) setState(_reload);
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(
      title: const Text('Orange Notes'),
      actions: [
        if (widget.aiGateway != null)
          IconButton(
            tooltip: 'Помощник',
            icon: const Icon(Icons.chat_bubble_outline),
            onPressed: () => Navigator.push(
              context,
              MaterialPageRoute<void>(
                builder: (_) => AiChatScreen(gateway: widget.aiGateway!),
              ),
            ),
          ),
        IconButton(
          tooltip: 'Граф заметок',
          icon: const Icon(Icons.hub_outlined),
          onPressed: () async {
            await Navigator.push(
              context,
              MaterialPageRoute<void>(
                builder: (_) => GraphScreen(store: widget.store),
              ),
            );
            if (mounted) setState(_reload);
          },
        ),
        IconButton(
          tooltip: 'Синхронизация',
          icon: const Icon(Icons.devices),
          onPressed: () async {
            await Navigator.push(
              context,
              MaterialPageRoute<void>(
                builder: (_) => PairingScreen(controller: widget.pairing),
              ),
            );
            if (mounted) setState(_reload);
          },
        ),
      ],
    ),
    body: SafeArea(
      child: Column(
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 8, 16, 8),
            child: TextField(
              controller: _search,
              decoration: const InputDecoration(
                prefixIcon: Icon(Icons.search),
                hintText: 'Поиск заметок',
                border: OutlineInputBorder(),
              ),
              onChanged: (_) => setState(_reload),
            ),
          ),
          FutureBuilder<List<String>>(
            future: _tags,
            builder: (context, snapshot) => SingleChildScrollView(
              scrollDirection: Axis.horizontal,
              padding: const EdgeInsets.symmetric(horizontal: 16),
              child: Row(
                children: [
                  ChoiceChip(
                    label: const Text('Все'),
                    selected: _tag == null,
                    onSelected: (_) => setState(() {
                      _tag = null;
                      _reload();
                    }),
                  ),
                  for (final tag in snapshot.data ?? <String>[])
                    Padding(
                      padding: const EdgeInsets.only(left: 8),
                      child: ChoiceChip(
                        label: Text(tag),
                        selected: _tag == tag,
                        onSelected: (_) => setState(() {
                          _tag = tag;
                          _reload();
                        }),
                      ),
                    ),
                ],
              ),
            ),
          ),
          Expanded(
            child: FutureBuilder<List<NoteRecord>>(
              future: _notes,
              builder: (context, snapshot) {
                if (snapshot.hasError) {
                  return const Center(
                    child: Text(
                      'Не удалось прочитать заметки. Вернитесь и попробуйте ещё раз.',
                    ),
                  );
                }
                if (!snapshot.hasData) {
                  return const Center(child: CircularProgressIndicator());
                }
                final notes = snapshot.data!;
                if (notes.isEmpty) {
                  return Center(
                    child: Padding(
                      padding: const EdgeInsets.all(24),
                      child: Text(
                        _search.text.isEmpty && _tag == null
                            ? 'Здесь будут Ваши заметки. Нажмите «Новая заметка», чтобы начать.'
                            : 'Подходящих заметок нет. Измените поиск или метку.',
                      ),
                    ),
                  );
                }
                return RefreshIndicator(
                  onRefresh: () async {
                    setState(_reload);
                    await _notes;
                  },
                  child: ListView.builder(
                    padding: const EdgeInsets.fromLTRB(12, 8, 12, 96),
                    itemCount: notes.length,
                    itemBuilder: (context, index) {
                      final note = notes[index];
                      return Card(
                        child: ListTile(
                          title: Text(
                            note.title.isEmpty ? 'Без названия' : note.title,
                            maxLines: 2,
                            overflow: TextOverflow.ellipsis,
                          ),
                          subtitle: Text(
                            note.readOnlyHtml
                                ? 'Форматированная заметка с компьютера'
                                : note.body,
                            maxLines: 2,
                            overflow: TextOverflow.ellipsis,
                          ),
                          onTap: () => _open(note),
                        ),
                      );
                    },
                  ),
                );
              },
            ),
          ),
        ],
      ),
    ),
    floatingActionButton: FloatingActionButton.extended(
      onPressed: () => _open(),
      icon: const Icon(Icons.add),
      label: const Text('Новая заметка'),
    ),
  );
}

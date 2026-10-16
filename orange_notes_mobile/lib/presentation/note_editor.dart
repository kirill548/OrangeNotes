import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_markdown/flutter_markdown.dart';

import '../data/mobile_store.dart';
import '../domain/note.dart';
import 'voice_input.dart';
import 'ai_chat_screen.dart';
import '../services/ai_gateway.dart';

class NoteEditor extends StatefulWidget {
  const NoteEditor({super.key, required this.store, this.note, this.aiGateway});
  final AiGateway? aiGateway;
  final MobileStore store;
  final NoteRecord? note;
  @override
  State<NoteEditor> createState() => _NoteEditorState();
}

class _NoteEditorState extends State<NoteEditor> with WidgetsBindingObserver {
  late final TextEditingController _title, _body, _tags;
  final _speech = VoiceInputSession();
  Timer? _timer;
  int? _id;
  bool _preview = false,
      _saving = false,
      _dirty = false,
      _leaving = false,
      _allowExit = false;
  String _status = '';
  Future<void> _saveQueue = Future.value();
  List<NoteRecord> _suggestions = [], _backlinks = [];
  int? _linkStart;
  bool get _readOnly => widget.note?.readOnlyHtml ?? false;
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _id = widget.note?.id;
    _title = TextEditingController(text: widget.note?.title ?? '');
    _body = TextEditingController(text: widget.note?.body ?? '');
    _tags = TextEditingController(text: widget.note?.tags.join(', ') ?? '');
    _body.addListener(_completeLinks);
    _loadBacklinks();
  }

  Future<void> _loadBacklinks() async {
    if (_id == null) return;
    final links = await widget.store.backlinks(_id!);
    if (mounted) setState(() => _backlinks = links);
  }

  void _changed(String _) {
    _dirty = true;
    _timer?.cancel();
    _timer = Timer(const Duration(milliseconds: 500), _save);
  }

  Future<void> _save() {
    _timer?.cancel();
    if (_readOnly || !_dirty) return _saveQueue;
    final title = _title.text, body = _body.text;
    final tags = _tags.text
        .split(',')
        .map((t) => t.trim())
        .where((t) => t.isNotEmpty)
        .toSet()
        .toList();
    _dirty = false;
    _saveQueue = _saveQueue.then((_) async {
      if (mounted) {
        setState(() {
          _saving = true;
          _status = 'Сохраняю…';
        });
      }
      try {
        final note = await widget.store.saveNote(
          id: _id,
          title: title,
          body: body,
          tags: tags,
        );
        _id = note.id;
        if (mounted) setState(() => _status = 'Сохранено на телефоне');
      } catch (_) {
        _dirty = true;
        if (mounted) {
          setState(
            () => _status =
                'Не удалось сохранить. Нажмите «Сохранить», чтобы повторить.',
          );
        }
      } finally {
        if (mounted) setState(() => _saving = false);
      }
    });
    return _saveQueue;
  }

  Future<void> _completeLinks() async {
    final end = _body.selection.baseOffset;
    if (end < 0 || end > _body.text.length) return;
    final before = _body.text.substring(0, end);
    final start = before.lastIndexOf('[[');
    if (start < 0 ||
        before.substring(start).contains(']]') ||
        before.substring(start).contains('\n')) {
      if (_suggestions.isNotEmpty && mounted) {
        setState(() {
          _suggestions = [];
          _linkStart = null;
        });
      }
      return;
    }
    final query = before.substring(start + 2);
    final notes = await widget.store.listNotes(query: query);
    if (!mounted ||
        _body.selection.baseOffset != end ||
        _body.text.substring(0, end) != before) {
      return;
    }
    setState(() {
      _linkStart = start;
      _suggestions = notes
          .where((n) => n.id != _id && n.title.trim().isNotEmpty)
          .take(5)
          .toList();
    });
  }

  void _insertLink(NoteRecord note) {
    final start = _linkStart, end = _body.selection.baseOffset;
    if (start == null || end < start) return;
    final value = '[[${note.title}]]';
    _body.value = TextEditingValue(
      text: _body.text.replaceRange(start, end, value),
      selection: TextSelection.collapsed(offset: start + value.length),
    );
    setState(() {
      _suggestions = [];
      _linkStart = null;
    });
    _changed('');
  }

  Future<void> _voice() async {
    if (_speech.isListening) {
      await _speech.stop();
      if (mounted) setState(() => _status = 'Запись завершена');
      return;
    }
    try {
      final ready = await _speech.initialize(
        onError: (_) {
          if (mounted) {
            setState(
              () => _status =
                  'Не удалось распознать речь. Проверьте доступ к микрофону.',
            );
          }
        },
        onStatus: (_) {
          if (mounted) setState(() {});
        },
      );
      if (!ready) {
        if (mounted) {
          setState(
            () => _status = 'Голосовой ввод недоступен. Проверьте разрешение микрофона в настройках телефона.',
          );
        }
        return;
      }
      final base = _body.text;
      final locales = await _speech.locales();
      final russian = locales
          .where((l) => l.localeId.toLowerCase().startsWith('ru'))
          .firstOrNull;
      if (mounted) {
        setState(() => _status = 'Слушаю… Нажмите микрофон, чтобы остановить.');
      }
      await _speech.listen(
        localeId: russian?.localeId,
        onResult: (result) {
          if (!mounted) return;
          _body.text =
              '$base${base.isEmpty ? '' : '\n'}${result.recognizedWords}';
          _body.selection = TextSelection.collapsed(offset: _body.text.length);
          _changed('');
        },
      );
    } catch (_) {
      if (mounted) {
        setState(
          () => _status = 'Голосовой ввод недоступен на этом устройстве.',
        );
      }
    }
  }

  String get _markdown =>
      _body.text.replaceAllMapped(RegExp(r'\[\[([^\[\]\n]+)\]\]'), (match) {
        final parts = match[1]!.split('|');
        final title = parts.first.trim();
        final label = (parts.length > 1 ? parts.sublist(1).join('|') : title)
            .replaceAll('[', r'\[')
            .replaceAll(']', r'\]');
        return '[$label](orangenotes://note/${Uri.encodeComponent(title)})';
      });
  Future<void> _openWiki(String? href) async {
    if (href == null || !href.startsWith('orangenotes://note/')) return;
    final title = Uri.decodeComponent(
      href.substring('orangenotes://note/'.length),
    );
    final matches = (await widget.store.listNotes(query: title))
        .where((n) => n.title.trim().toLowerCase() == title.toLowerCase())
        .toList();
    if (!mounted) return;
    if (matches.length != 1) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(
            matches.isEmpty ? 'Заметка «$title» пока не создана.' : 'Найдено несколько заметок с этим заголовком. Уточните название.',
          ),
        ),
      );
      return;
    }
    await _save();
    if (!mounted || _dirty) return;
    await Navigator.push(
      context,
      MaterialPageRoute<void>(
        builder: (_) => NoteEditor(
          store: widget.store,
          note: matches.single,
          aiGateway: widget.aiGateway,
        ),
      ),
    );
    _loadBacklinks();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state != AppLifecycleState.resumed) {
      _save();
      _speech.stop();
    }
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _timer?.cancel();
    _speech.cancel();
    _title.dispose();
    _body.removeListener(_completeLinks);
    _body.dispose();
    _tags.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => PopScope(
    canPop: _allowExit || (!_dirty && !_saving),
    onPopInvokedWithResult: (didPop, result) async {
      if (didPop || _leaving) return;
      _leaving = true;
      await _speech.stop();
      await _save();
      if (mounted && !_dirty) {
        setState(() => _allowExit = true);
        WidgetsBinding.instance.addPostFrameCallback((_) {
          if (mounted) Navigator.pop(context);
        });
      } else {
        _leaving = false;
      }
    },
    child: Scaffold(
      appBar: AppBar(
        title: Text(_readOnly ? 'Просмотр заметки' : 'Заметка'),
        actions: [
          if (!_readOnly)
            IconButton(
              tooltip: _preview ? 'Редактировать' : 'Предпросмотр Markdown',
              icon: Icon(_preview ? Icons.edit : Icons.visibility_outlined),
              onPressed: () => setState(() => _preview = !_preview),
            ),
          if (!_readOnly)
            IconButton(
              tooltip: 'Сохранить',
              onPressed: _saving ? null : _save,
              icon: const Icon(Icons.check),
            ),
          if (_id != null)
            IconButton(
              tooltip: 'Удалить заметку',
              icon: const Icon(Icons.delete_outline),
              onPressed: () async {
                final yes = await showDialog<bool>(
                  context: context,
                  builder: (context) => AlertDialog(
                    title: const Text('Удалить заметку?'),
                    content: const Text(
                      'Заметка будет помечена как удалённая на этом устройстве и при следующем обмене.',
                    ),
                    actions: [
                      TextButton(
                        onPressed: () => Navigator.pop(context, false),
                        child: const Text('Отмена'),
                      ),
                      FilledButton(
                        onPressed: () => Navigator.pop(context, true),
                        child: const Text('Удалить'),
                      ),
                    ],
                  ),
                );
                if (yes != true) return;
                await _save();
                try {
                  await widget.store.deleteNote(_id!);
                  _dirty = false;
                  if (context.mounted) Navigator.pop(context);
                } catch (_) {
                  if (mounted) {
                    setState(() => _status = 'Не удалось удалить заметку.');
                  }
                }
              },
            ),
        ],
      ),
      body: SafeArea(
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            if (_readOnly)
              const Card(
                child: Padding(
                  padding: EdgeInsets.all(12),
                  child: Text(
                    'Это форматированная заметка с компьютера. Просмотр исходного текста доступен; редактирование отключено, чтобы сохранить оформление.',
                  ),
                ),
              ),
            TextField(
              controller: _title,
              readOnly: _readOnly,
              decoration: const InputDecoration(labelText: 'Заголовок'),
              onChanged: _changed,
              style: Theme.of(context).textTheme.headlineSmall,
            ),
            const SizedBox(height: 12),
            if (_preview && !_readOnly)
              MarkdownBody(
                data: _markdown,
                selectable: true,
                onTapLink: (text, href, title) => _openWiki(href),
              )
            else
              TextField(
                controller: _body,
                readOnly: _readOnly || _speech.isListening,
                minLines: 10,
                maxLines: null,
                decoration: const InputDecoration(
                  hintText: 'Текст заметки. Для ссылки введите [[',
                  border: InputBorder.none,
                ),
                onChanged: _changed,
              ),
            for (final note in _suggestions)
              ListTile(
                dense: true,
                leading: const Icon(Icons.link),
                title: Text(note.title),
                onTap: () => _insertLink(note),
              ),
            if (!_readOnly)
              TextField(
                controller: _tags,
                decoration: const InputDecoration(
                  labelText: 'Метки через запятую',
                ),
                onChanged: _changed,
              ),
            if (!_readOnly)
              Align(
                alignment: Alignment.centerLeft,
                child: TextButton.icon(
                  onPressed: _voice,
                  icon: Icon(
                    _speech.isListening
                        ? Icons.stop_circle_outlined
                        : Icons.mic_none,
                  ),
                  label: Text(
                    _speech.isListening ? 'Остановить запись' : 'Надиктовать',
                  ),
                ),
              ),
            if (!_readOnly)
              const Text(
                'Голос распознаётся средствами телефона. Доступность без интернета зависит от системы.',
                style: TextStyle(fontSize: 12),
              ),
            if (widget.aiGateway != null)
              OutlinedButton.icon(
                icon: const Icon(Icons.chat_bubble_outline),
                label: const Text('Спросить ИИ по заметке'),
                onPressed: () async {
                  await _save();
                  if (!context.mounted || _dirty) return;
                  final selected = await widget.store.getNote(_id ?? -1);
                  if (!context.mounted) return;
                  if (selected == null) {
                    ScaffoldMessenger.of(context).showSnackBar(
                      const SnackBar(
                        content: Text('Сначала сохраните заметку.'),
                      ),
                    );
                    return;
                  }
                  await Navigator.push(
                    context,
                    MaterialPageRoute<void>(
                      builder: (_) => AiChatScreen(
                        gateway: widget.aiGateway!,
                        noteContext: AiContext(
                          noteId: selected.uuid,
                          title: selected.title,
                          body: selected.body,
                        ),
                      ),
                    ),
                  );
                },
              ),
            const SizedBox(height: 12),
            Semantics(liveRegion: true, child: Text(_status)),
            const Divider(height: 32),
            const Text(
              'Обратные ссылки',
              style: TextStyle(fontWeight: FontWeight.bold),
            ),
            if (_backlinks.isEmpty)
              const Padding(
                padding: EdgeInsets.symmetric(vertical: 12),
                child: Text('Другие заметки пока не ссылаются на эту.'),
              ),
            for (final note in _backlinks)
              ListTile(
                leading: const Icon(Icons.link),
                title: Text(note.title),
                onTap: () async {
                  await _save();
                  if (!context.mounted || _dirty) return;
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
                  _loadBacklinks();
                },
              ),
          ],
        ),
      ),
    ),
  );
}

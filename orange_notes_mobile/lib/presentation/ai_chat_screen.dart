import 'dart:async';

import 'package:flutter/material.dart';

import '../services/ai_gateway.dart';

/// Read-only assistant: a note is sent only after the user attaches it and sends.
class AiChatScreen extends StatefulWidget {
  const AiChatScreen({super.key, required this.gateway, this.noteContext});
  final AiGateway gateway;
  final AiContext? noteContext;

  @override
  State<AiChatScreen> createState() => _AiChatScreenState();
}

class _ChatMessage {
  _ChatMessage(this.user, this.text);
  final bool user;
  String text;
  bool localSearch = false;
}

class _AiChatScreenState extends State<AiChatScreen> {
  final _input = TextEditingController();
  final _scroll = ScrollController();
  final _messages = <_ChatMessage>[];
  StreamSubscription<AiEvent>? _subscription;
  AiContext? _attached;
  bool _busy = false;
  int _generation = 0;
  String _status = 'Готов к запросу';

  @override
  void initState() {
    super.initState();
    _attached = widget.noteContext;
  }

  void _reveal() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted && _scroll.hasClients) {
        _scroll.jumpTo(_scroll.position.maxScrollExtent);
      }
    });
  }

  void _stop() {
    _generation++;
    widget.gateway.cancel();
    _subscription?.cancel();
    _subscription = null;
    if (mounted) {
      setState(() {
        _busy = false;
        _status = 'Остановлено';
      });
    }
  }

  void _send() {
    final prompt = _input.text.trim();
    if (_busy || prompt.isEmpty) return;
    final generation = ++_generation;
    final answer = _ChatMessage(false, '');
    setState(() {
      _busy = true;
      _status = 'Подключаюсь…';
      _messages.addAll([_ChatMessage(true, prompt), answer]);
      _input.clear();
    });
    _reveal();
    void failed() {
      if (!mounted || generation != _generation) return;
      setState(() {
        _busy = false;
        _status = answer.text.isEmpty
            ? 'Недоступен'
            : 'Недоступен · Ответ не завершён';
        if (answer.text.isEmpty) {
          answer.text = 'Не удалось получить ответ. Попробуйте ещё раз.';
        }
      });
    }

    try {
      _subscription = widget.gateway
          .ask(prompt, contexts: _attached == null ? const [] : [_attached!])
          .listen(
            (event) {
              if (!mounted || generation != _generation) return;
              setState(() {
                switch (event.kind) {
                  case AiEventKind.status:
                    if (event.text == 'local_search') answer.localSearch = true;
                    _status = switch (event.text) {
                      'connecting' => 'Подключаюсь…',
                      'generating' => 'Подключён · Готовлю ответ…',
                      'local_search' =>
                        'Недоступен · Локальный поиск на телефоне',
                      'syncing' => 'Идёт синхронизация',
                      _ => 'Готовлю ответ…',
                    };
                  case AiEventKind.token:
                    answer.text += event.text;
                  case AiEventKind.done:
                    _busy = false;
                    if (_status.startsWith('Подключён')) _status = 'Подключён';
                    if (answer.text.isEmpty) {
                      answer.text = 'Ответ пуст. Попробуйте уточнить вопрос.';
                    }
                  case AiEventKind.error:
                    _busy = false;
                    _status = answer.text.isEmpty
                        ? 'Недоступен'
                        : 'Недоступен · Ответ не завершён';
                    // Never expose raw networking exceptions or protocol payloads.
                    if (answer.text.isEmpty) {
                      answer.text =
                          'Не удалось получить ответ. Попробуйте ещё раз.';
                    }
                }
              });
              _reveal();
            },
            onError: (_) => failed(),
            onDone: () {
              if (!mounted || generation != _generation) return;
              setState(() {
                _busy = false;
                if (answer.text.isEmpty) {
                  answer.text = 'Ответ пуст. Попробуйте уточнить вопрос.';
                }
              });
            },
          );
    } catch (_) {
      failed();
    }
  }

  @override
  void dispose() {
    _generation++;
    widget.gateway.cancel();
    _subscription?.cancel();
    _input.dispose();
    _scroll.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(title: const Text('Помощник')),
    body: SafeArea(
      child: Column(
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 8, 16, 8),
            child: Semantics(liveRegion: true, child: Text(_status)),
          ),
          if (_attached != null)
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 16),
              child: InputChip(
                avatar: const Icon(Icons.description_outlined),
                label: Text(
                  _attached!.title.isEmpty ? 'Без названия' : _attached!.title,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                ),
                deleteButtonTooltipMessage: 'Убрать заметку из запроса',
                onDeleted: _busy
                    ? null
                    : () => setState(() => _attached = null),
              ),
            ),
          Expanded(
            child: _messages.isEmpty
                ? const Center(
                    child: Padding(
                      padding: EdgeInsets.all(24),
                      child: Text(
                        'Задайте вопрос. Прикреплённая заметка будет отправлена только доверенному компьютеру. Без подключения доступен локальный поиск. Помощник не изменяет заметки.',
                      ),
                    ),
                  )
                : ListView.builder(
                    controller: _scroll,
                    padding: const EdgeInsets.all(16),
                    itemCount: _messages.length,
                    itemBuilder: (context, index) {
                      final message = _messages[index];
                      return Card(
                        color: message.user
                            ? Theme.of(context).colorScheme.secondaryContainer
                            : null,
                        child: Padding(
                          padding: const EdgeInsets.all(12),
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(
                                message.user
                                    ? 'Вы'
                                    : (message.localSearch
                                          ? 'Результаты поиска · на телефоне'
                                          : 'Помощник'),
                                style: const TextStyle(
                                  fontWeight: FontWeight.bold,
                                ),
                              ),
                              const SizedBox(height: 4),
                              SelectableText(
                                message.text.isEmpty ? '…' : message.text,
                              ),
                            ],
                          ),
                        ),
                      );
                    },
                  ),
          ),
          Padding(
            padding: const EdgeInsets.all(12),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.end,
              children: [
                Expanded(
                  child: TextField(
                    controller: _input,
                    minLines: 1,
                    maxLines: 5,
                    maxLength: 8192,
                    decoration: const InputDecoration(
                      hintText: 'Ваш вопрос',
                      counterText: '',
                      border: OutlineInputBorder(),
                    ),
                  ),
                ),
                const SizedBox(width: 8),
                IconButton.filled(
                  tooltip: _busy ? 'Остановить ответ' : 'Отправить',
                  onPressed: _busy ? _stop : _send,
                  icon: Icon(_busy ? Icons.stop : Icons.send),
                ),
              ],
            ),
          ),
        ],
      ),
    ),
  );
}

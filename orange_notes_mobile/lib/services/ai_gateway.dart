import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import 'package:crypto/crypto.dart';
import 'package:uuid/uuid.dart';

import '../data/mobile_store.dart';
import 'crdt_engine.dart';
import 'p2p_transport.dart';

enum AiEventKind { status, token, done, error }

class AiContext {
  const AiContext({
    required this.noteId,
    required this.title,
    required this.body,
  });
  final String noteId, title, body;
  Map<String, String> toJson() => {
    'note_id': noteId,
    'title': title,
    'body': body,
  };
}

class AiEvent {
  const AiEvent(this.kind, {this.text = '', this.sources = const []});
  final AiEventKind kind;
  final String text;
  final List<AiContext> sources;
}

abstract class AiGateway {
  Stream<AiEvent> ask(String prompt, {List<AiContext> contexts = const []});
  void cancel();
}

/// Read-only bridge. A request can never execute tools or mutate notes.
class DesktopAiGateway implements AiGateway {
  DesktopAiGateway({
    required this.context,
    required this.localActorId,
    required this.workspaceId,
    required this.peer,
    required this.host,
    required this.port,
    this.serverHostname = 'localhost',
    this.timeout = const Duration(seconds: 30),
  }) {
    validUuid(localActorId);
    validUuid(workspaceId);
    if (peer.workspaceId != workspaceId ||
        port < 1 ||
        port > 65535 ||
        context.minimumTlsProtocolVersion != TlsProtocolVersion.tls1_3 ||
        timeout <= Duration.zero ||
        timeout > const Duration(seconds: 30)) {
      throw TransportError('Invalid AI relay configuration');
    }
  }
  final SecurityContext context;
  final String localActorId, workspaceId, host, serverHostname;
  final PeerIdentity peer;
  final int port;
  final Duration timeout;
  Socket? _raw;
  SecureSocket? _socket;
  int _generation = 0;
  bool _busy = false;

  @override
  void cancel() {
    _generation++;
    _socket?.destroy();
    _raw?.destroy();
  }

  @override
  Stream<AiEvent> ask(
    String prompt, {
    List<AiContext> contexts = const [],
  }) async* {
    if (_busy) throw TransportError('AI request already active');
    _busy = true;
    final generation = ++_generation;
    final watch = Stopwatch()..start();
    final deadline = Timer(timeout, () {
      _socket?.destroy();
      _raw?.destroy();
    });
    StreamIterator<Uint8List>? reader;
    Duration remaining() {
      if (generation != _generation) throw const _Cancelled();
      final left = timeout - watch.elapsed;
      if (left <= Duration.zero) throw TimeoutException('AI deadline');
      return left;
    }

    try {
      if (prompt.trim().isEmpty ||
          utf8.encode(prompt).length > 8192 ||
          contexts.length > 8) {
        throw TransportError('AI request limit');
      }
      var contextBytes = 0;
      final seen = <String>{};
      for (final note in contexts) {
        validUuid(note.noteId);
        if (!seen.add(note.noteId) || utf8.encode(note.title).length > 1024) {
          throw TransportError('AI context identity or title limit');
        }
        contextBytes +=
            utf8.encode(note.title).length + utf8.encode(note.body).length;
      }
      if (contextBytes > 32768) throw TransportError('AI context limit');
      final requestId = const Uuid().v4();
      final bytes = utf8.encode(
        jsonEncode({
          'version': 1,
          'type': 'ai.request',
          'request_id': requestId,
          'actor_id': localActorId,
          'workspace_id': workspaceId,
          'prompt': prompt,
          'contexts': contexts.map((n) => n.toJson()).toList(),
        }),
      );
      if (bytes.length > 65536) throw TransportError('AI frame limit');
      yield const AiEvent(AiEventKind.status, text: 'connecting');
      _raw = await Socket.connect(host, port, timeout: remaining());
      remaining();
      _socket =
          await SecureSocket.secure(
            _raw!,
            host: serverHostname,
            context: context,
          ).timeout(
            remaining(),
            onTimeout: () {
              _raw?.destroy();
              throw TimeoutException('AI TLS deadline');
            },
          );
      remaining();
      final certificate = _socket!.peerCertificate;
      if (certificate == null ||
          sha256.convert(certificate.der).toString() !=
              peer.fingerprintSha256) {
        throw TransportError('Unpaired AI certificate');
      }
      final header = ByteData(4)..setUint32(0, bytes.length);
      _socket!
        ..add(header.buffer.asUint8List())
        ..add(bytes);
      await _socket!.flush().timeout(remaining());
      yield const AiEvent(AiEventKind.status, text: 'generating');
      reader = StreamIterator(_socket!);
      final buffer = <int>[];
      var sequence = 0, outputBytes = 0;
      while (true) {
        while (buffer.length < 4 ||
            buffer.length <
                4 +
                    ByteData.sublistView(
                      Uint8List.fromList(buffer.take(4).toList()),
                    ).getUint32(0)) {
          if (buffer.length >= 4) {
            final size = ByteData.sublistView(
              Uint8List.fromList(buffer.take(4).toList()),
            ).getUint32(0);
            if (size < 1 || size > 65536) {
              throw TransportError('AI frame limit');
            }
          }
          if (!await reader.moveNext().timeout(remaining())) {
            throw TransportError('AI stream interrupted');
          }
          buffer.addAll(reader.current);
          if (buffer.length > 131080) {
            throw TransportError('AI buffered frame limit');
          }
        }
        final size = ByteData.sublistView(
          Uint8List.fromList(buffer.take(4).toList()),
        ).getUint32(0);
        if (size < 1 || size > 65536) throw TransportError('AI frame limit');
        final frame = strictJson(utf8.decode(buffer.sublist(4, size + 4)));
        buffer.removeRange(0, size + 4);
        if (frame is! Map ||
            frame['version'] is! int ||
            frame['version'] != 1 ||
            frame['request_id'] != requestId ||
            frame['sequence'] is! int ||
            frame['sequence'] != sequence++) {
          throw TransportError('AI stream identity or sequence');
        }
        remaining();
        if (frame['type'] == 'ai.done' && frame.length == 4) {
          yield const AiEvent(AiEventKind.done);
          return;
        }
        if (frame['type'] == 'ai.error' &&
            frame.length == 5 &&
            frame['error_code'] is String) {
          throw TransportError('Desktop AI rejected request');
        }
        if (frame['type'] != 'ai.token' ||
            frame.length != 5 ||
            frame['text'] is! String) {
          throw TransportError('Invalid AI stream event');
        }
        final text = frame['text'] as String;
        outputBytes += utf8.encode(text).length;
        if (outputBytes > 65536 || sequence > 4096) {
          throw TransportError('AI output limit');
        }
        yield AiEvent(AiEventKind.token, text: text);
      }
    } on _Cancelled {
      return;
    } finally {
      deadline.cancel();
      _socket?.destroy();
      _raw?.destroy();
      await reader?.cancel();
      _socket = null;
      _raw = null;
      _busy = false;
    }
  }
}

class _Cancelled implements Exception {
  const _Cancelled();
}

/// Offline deterministic search, not an on-device generative model.
class LocalAiGateway implements AiGateway {
  LocalAiGateway(this.store);
  final MobileStore store;
  int _generation = 0;
  @override
  void cancel() {
    _generation++;
  }

  @override
  Stream<AiEvent> ask(
    String prompt, {
    List<AiContext> contexts = const [],
  }) async* {
    final generation = ++_generation;
    if (prompt.trim().isEmpty || utf8.encode(prompt).length > 8192) {
      throw TransportError('AI request limit');
    }
    yield const AiEvent(AiEventKind.status, text: 'local_search');
    final matches = await store.searchNotes(prompt);
    if (generation != _generation) return;
    final sources = matches
        .map(
          (n) => AiContext(
            noteId: n.uuid,
            title: n.title,
            body: n.body.length > 500 ? n.body.substring(0, 500) : n.body,
          ),
        )
        .toList();
    yield AiEvent(
      AiEventKind.token,
      sources: sources,
      text: sources.isEmpty
          ? 'Локальный поиск: совпадений нет. Для ответа ИИ подключите компьютер.'
          : 'Локальный поиск · текст заметок без генерации\n\n${sources.map((n) => '[${n.title}]\n${n.body}').join('\n\n')}',
    );
    yield const AiEvent(AiEventKind.done);
  }
}

class FallbackAiGateway implements AiGateway {
  FallbackAiGateway(this.remote, this.local);
  final AiGateway remote, local;
  int _generation = 0;
  @override
  void cancel() {
    _generation++;
    remote.cancel();
    local.cancel();
  }

  @override
  Stream<AiEvent> ask(
    String prompt, {
    List<AiContext> contexts = const [],
  }) async* {
    final generation = ++_generation;
    var receivedText = false;
    try {
      await for (final event in remote.ask(prompt, contexts: contexts)) {
        if (generation != _generation) return;
        if (event.kind == AiEventKind.token) receivedText = true;
        yield event;
      }
    } catch (_) {
      if (generation != _generation) return;
      if (receivedText) {
        yield const AiEvent(
          AiEventKind.error,
          text: 'Соединение прервано. Ответ не завершён.',
        );
        return;
      }
      yield* local.ask(prompt, contexts: contexts);
    }
  }
}

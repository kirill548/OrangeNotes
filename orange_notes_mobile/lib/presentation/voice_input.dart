import 'package:speech_to_text/speech_to_text.dart';
import 'package:speech_to_text/speech_recognition_error.dart';
import 'package:speech_to_text/speech_recognition_result.dart';

/// The platform recognizer is a singleton. Route callbacks to the current
/// editor instead of retaining the first editor's disposed State forever.
class VoiceInputSession {
  static final _engine = SpeechToText();
  static VoiceInputSession? _active;
  void Function(SpeechRecognitionError)? _error;
  void Function(String)? _status;

  bool get isListening => identical(_active, this) && _engine.isListening;

  Future<bool> initialize({
    required void Function(SpeechRecognitionError) onError,
    required void Function(String) onStatus,
  }) async {
    if (_active != null && !identical(_active, this)) await _engine.cancel();
    _active = this;
    _error = onError;
    _status = onStatus;
    return _engine.initialize(
      onError: (error) => _active?._error?.call(error),
      onStatus: (status) => _active?._status?.call(status),
    );
  }

  Future<List<LocaleName>> locales() => _engine.locales();
  Future<void> listen({
    String? localeId,
    required void Function(SpeechRecognitionResult) onResult,
  }) async {
    if (!identical(_active, this)) return;
    await _engine.listen(
      listenOptions: SpeechListenOptions(
        localeId: localeId,
        cancelOnError: true,
      ),
      onResult: (result) {
        if (identical(_active, this)) onResult(result);
      },
    );
  }

  Future<void> stop() async {
    if (identical(_active, this)) await _engine.stop();
  }

  Future<void> cancel() async {
    if (!identical(_active, this)) return;
    try {
      await _engine.cancel();
    } finally {
      if (identical(_active, this)) _active = null;
      _error = null;
      _status = null;
    }
  }
}

"""Bounded streaming buffer: syntactic boundaries never authorize model facts.

Only independently grounded complete fact objects can be shown provisionally.
Other text waits for complete packet validation. No partial JSON escapes.
"""
import re
import json

from app.services.local_ai import LocalAIError


_CONTROL = re.compile(r'<\|[^>]*(?:>|$)|</?(?:think|analysis|tool_call)(?:\b[^>]*>|$)|[\x00-\x08\x0b\x0c\x0e-\x1f]', re.I)


class GroundingValidator:
    """Use the same evidence and inference policy as the nonstreaming engine."""

    def validate(self, raw, sources, *, strict_memory=False, allow_inference=False):
        # Lazy import avoids a cycle when the engine adopts the stream buffer.
        from app.services.companion import CompanionEngine
        return CompanionEngine._validate(raw, sources, strict_memory=strict_memory,
                                         allow_inference=allow_inference)


class StreamSegmentValidator:
    def __init__(self, validator=None, *, chunk_size=48, max_buffer_chars=4 * 1024 * 1024, sources=None, revision_check=None):
        if not 40 <= chunk_size <= 50:
            raise ValueError('chunk_size must be between 40 and 50')
        if max_buffer_chars < chunk_size:
            raise ValueError('buffer limit must accommodate one segment')
        self.validator = validator or GroundingValidator()
        self.chunk_size = chunk_size
        self.max_buffer_chars = max_buffer_chars
        self.sources = sources
        self.revision_check = revision_check
        self._parse_text = ""
        self._array_started = False
        self._need_comma = False
        self._segment_count = 0
        self._preview_chars = 0
        self._parts = []
        self._buffer_size = 0
        self._aborted = False

    @property
    def buffered_chars(self):
        return self._buffer_size

    def feed(self, text):
        """Accumulate untrusted NDJSON content, emitting nothing before grounding."""
        if self._aborted:
            return []
        if not isinstance(text, str):
            raise TypeError('stream content must be text')
        if self._buffer_size + len(text) > self.max_buffer_chars:
            self.abort()
            raise LocalAIError('Ответ ИИ превышает допустимый размер.')
        # Merge tiny token pieces: bounded text must not retain millions of objects.
        if self._parts and len(self._parts[-1]) < 4096:
            self._parts[-1] += text
        elif text:
            self._parts.append(text)
        self._buffer_size += len(text)
        if self.sources is None:
            return []
        self._parse_text += text
        try:
            return self._completed_facts()
        except Exception:
            self.abort()
            raise

    def _completed_facts(self):
        result = []
        if not self._array_started:
            match = re.match(r'^\s*\{\s*"segments"\s*:\s*\[', self._parse_text)
            if not match:
                return []
            self._parse_text = self._parse_text[match.end():]
            self._array_started = True
        decoder = json.JSONDecoder()
        while True:
            self._parse_text = self._parse_text.lstrip()
            if self._need_comma:
                if not self._parse_text or self._parse_text[0] != ',':
                    break
                self._parse_text = self._parse_text[1:].lstrip()
                self._need_comma = False
            if not self._parse_text or self._parse_text[0] != '{':
                break
            try:
                segment, end = decoder.raw_decode(self._parse_text)
            except json.JSONDecodeError:
                break
            self._parse_text = self._parse_text[end:]
            self._need_comma = True
            self._segment_count += 1
            if self._segment_count > 16:
                raise LocalAIError('Ответ содержит слишком много сегментов.')
            if not isinstance(segment, dict):
                raise LocalAIError('Неверная структура ответа ИИ.')
            if _CONTROL.search(str(segment.get('text', ''))):
                raise LocalAIError('Ответ ИИ содержит управляющие токены.')
            # Only independently grounded facts can be published provisionally.
            if segment.get('kind') != 'fact':
                continue
            verified = self.validator.validate(json.dumps({'segments': [segment]}, ensure_ascii=False),
                                               self.sources, strict_memory=True)
            if self.revision_check is not None:
                if self.revision_check() is False:
                    raise LocalAIError('Источники изменились во время ответа.')
            # Bound queued previews independently of the full response budget.
            # Excess facts still undergo final validation and final rendering.
            if self._preview_chars + len(verified) <= 16384:
                result.extend(self.segment_verified(verified))
                self._preview_chars += len(verified)
        return result

    def finish(self, sources, *, strict_memory=False, allow_inference=False):
        if self._aborted:
            return []
        raw = ''.join(self._parts)
        self._clear()
        return self.validate_packet(raw, sources, strict_memory=strict_memory,
                                    allow_inference=allow_inference)

    def validate_packet(self, raw, sources, *, strict_memory=False, allow_inference=False):
        if self._aborted:
            return []
        if not isinstance(raw, str) or len(raw) > self.max_buffer_chars:
            self.abort()
            raise LocalAIError('Ответ ИИ превышает допустимый размер.')
        try:
            verified = self.validator.validate(raw, sources, strict_memory=strict_memory,
                                               allow_inference=allow_inference)
            return self.segment_verified(verified)
        except Exception:
            self._clear()
            raise

    def segment_verified(self, text):
        """Split trusted post-grounding text; callers must never pass raw tokens."""
        if self._aborted:
            return []
        if not isinstance(text, str):
            raise TypeError('verified content must be text')
        if len(text) > self.max_buffer_chars:
            raise LocalAIError('Ответ ИИ превышает допустимый размер.')
        if _CONTROL.search(text):
            raise LocalAIError('Ответ ИИ содержит незавершённые управляющие токены.')
        result = []
        offset = 0
        while offset < len(text):
            end = min(offset + self.chunk_size, len(text))
            for index in range(offset, end):
                if text[index] in '.!?\n':
                    end = index + 1
                    break
            result.append(text[offset:end])
            offset = end
        return result

    def _clear(self):
        self._parts.clear()
        self._buffer_size = 0
        self._parse_text = ""
        self._array_started = False
        self._need_comma = False
        self._segment_count = 0
        self._preview_chars = 0

    def abort(self):
        self._aborted = True
        self._clear()

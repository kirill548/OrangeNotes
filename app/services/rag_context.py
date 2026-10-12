"""Conservative byte-based input budget; never truncate the user's question."""
import json
from app.services.local_ai import LocalAIError

INPUT_TOKEN_BUDGET = 6912  # 8192 - 1024 output - 256 safety


def estimated_tokens(messages):
    # UTF-8 bytes upper-bound byte-level tokenization; deliberately conservative.
    return len(json.dumps(messages, ensure_ascii=False).encode('utf-8'))


def fit_messages(messages):
    messages = [dict(message) for message in messages]
    # Keep source text exclusively in escaped JSON user data, never system role.
    # Add delimiters as separate fields rather than interpolating note content into
    # a pseudo-XML prompt that an adversarial note could close prematurely.
    if messages and messages[-1].get('role') == 'user':
        try:
            payload = json.loads(messages[-1]['content'])
        except (ValueError, TypeError):
            payload = None
        if isinstance(payload, dict) and isinstance(payload.get('sources'), list):
            payload['context_format'] = 'isolated_sources_v1'
            payload['sources'] = [dict(source, untrusted_data=True,
                block_start=f"BEGIN_SOURCE_{source.get('source_id', index)}",
                block_end=f"END_SOURCE_{source.get('source_id', index)}")
                for index, source in enumerate(payload['sources'], 1)]
            messages[-1]['content'] = json.dumps(payload, ensure_ascii=False)
    while len(messages) > 2 and estimated_tokens(messages) > INPUT_TOKEN_BUDGET:
        del messages[1]  # Oldest history/repair turn; system and current user survive.
    if estimated_tokens(messages) > INPUT_TOKEN_BUDGET:
        payload = json.loads(messages[-1]['content'])
        sources = payload.get('sources', [])
        while sources and estimated_tokens(messages) > INPUT_TOKEN_BUDGET:
            if sources[-1].get('source_kind') == 'attachment':
                raise LocalAIError('Прикреплённые заметки превышают безопасный бюджет контекста. Уберите часть заметок или сократите вопрос.')
            sources.pop()
            messages[-1]['content'] = json.dumps(payload, ensure_ascii=False)
        if estimated_tokens(messages) > INPUT_TOKEN_BUDGET:
            raise LocalAIError('Вопрос превышает безопасный бюджет контекста. Сократите его.')
    return messages

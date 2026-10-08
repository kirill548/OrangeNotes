"""Conservative byte-based input budget; never truncate the user's question."""
import json
from app.services.local_ai import LocalAIError

INPUT_TOKEN_BUDGET = 6912  # 8192 - 1024 output - 256 safety


def estimated_tokens(messages):
    # UTF-8 bytes upper-bound byte-level tokenization; deliberately conservative.
    return len(json.dumps(messages, ensure_ascii=False).encode('utf-8'))


def fit_messages(messages):
    messages = [dict(message) for message in messages]
    while len(messages) > 2 and estimated_tokens(messages) > INPUT_TOKEN_BUDGET:
        del messages[1]  # Oldest history/repair turn; system and current user survive.
    if estimated_tokens(messages) > INPUT_TOKEN_BUDGET:
        payload = json.loads(messages[-1]['content'])
        sources = payload.get('sources', [])
        while sources and estimated_tokens(messages) > INPUT_TOKEN_BUDGET:
            sources.pop()
            messages[-1]['content'] = json.dumps(payload, ensure_ascii=False)
        if estimated_tokens(messages) > INPUT_TOKEN_BUDGET:
            raise LocalAIError('Вопрос превышает безопасный бюджет контекста. Сократите его.')
    return messages

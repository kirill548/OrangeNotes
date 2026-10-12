from app.services.intent_router import classify_intent
from app.services.assistant_actions import intent_response
from app.services.companion import CompanionEngine


def test_dictated_reminder_asks_date_without_model():
    query = 'сделай на поменание о том, что мне надо сходить к врачу.'
    assert classify_intent(query) == 'tool_call'
    response = intent_response(query, 'personal')
    assert response is not None
    assert response['status'] == 'answered'
    assert 'Уточните' in response['text']
    assert 'дат' in response['text']


def test_note_text_is_not_rewritten_as_creation():
    assert classify_intent('В моих заметках написано на поменание') == 'note_rag'


def test_request_returns_without_ollama_or_retrieval():
    def forbidden(**kwargs):
        raise AssertionError('Local clarification must not call the model')
    engine = CompanionEngine('missing-database', client_factory=forbidden)
    response = engine.ask('сделай на поменание о том, что мне надо сходить к врачу.')
    assert response['retrieval'] == 'local_intent'
    assert 'Уточните' in response['text']

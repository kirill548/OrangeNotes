"""Deterministic contracts, not a live model qualification."""
from datetime import datetime
from unittest.mock import patch
from app.services.assistant_actions import intent_response
from app.services.intent_router import conversational_response, classify_intent
from app.services.tool_schema import validate_tool_call, validate_model_response

def test_reminder_contract():
    with patch('app.utils.timezones.device_zone', return_value='Asia/Yekaterinburg'):
        result = intent_response('Напомни завтра в 15:00 купить молоко', 1, now=datetime(2026,10,12,11))
    call = validate_tool_call(result['tool_calls'][0])
    assert call['name'] == 'create_reminder'
    assert call['arguments']['date'] == '2026-10-13'
    assert call['arguments']['time'] == '15:00'
    assert 'молоко' in call['arguments']['title']

def test_search_contract():
    assert classify_intent('Найди мои заметки про отчет') == 'note_rag'
    assert validate_tool_call({'name':'search_notes','arguments':{'query':'отчет'}})['arguments']['query'] == 'отчет'

def test_greeting_no_false_call():
    result = conversational_response('Привет, как дела?')
    assert result and result['text']
    assert not result.get('tool_calls')
    assert validate_model_response({'text':result['text'],'tool_calls':[]})['tool_calls'] == []


def test_native_ollama_envelope():
    from app.services.tool_schema import validate_ollama_tool_call
    assert validate_ollama_tool_call({'id':'opaque','function':{'index':0,'name':'search_notes','arguments':{'query':'отчет'}}})['name'] == 'search_notes'

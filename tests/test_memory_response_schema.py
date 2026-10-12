import json
import unittest
from unittest.mock import patch

from app.services.local_ai import OllamaClient, memory_response_schema, _ANSWER_SCHEMA
from app.services.companion import CompanionEngine


class MemoryResponseSchemaTests(unittest.TestCase):
    def test_transport_sends_memory_grammar_and_preserves_fact_choice(self):
        client = OllamaClient()
        with patch.object(client, '_ensure_installed_local'), patch.object(client, '_request', return_value={'message': {'content': '{}'}}) as request:
            client.chat([{'role': 'user', 'content': 'Вопрос'}], json_mode='memory')
        schema = request.call_args.args[1]['format']
        refusal, fact = schema['properties']['segments']['items']['anyOf']
        self.assertEqual(refusal['properties']['text']['const'], 'В ваших заметках нет информации об этом')
        self.assertEqual(refusal['properties']['source_ids']['maxItems'], 0)
        self.assertEqual(fact['properties']['kind']['enum'], ['fact'])
        self.assertEqual(fact['properties']['quotes']['minItems'], 1)
        self.assertEqual(fact['properties']['source_ids']['minItems'], 1)

    def test_inference_is_opt_in_and_schema_cannot_mutate_defaults(self):
        schema = memory_response_schema(True)
        self.assertEqual(schema['properties']['segments']['items']['anyOf'][1]['properties']['kind']['enum'], ['fact', 'inference'])
        schema['properties']['segments']['items']['anyOf'][1]['properties']['kind']['enum'].clear()
        self.assertEqual(memory_response_schema()['properties']['segments']['items']['anyOf'][1]['properties']['kind']['enum'], ['fact'])
        self.assertIn('suggestion', _ANSWER_SCHEMA['properties']['segments']['items']['properties']['kind']['enum'])

    def test_grammar_does_not_bypass_verbatim_quote_validation(self):
        raw = json.dumps({'segments': [{'kind': 'fact', 'text': 'Подтверждено', 'source_ids': [1], 'quotes': [{'source_id': 1, 'quote': 'Выдуманная цитата'}]}]})
        from app.services.local_ai import LocalAIError
        with self.assertRaises(LocalAIError):
            CompanionEngine._validate(raw, [{'excerpt': 'Телефон Влада: 12345.'}], strict_memory=True)

    def test_question_echo_still_fails_post_generation_guard(self):
        raw = json.dumps({'segments': [{'kind': 'question', 'text': 'Какой PIN?', 'source_ids': [], 'quotes': []}]})
        from app.services.local_ai import LocalAIError
        with self.assertRaises(LocalAIError):
            CompanionEngine._validate(raw, [], strict_memory=True)

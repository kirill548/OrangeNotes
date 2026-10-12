import json
import unittest

from app.services.tool_schema import (ToolValidationError, parse_model_response,
                                     validate_model_response, validate_tool_call)


class ToolSchemaTests(unittest.TestCase):
    def reminder(self):
        return {'name': 'create_reminder', 'arguments': {
            'title': 'Купить молоко', 'date': '2026-10-13', 'time': '15:00',
            'recurrence': None, 'timezone': 'UTC'}}

    def test_valid_reminder_and_detached_copy(self):
        call = self.reminder()
        validated = validate_tool_call(call)
        call['arguments']['title'] = 'changed'
        self.assertEqual(validated['arguments']['title'], 'Купить молоко')

    def test_valid_search(self):
        for arguments in ({'query': 'отчет'}, {'query': 'отчет', 'limit': 50}):
            call = {'name': 'search_notes', 'arguments': arguments}
            self.assertEqual(validate_tool_call(call), call)

    def test_conversation_without_calls(self):
        response = {'text': 'Привет! Чем помочь?', 'tool_calls': []}
        self.assertEqual(parse_model_response(json.dumps(response)), response)

    def test_unknown_tools_and_capabilities(self):
        for name in ('delete_notes', 'shell', '__import__', ['search_notes']):
            with self.subTest(name=name), self.assertRaises(ToolValidationError):
                validate_tool_call({'name': name, 'arguments': {}})
        for key in ('workspace_id', 'database', 'sql', 'path', 'confirm'):
            with self.subTest(key=key), self.assertRaises(ToolValidationError):
                validate_tool_call({'name': 'search_notes', 'arguments': {'query': 'отчет', key: 1}})

    def test_exact_types_and_limits(self):
        for limit in (True, False, 0, 51, 1.0, '10', None):
            with self.subTest(limit=limit), self.assertRaises(ToolValidationError):
                validate_tool_call({'name': 'search_notes', 'arguments': {'query': 'x', 'limit': limit}})
        for query in ('', ' ', 'x' * 1001, 'x\x00', 4, []):
            with self.subTest(query=query), self.assertRaises(ToolValidationError):
                validate_tool_call({'name': 'search_notes', 'arguments': {'query': query}})

    def test_invalid_schedule(self):
        for key, value in (('date', '2026-02-30'), ('date', '0000-01-01'),
                           ('date', None), ('time', '24:00'), ('time', '14:60'),
                           ('time', '5:00'), ('timezone', 'UTC+01:99'),
                           ('timezone', 'UTC+ab:01'), ('title', ''), ('title', 'x' * 101)):
            call = self.reminder()
            call['arguments'][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ToolValidationError):
                validate_tool_call(call)

    def test_recurrence_allowlist_and_consistency(self):
        call = self.reminder()
        call['arguments']['recurrence'] = 'FREQ=WEEKLY;BYDAY=TU;BYHOUR=15;BYMINUTE=0'
        validate_tool_call(call)
        for rule in ('FREQ=MONTHLY;BYHOUR=15;BYMINUTE=0',
                     'FREQ=DAILY;BYHOUR=14;BYMINUTE=0',
                     'FREQ=WEEKLY;BYDAY=TU,TU;BYHOUR=15;BYMINUTE=0',
                     'FREQ=DAILY;BYHOUR=15;BYMINUTE=0;COUNT=4'):
            call['arguments']['recurrence'] = rule
            with self.subTest(rule=rule), self.assertRaises(ToolValidationError):
                validate_tool_call(call)

    def test_task_has_no_schedule(self):
        call = self.reminder()
        call['name'] = 'create_task'
        with self.assertRaises(ToolValidationError):
            validate_tool_call(call)
        call['arguments'].update(date=None, time=None, recurrence=None)
        validate_tool_call(call)

    def test_bad_json_and_duplicate_keys(self):
        for raw in ('not json', '```json\n{}\n```',
                    '{"text":"a","text":"b","tool_calls":[]}',
                    '{"text":"x","tool_calls":[],"value":NaN}',
                    '[' * 2000 + ']' * 2000, 'x' * 65537,
                    '{"text":"\\ud800","tool_calls":[]}'):
            with self.subTest(raw=raw[:80]), self.assertRaises(ToolValidationError):
                parse_model_response(raw)

    def test_no_batch_or_unknown_response_fields(self):
        response = {'text': '', 'tool_calls': [self.reminder(), self.reminder()]}
        with self.assertRaises(ToolValidationError):
            validate_model_response(response)
        for value in ({'text': 'x'}, {'text': 'x', 'tool_calls': [], 'execute': True},
                      {'text': [], 'tool_calls': []}, {'text': 'x', 'tool_calls': None}):
            with self.subTest(value=value), self.assertRaises(ToolValidationError):
                validate_model_response(value)

"""Independent regressions replaying the user's multi-turn reminder transcript."""
import unittest
from datetime import datetime

from app.services.assistant_actions import intent_response


class ReminderUserTranscriptTests(unittest.TestCase):
    now = datetime(2026, 10, 12, 11)
    body = 'мне нужно на приём к врачу в 15:00'

    def ask(self, query, history):
        response = intent_response(query, 1, history, now=self.now)
        self.assertIsNotNone(response)
        history.extend([
            {'role': 'user', 'workspace_id': 1, 'content': query},
            {'role': 'assistant', 'workspace_id': 1, 'content': response['text']},
        ])
        return response

    def assert_notification(self, response, hour):
        draft = response['action_draft']
        self.assertEqual(draft['once_at'], f'2026-10-15T{hour:02d}:00:00')
        self.assertEqual(draft['body'], self.body)
        self.assertEqual(response['tool_calls'][0]['arguments']['time'], f'{hour:02d}:00')

    def test_full_user_sequence_two_commands_and_bare_time_correction(self):
        history = []
        response = self.ask('Сделай мне напоминание на 15 октября, что ' + self.body, history)
        self.assertIn('Уточните время', response['text'])
        self.assertNotIn('action_draft', response)
        self.assert_notification(self.ask('15 октября 12:00', history), 12)
        response = self.ask('Создай напоминание на 15 октября, что ' + self.body, history)
        self.assertIn('Уточните время', response['text'])
        self.assert_notification(self.ask('15 октября 11:00', history), 11)
        self.assert_notification(self.ask('11:00', history), 11)

    def test_new_parser_recovers_from_exact_old_date_clarification(self):
        history = [
            {'role': 'user', 'workspace_id': 1, 'content': 'Сделай мне напоминание на 15 октября, что ' + self.body},
            {'role': 'assistant', 'workspace_id': 1, 'content': 'Уточните дату напоминания. Например «завтра в 10:00».'},
        ]
        self.assert_notification(self.ask('15 октября 12:00', history), 12)

    def test_bare_time_recovers_after_exact_old_multiple_time_clarification(self):
        history = [
            {'role': 'user', 'workspace_id': 1, 'content': 'Создай напоминание на 15 октября, что ' + self.body},
            {'role': 'assistant', 'workspace_id': 1, 'content': 'Уточните дату напоминания. Например «завтра в 10:00».'},
            {'role': 'user', 'workspace_id': 1, 'content': '15 октября 11:00'},
            {'role': 'assistant', 'workspace_id': 1, 'content': 'Уточните одно время для этого напоминания. Несколько времён можно добавить в редакторе расписания.'},
        ]
        self.assert_notification(self.ask('11:00', history), 11)

    def test_missing_comma_before_event_content(self):
        for query in (
            'Напомни мне на 15 октября в 12:00 что ' + self.body,
            'Сделай напоминание на 15 октября в 12:00 что ' + self.body,
            'Создай напоминание на 15 октября в 12:00 чтобы ' + self.body,
        ):
            with self.subTest(query=query):
                self.assert_notification(self.ask(query, []), 12)

    def test_notification_verb_alias_and_followup(self):
        history = []
        response = self.ask('Уведоми меня 15 октября, что ' + self.body, history)
        self.assertIn('Уточните время', response['text'])
        self.assert_notification(self.ask('12:00', history), 12)
        self.assert_notification(self.ask('Уведомите меня 15 октября в 11:00 что ' + self.body, []), 11)

    def test_negated_notification_alias_never_creates_a_draft(self):
        response = intent_response('Не уведоми меня 15 октября в 12:00, что ' + self.body, 1, now=self.now)
        if response is not None:
            self.assertNotIn('action_draft', response)


if __name__ == '__main__':
    unittest.main()

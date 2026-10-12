"""User-facing reminder slots: notification time differs from event content.

These cases never invoke a model, download data, or save a draft.
"""
import unittest
from datetime import datetime
from unittest.mock import patch

from app.services.assistant_actions import intent_response, validate_tool_call


NOW = datetime(2026, 10, 12, 11)

ACCEPT = [
    ('explicit_body', 'Сделай напоминание на 15 октября на 13:00, что мне надо сходить на приём к врачу в 15:00', '2026-10-15T13:00:00', '15:00'),
    ('body_inherits_date', 'Сделай мне напоминание на 12:00, что запись у врача 15 октября 15:00', '2026-10-15T12:00:00', '15:00'),
    ('reversed_order', 'У меня приём 15 октября 15:00, напомни в 12:00', '2026-10-15T12:00:00', '15:00'),
    ('reversed_preposition', 'Приём у врача 15 октября в 15:00, напомни мне в 12:00', '2026-10-15T12:00:00', '15:00'),
    ('explicit_notification_date', 'Напомни 14 октября в 12:00, что приём у врача 15 октября в 15:00', '2026-10-14T12:00:00', '15 октября'),
    ('explicit_tomorrow', 'Напомни завтра в 12:00, что приём у врача 15 октября в 15:00', '2026-10-13T12:00:00', '15 октября'),
    ('event_same_time', 'Напомни 15 октября в 15:00, что приём у врача в 15:00', '2026-10-15T15:00:00', '15:00'),
    ('two_body_times', 'Напомни 15 октября в 12:00, что приём в 15:00, а анализы в 16:00', '2026-10-15T12:00:00', '16:00'),
    ('explicit_year', 'Напомни 15 октября 2027 в 12:00, что приём в 15:00', '2027-10-15T12:00:00', '15:00'),
    ('numeric_date', 'Напомни 15.10.2026 в 12:00, что приём в 15:00', '2026-10-15T12:00:00', '15:00'),
    ('today_future', 'Напомни сегодня в 12:00, что созвон в 15:00', '2026-10-12T12:00:00', '15:00'),
    ('day_after_tomorrow', 'Напомни послезавтра в 12:00, что встреча в 15:00', '2026-10-14T12:00:00', '15:00'),
    ('explicit_date_multiple_events', 'Напомни 14 октября в 12:00, что врач 15 октября в 15:00, а анализы 16 октября в 16:00', '2026-10-14T12:00:00', '16 октября'),
]

REJECT = [
    ('two_event_dates_no_date', 'Сделай напоминание в 12:00, что врач 15 октября в 15:00, а анализы 16 октября в 16:00'),
    ('two_notification_times', 'Напомни 15 октября в 12:00 и в 13:00 проверить отчёт'),
    ('two_notification_dates', 'Напомни 15 октября и 16 октября в 12:00 проверить отчёт'),
    ('invalid_notification_hour', 'Напомни 15 октября в 25:00, что врач в 15:00'),
    ('invalid_notification_minute', 'Напомни 15 октября в 12:99, что врач в 15:00'),
    ('invalid_calendar_date', 'Напомни 31 февраля 2027 в 12:00, что врач в 15:00'),
    ('invalid_event_date_inherited', 'Сделай напоминание в 12:00, что врач 31 февраля 2027 в 15:00'),
    ('past_notification', 'Напомни сегодня в 10:00, что врач в 15:00'),
    ('explicit_timezone', 'Напомни 15 октября в 12:00 по московскому времени, что врач в 15:00'),
    ('explicit_utc', 'Напомни 15 октября в 12:00 UTC, что врач в 15:00'),
    ('negated_creation', 'Не создавай напоминание 15 октября в 12:00, что врач в 15:00'),
    ('negated_reminder', 'Не напомни мне 15 октября в 12:00, что врач в 15:00'),
    ('missing_reminder_time', 'Напомни 15 октября, что приём в 15:00'),
]


class ReminderMatrixTests(unittest.TestCase):
    def respond(self, query, workspace=1, history=None):
        with patch('app.utils.timezones.device_zone', return_value='UTC+05:00'):
            return intent_response(query, workspace, history=history, now=NOW)

    def test_followup_time_preserves_body_event_time(self):
        request = 'Сделай напоминание на 15 октября на 13:00, что врач в 15:00'
        first = self.respond(request)
        history = [{'role': 'user', 'workspace_id': 1, 'content': request},
                   {'role': 'assistant', 'workspace_id': 1, 'content': first['text']}]
        result = self.respond('Напомнить мне надо в 12:00', history=history)
        self.assertEqual(result['action_draft']['once_at'], '2026-10-15T12:00:00')
        self.assertIn('15:00', result['action_draft']['body'])

    def test_followup_cannot_cross_workspace(self):
        history = [{'role': 'user', 'workspace_id': 1,
                    'content': 'Напомни 15 октября врачу'},
                   {'role': 'assistant', 'workspace_id': 1,
                    'content': 'Уточните время напоминания.'}]
        result = self.respond('в 12:00', workspace=2, history=history)
        self.assertNotIn('action_draft', result or {})

    def test_followup_does_not_replay_after_saved_confirmation(self):
        history = [{'role': 'user', 'workspace_id': 1,
                    'content': 'Напомни 15 октября в 12:00 врачу'},
                   {'role': 'assistant', 'workspace_id': 1,
                    'content': 'Напоминание создано.'}]
        result = self.respond('в 13:00', history=history)
        self.assertNotIn('action_draft', result or {})


def accepted_case(query, stamp, body_part):
    def test(self):
        result = self.respond(query)
        self.assertIsNotNone(result)
        self.assertIn('action_draft', result, result.get('text'))
        draft = result['action_draft']
        self.assertEqual(draft['once_at'], stamp)
        self.assertIn(body_part, draft['body'])
        self.assertEqual(draft['workspace_id'], 1)
        self.assertIn('подтверждения', result['text'].casefold())
        self.assertEqual(len(result['tool_calls']), 1)
        validate_tool_call(result['tool_calls'][0])
        self.assertEqual(result['tool_calls'][0]['name'], 'create_reminder')
    return test


def rejected_case(query):
    def test(self):
        result = self.respond(query)
        self.assertNotIn('action_draft', result or {}, query)
        self.assertNotIn('tool_calls', result or {}, query)
    return test


for name, query, stamp, body_part in ACCEPT:
    setattr(ReminderMatrixTests, 'test_accept_' + name, accepted_case(query, stamp, body_part))
for name, query in REJECT:
    setattr(ReminderMatrixTests, 'test_reject_' + name, rejected_case(query))


if __name__ == '__main__':
    unittest.main()

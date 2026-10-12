import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from app.database.store import Store
from app.services.assistant_actions import intent_response, commit_action_draft, validate_tool_call, validate_recurrence


class ConversationalToolsTests(unittest.TestCase):
    now = datetime(2026, 10, 12, 11, 0)

    def response(self, query):
        with patch('app.utils.timezones.device_zone', return_value='Asia/Yekaterinburg'):
            return intent_response(query, 1, now=self.now)

    def test_weekly_tool_json_and_next_occurrence(self):
        result = self.response('Напомни проверить отчет каждый понедельник в 10:00')
        call = json.loads(json.dumps(result['tool_calls']))[0]
        self.assertEqual(call['name'], 'create_reminder')
        self.assertEqual(call['arguments'], dict(title='проверить отчет', date='2026-10-19', time='10:00', recurrence='FREQ=WEEKLY;BYDAY=MO;BYHOUR=10;BYMINUTE=0', timezone='Asia/Yekaterinburg'))
        self.assertEqual(validate_tool_call(call), call)

    def test_daily_and_task(self):
        reminder = self.response('Создай напоминание пить воду ежедневно в 12:00')
        self.assertEqual(reminder['tool_calls'][0]['arguments']['recurrence'], 'FREQ=DAILY;BYHOUR=12;BYMINUTE=0')
        task = self.response('Создай задачу проверить отчет')
        self.assertEqual(task['tool_calls'][0]['name'], 'create_task')
        self.assertIsNone(task['action_draft']['once_at'])

    def test_weekdays_and_weekend_rrule(self):
        work = self.response('Напомни проверить отчет по будням в 10:00')['tool_calls'][0]
        self.assertEqual(work['arguments']['recurrence'], 'FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR;BYHOUR=10;BYMINUTE=0')
        weekend = self.response('Напомни проверить отчет по выходным в 10:00')['tool_calls'][0]
        self.assertEqual(weekend['arguments']['recurrence'], 'FREQ=WEEKLY;BYDAY=SA,SU;BYHOUR=10;BYMINUTE=0')

    def test_missing_invalid_and_unsupported_slots_clarify(self):
        for query in ('Напомни проверить отчет каждый понедельник', 'Напомни проверить отчет завтра в 25:00', 'Напомни отчет каждый месяц в 10:00', 'Напомни отчет каждый понедельник и вторник в 10:00', 'Напомни отчет каждый понедельник завтра в 10:00', 'Напомни отчет каждый понедельник в 10:00 в течение 5 недель'):
            answer = self.response(query)
            self.assertNotIn('action_draft', answer, query)
            self.assertIn('Уточните', answer['text'])

    def test_leap_day_and_exact_once_time(self):
        call = self.response('Создай задачу проверить отчет 29.02.2028 в 09:15')['tool_calls'][0]
        self.assertEqual(call['arguments']['date'], '2028-02-29')
        self.assertEqual(call['arguments']['time'], '09:15')
        self.assertIsNone(call['arguments']['recurrence'])

    def test_unknown_tool_extra_fields_and_rrule_rejected(self):
        call = self.response('Напомни отчет завтра в 10:00')['tool_calls'][0]
        call['arguments']['execute'] = True
        with self.assertRaises(ValueError):
            validate_tool_call(call)
        for rule in ('FREQ=MONTHLY;BYHOUR=10;BYMINUTE=0', 'FREQ=WEEKLY;BYDAY=TU;BYHOUR=10;BYMINUTE=0', 'FREQ=DAILY;BYHOUR=11;BYMINUTE=0', 'FREQ=DAILY;FREQ=DAILY;BYHOUR=10;BYMINUTE=0'):
            with self.assertRaises(ValueError):
                validate_recurrence(rule, '2026-10-19T10:00:00')

    def test_repeat_commit_atomic_idempotent_no_write_before_confirmation(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory)/'notes.db')
            try:
                draft = self.response('Напомни проверить отчет каждый понедельник в 10:00')['action_draft']
                self.assertEqual(store.db.execute('SELECT COUNT(*) FROM notes').fetchone()[0], 0)
                note_id = commit_action_draft(store, draft, self.now)
                self.assertEqual(commit_action_draft(store, draft, self.now), note_id)
                reminder = store.reminder(note_id)
                self.assertEqual(reminder['mode'], 'repeat')
                self.assertEqual(reminder['days'], [0])
                self.assertEqual(reminder['times'], ['10:00'])
                self.assertEqual(reminder['timezone_id'], 'Asia/Yekaterinburg')
                self.assertEqual(store.db.execute('SELECT COUNT(*) FROM notes').fetchone()[0], 1)
            finally:
                store.db.close()

    def test_invalid_repeat_never_creates_note(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory)/'notes.db')
            try:
                draft = self.response('Напомни отчет каждый понедельник в 10:00')['action_draft']
                draft['recurrence'] = 'FREQ=MONTHLY'
                with self.assertRaises(ValueError):
                    commit_action_draft(store, draft, self.now)
                self.assertEqual(store.db.execute('SELECT COUNT(*) FROM notes').fetchone()[0], 0)
            finally:
                store.db.close()

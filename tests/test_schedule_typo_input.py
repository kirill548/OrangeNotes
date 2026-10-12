import unittest
from datetime import datetime
from app.services.assistant_actions import intent_response


class ScheduleTypoInputTests(unittest.TestCase):
    now = datetime(2026, 10, 1, 8)

    def ask(self, text, history=None):
        return intent_response(text, 1, history, now=self.now)

    def test_compact_and_misspelled_dates(self):
        for date in ('12октября10:00', '12октябяр10:00', '12октябра10:00',
                     '12 октяборя 10:00', '12 октября 10 : 00', '12 октября в 10.00'):
            with self.subTest(date=date):
                draft = self.ask('Создай напоминание на ' + date + ', что созвон с командой в 15:00')['action_draft']
                self.assertEqual(draft['once_at'], '2026-10-12T10:00:00')
                self.assertEqual(draft['body'], 'созвон с командой в 15:00')

    def test_compact_followup_preserves_appointment(self):
        query = 'Сделай напоминание, что приём в 14:00'
        answer = self.ask(query)
        history = [dict(role='user', workspace_id=1, content=query),
                   dict(role='assistant', workspace_id=1, content=answer['text'])]
        draft = self.ask('12октябяр10:00', history)['action_draft']
        self.assertEqual(draft['once_at'], '2026-10-12T10:00:00')
        self.assertEqual(draft['body'], 'приём в 14:00')

    def test_all_months_compact_and_typo_matrix(self):
        months = [('января', 'янврая'), ('февраля', 'феврля'), ('марта', 'марат'),
                  ('апреля', 'апрлея'), ('мая', 'майя'), ('июня', 'июння'),
                  ('июля', 'июлля'), ('августа', 'авгусат'), ('сентября', 'сентябяр'),
                  ('октября', 'октябяр'), ('ноября', 'ноябяр'), ('декабря', 'декабяр')]
        for month, words in enumerate(months, 1):
            for word in words:
                for clock in ('00:00', '08:05', '12:00', '23:59'):
                    with self.subTest(month=word, clock=clock):
                        response = intent_response(f'Создай напоминание на 15{word}{clock}, что встреча',
                                                   1, now=datetime(2026, 1, 1))
                        self.assertEqual(response['action_draft']['once_at'], f'2026-{month:02d}-15T{clock}:00')

    def test_invalid_numbers_and_ambiguous_month_are_not_guessed(self):
        for slots in ('32октября10:00', '12октября25:00', '12октября10:99',
                      '12октября10:0', '12октября100:00', '12июя10:00'):
            with self.subTest(slots=slots):
                self.assertNotIn('action_draft', self.ask('Создай напоминание на ' + slots + ', что встреча'))

    def test_weekday_typo_and_original_content(self):
        result = self.ask('Напомни каждый понеделник в 10:00, что проверить отчёт октября и ID12октябяр')
        self.assertIn('BYDAY=MO', result['action_draft']['recurrence'])
        self.assertEqual(result['action_draft']['body'], 'проверить отчёт октября и ID12октябяр')

    def test_past_date_is_not_moved_to_another_day(self):
        result = intent_response('Создай напоминание на 12октября10:00, что встреча', 1,
                                 now=datetime(2026, 10, 12, 11))
        self.assertNotIn('action_draft', result)
        self.assertIn('прошло', result['text'])

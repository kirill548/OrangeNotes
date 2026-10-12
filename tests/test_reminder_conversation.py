import unittest
from datetime import datetime
from app.services.assistant_actions import intent_response


class ReminderConversationTests(unittest.TestCase):
    now = datetime(2026, 10, 12, 11)
    query = 'Сделай напоминание на 15 октября на 13:00, что мне надо сходить на приём к врачу в 15:00'

    def ask(self, text, history=None, workspace=1):
        return intent_response(text, workspace, history, now=self.now)

    def history(self, response):
        return [{'role': 'user', 'workspace_id': 1, 'content': self.query},
                {'role': 'assistant', 'workspace_id': 1, 'content': response}]

    def test_notification_time_is_separate_from_event_time(self):
        answer = self.ask(self.query)
        draft = answer['action_draft']
        self.assertEqual(draft['once_at'], '2026-10-15T13:00:00')
        self.assertEqual(draft['body'], 'мне надо сходить на приём к врачу в 15:00')
        self.assertIn('15.10.2026 в 13:00', answer['text'])

    def test_note_with_reminder_and_different_appointment_time(self):
        answer = self.ask('Создай заметку на 15 октября с напоминанием на 12:00, что мне надо на приём к врачу в 15:00')
        draft = answer['action_draft']
        self.assertEqual(draft['once_at'], '2026-10-15T12:00:00')
        self.assertEqual(draft['body'], 'мне надо на приём к врачу в 15:00')
        self.assertEqual(answer['tool_calls'][0]['arguments']['time'], '12:00')

    def test_explaining_clarification_keeps_original_context(self):
        history = self.history('Уточните одно время для этого напоминания.')
        explanation = self.ask('Не понял', history)
        self.assertIn('время', explanation['text'])
        self.assertNotIn('action_draft', explanation)
        history += [{'role': 'user', 'workspace_id': 1, 'content': 'Не понял'},
                    {'role': 'assistant', 'workspace_id': 1, 'content': explanation['text']}]
        draft = self.ask('Напомнить мне надо в 12:00', history)['action_draft']
        self.assertEqual(draft['once_at'], '2026-10-15T12:00:00')
        self.assertIn('в 15:00', draft['body'])

    def test_named_date_and_time_slot_correction(self):
        draft = self.ask('15 октября 12:00', self.history('Уточните время уведомления.'))['action_draft']
        self.assertEqual(draft['once_at'], '2026-10-15T12:00:00')
        self.assertIn('в 15:00', draft['body'])

    def test_missing_notification_clock_then_date_and_clock_followup(self):
        query = 'Сделай мне напоминание на 15 октября, что мне нужно на приём к врачу в 15:00'
        answer = self.ask(query)
        self.assertNotIn('action_draft', answer)
        self.assertIn('Уточните время', answer['text'])
        history = [{'role': 'user', 'workspace_id': 1, 'content': query},
                   {'role': 'assistant', 'workspace_id': 1, 'content': answer['text']}]
        for clarification in ('15 октября 12:00', 'Напомнить мне надо в 12:00', '12:00'):
            with self.subTest(clarification=clarification):
                draft = self.ask(clarification, history)['action_draft']
                self.assertEqual(draft['once_at'], '2026-10-15T12:00:00')
                self.assertEqual(draft['body'], 'мне нужно на приём к врачу в 15:00')

    def test_correction_of_unconfirmed_draft(self):
        original = self.ask(self.query)
        corrected = self.ask('Напомнить мне надо в 12:00', self.history(original['text']))
        self.assertEqual(corrected['action_draft']['once_at'], '2026-10-15T12:00:00')

    def test_context_does_not_cross_space_or_replay_saved_draft(self):
        history = self.history('Уточните время уведомления.')
        self.assertIsNone(self.ask('Напомнить мне надо в 12:00', history, workspace=2))
        history.append({'role': 'assistant', 'workspace_id': 1, 'content': 'Заметка сохранена.'})
        self.assertIsNone(self.ask('Напомнить мне надо в 12:00', history))

    def test_invalid_named_date_and_ambiguous_notification_are_not_guessed(self):
        for text in ('Сделай напоминание на 31 февраля на 13:00, что приём в 15:00',
                     'Сделай напоминание завтра в 12:00 или в 13:00, что приём в 15:00'):
            self.assertNotIn('action_draft', self.ask(text))

    def test_dates_inside_plain_note_do_not_create_unrequested_reminder(self):
        draft = self.ask('Создай заметку, что у меня запись 15 октября в 15:00')['action_draft']
        self.assertIsNone(draft['once_at'])
        self.assertIn('15 октября в 15:00', draft['body'])

    def test_appointment_without_notification_date_or_time(self):
        query = 'Сделай напоминание, что мне нужно к парикхамеру в 14:00'
        answer = self.ask(query)
        self.assertIn('дату и время', answer['text'])
        history = [{'role': 'user', 'workspace_id': 1, 'content': query},
                   {'role': 'assistant', 'workspace_id': 1, 'content': answer['text']}]
        draft = self.ask('15 октября 12:00', history)['action_draft']
        self.assertEqual(draft['once_at'], '2026-10-15T12:00:00')
        self.assertEqual(draft['body'], 'мне нужно к парикхамеру в 14:00')

    def test_month_clock_and_word_order_matrix(self):
        months = ('января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря')
        templates = (
            'Сделай напоминание на 15 {month} на {clock}, что встреча в 15:00',
            'Создай напоминание на {clock}, что встреча 15 {month} в 15:00',
            'Напомни в {clock}, что встреча 15 {month} в 15:00',
            'Встреча 15 {month} в 15:00, напомни в {clock}',
            'Напомни в {clock} о встрече 15 {month} в 15:00',
        )
        for month_number, month in enumerate(months, 1):
            for clock in ('00:00', '08:05', '12:00', '23:59'):
                for template in templates:
                    query = template.format(month=month, clock=clock)
                    with self.subTest(query=query):
                        response = intent_response(query, 1, now=datetime(2026, 1, 1, 11))
                        draft = response['action_draft']
                        self.assertEqual(draft['once_at'], f'2026-{month_number:02d}-15T{clock}:00')
                        self.assertIn('15:00', draft['body'])

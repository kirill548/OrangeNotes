import unittest
from datetime import datetime
from app.services.note_titles import suggest_note_title
from app.services.assistant_actions import intent_response


class NoteTitleTests(unittest.TestCase):
    def test_visit_titles(self):
        for body, expected in (
            ('мне нужно к парикмахеру в 15:00', 'Визит к парикмахеру'),
            ('мне надо на приём к врачу в 15:00', 'Визит к врачу'),
            ('у меня запись к стоматологу 15 октября в 14:00', 'Визит к стоматологу'),
        ):
            with self.subTest(body=body):
                self.assertEqual(suggest_note_title(body), expected)

    def test_other_actions_keep_their_meaning(self):
        for body, expected in (
            ('мне нужно купить лекарства в 15:00', 'купить лекарства'),
            ('позвонить парикмахеру в 14:00', 'позвонить парикмахеру'),
            ('отменить запись к врачу в 14:00', 'отменить запись к врачу'),
            ('проверить отчет', 'проверить отчет'),
            ('не идти к врачу', 'не идти к врачу'),
        ):
            with self.subTest(body=body):
                self.assertEqual(suggest_note_title(body), expected)

    def test_short_titles_and_empty_fallback(self):
        self.assertLessEqual(len(suggest_note_title('обсудить ' * 100)), 60)
        self.assertEqual(suggest_note_title('15:00'), 'Напоминание')

    def test_draft_retains_original_body_and_notification_time(self):
        result = intent_response('Создай напоминание на 15 октября в 12:00, что мне нужно к парикмахеру в 15:00', 1, now=datetime(2026, 10, 12))
        draft = result['action_draft']
        self.assertEqual(draft['title'], 'Визит к парикмахеру')
        self.assertEqual(draft['body'], 'мне нужно к парикмахеру в 15:00')
        self.assertEqual(draft['once_at'], '2026-10-15T12:00:00')

"""Work reminder title coverage without changing the scheduled note payload."""
import unittest
from datetime import datetime

from app.services.note_titles import suggest_note_title
from app.services.assistant_actions import intent_response


CALLS = [
    (f'{prefix}созвониться с {who}{clock}', f'Созвон с {who}')
    for prefix in ('', 'мне нужно ')
    for who in ('коллегой', 'командой', 'клиентом')
    for clock in ('', ' в 14:00')
]
MEETINGS = [
    (f'{prefix}встретиться с {who}{clock}', f'Встреча с {who}')
    for prefix in ('', 'мне надо ')
    for who in ('партнёрами', 'заказчиком', 'руководителем')
    for clock in ('', ' в 15:30')
]
ATTENDANCE = [
    (f'{action} {where}', title)
    for action in ('присутствовать на', 'участвовать в', 'мне нужно присутствовать на', 'мне надо участвовать в')
    for where, title in (
        ('ВКС с клиентом', 'ВКС с клиентом'),
        ('собрании отдела', 'Собрание отдела'),
        ('совещании по проекту', 'Совещание по проекту'),
        ('встрече команды', 'Встреча команды'),
    )
]
SAFETY = [
    ('отменить созвон с клиентом', ('отмен', 'клиент')),
    ('перенести созвон с командой', ('перен', 'команд')),
    ('подготовить вопросы для созвона', ('подготов', 'вопрос')),
    ('не созваниваться с поставщиком', ('не', 'поставщик')),
    ('отменить встречу с заказчиком', ('отмен', 'заказчик')),
    ('перенести встречу с партнёрами', ('перен', 'партнёр')),
    ('подготовиться к встрече с клиентом', ('подготов', 'клиент')),
    ('не встречаться с подрядчиком', ('не', 'подрядчик')),
    ('подготовить материалы к ВКС', ('подготов', 'материал', 'вкс')),
    ('отменить собрание отдела', ('отмен', 'отдел')),
    ('не участвовать в совещании', ('не', 'совещан')),
    ('проверить оборудование перед ВКС', ('провер', 'оборуд', 'вкс')),
]


class WorkNoteTitleTests(unittest.TestCase):
    def test_contextual_work_titles(self):
        for body, expected in CALLS + MEETINGS + ATTENDANCE:
            with self.subTest(body=body):
                self.assertEqual(suggest_note_title(body), expected)

    def test_preparation_cancellation_and_negation_keep_action(self):
        for body, stems in SAFETY:
            with self.subTest(body=body):
                title = suggest_note_title(body).casefold()
                for stem in stems:
                    self.assertIn(stem, title)

    def test_work_drafts_preserve_original_text_and_notification(self):
        records = CALLS + MEETINGS + ATTENDANCE + SAFETY
        self.assertGreaterEqual(len(records), 50)
        for body, _ in records:
            with self.subTest(body=body):
                reply = intent_response(
                    f'Создай напоминание на 15 октября в 12:00, что {body}',
                    1, now=datetime(2026, 10, 12, 9, 0),
                )
                draft = reply['action_draft']
                self.assertEqual(draft['body'], body)
                self.assertEqual(draft['once_at'], '2026-10-15T12:00:00')
                self.assertEqual(draft['title'], suggest_note_title(body))
                self.assertLessEqual(len(draft['title']), 60)


if __name__ == '__main__':
    unittest.main()

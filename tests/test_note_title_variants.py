"""Independent contextual-title matrix; factual body content stays untouched."""
import unittest
from datetime import datetime

from app.services.note_titles import suggest_note_title
from app.services.assistant_actions import intent_response


SHOP_CASES = [
    ('мне надо сходить в магазин', 'Поход в магазин'),
    ('мне нужно сходить в магазин', 'Поход в магазин'),
    ('мне необходимо сходить в магазин', 'Поход в магазин'),
    ('сходить в магазин', 'Поход в магазин'),
    ('пойти в магазин', 'Поход в магазин'),
    ('мне надо пойти в магазин', 'Поход в магазин'),
    ('мне нужно сходить в магазин в 14:25', 'Поход в магазин'),
    ('мне надо сходить в магазин завтра', 'Поход в магазин'),
    ('сходить в магазин 15 октября в 12:00', 'Поход в магазин'),
    ('  мне надо   сходить в магазин  ', 'Поход в магазин'),
]

VISIT_CASES = [
    (f'{prefix}к {person}{suffix}', f'Визит к {person}')
    for prefix in ('мне нужно ', 'мне надо сходить ', 'на приём ', 'у меня запись ')
    for person in ('врачу', 'парикмахеру', 'стоматологу', 'терапевту', 'окулисту')
    for suffix in ('', ' в 15:00')
]

# Preserve the requested action and objects instead of guessing a new intent.
ACTION_CASES = [
    ('купить молоко и хлеб', ('куп', 'молок', 'хлеб')),
    ('купить лекарства', ('куп', 'лекарств')),
    ('купить подарок маме', ('куп', 'подар', 'мам')),
    ('заказать продукты', ('заказ', 'продукт')),
    ('забрать заказ из магазина', ('заб', 'заказ')),
    ('вернуть товар в магазин', ('вер', 'товар')),
    ('позвонить парикмахеру', ('звон', 'парикмахер')),
    ('позвонить врачу', ('звон', 'врач')),
    ('позвонить маме', ('звон', 'мам')),
    ('написать коллеге', ('пис', 'коллег')),
    ('отправить отчет', ('от', 'отчет')),
    ('проверить отчёт', ('провер', 'отчёт')),
    ('подготовить презентацию', ('подготов', 'презентац')),
    ('обсудить проект с командой', ('обсуд', 'проект')),
    ('оплатить интернет', ('оплат', 'интернет')),
    ('оплатить аренду', ('оплат', 'аренд')),
    ('получить посылку', ('получ', 'посыл')),
    ('забрать ребёнка из школы', ('заб', 'ребён')),
    ('погулять с собакой', ('погул', 'собак')),
    ('полить цветы', ('пол', 'цвет')),
    ('прочитать книгу', ('проч', 'книг')),
    ('сделать зарядку', ('заряд',)),
    ('постирать одежду', ('стир', 'одежд')),
    ('проверить билет A-102', ('билет', 'a-102')),
    ('купить 2 литра молока', ('2', 'молок')),
    ('оплатить счёт 1500 рублей', ('1500', 'руб')),
    ('обновить версию 1.2.3', ('1.2.3',)),
    ('проверить заказ #A102', ('#a102',)),
    ('обсудить проект Qwen3', ('qwen3',)),
    ('сохранить адрес doctor@example.org', ('doctor@example.org',)),
]

SAFETY_CASES = [
    ('не идти к врачу', ('не', 'врач')),
    ('не ходить в магазин', ('не', 'магазин')),
    ('не покупать молоко', ('не', 'молок')),
    ('отменить запись к врачу', ('отмен', 'врач')),
    ('отменить визит к парикмахеру', ('отмен', 'парикмахер')),
    ('перенести запись к стоматологу', ('перен', 'стоматолог')),
    ('узнать цену стрижки у парикмахера', ('цен', 'парикмахер')),
    ('попросить врача перенести приём', ('перен', 'врач')),
    ('магазин закрыт, никуда не идти', ('закрыт', 'не')),
    ('напомнить отменить покупку', ('отмен', 'покуп')),
]


class NoteTitleVariantsTests(unittest.TestCase):
    def test_contextual_shopping_titles(self):
        for body, expected in SHOP_CASES:
            with self.subTest(body=body):
                self.assertEqual(suggest_note_title(body), expected)

    def test_positive_visits(self):
        for body, expected in VISIT_CASES:
            with self.subTest(body=body):
                self.assertEqual(suggest_note_title(body), expected)

    def test_actions_and_identifiers_preserve_meaning(self):
        for body, stems in ACTION_CASES + SAFETY_CASES:
            with self.subTest(body=body):
                title = suggest_note_title(body).casefold()
                for stem in stems:
                    self.assertIn(stem, title)

    def test_no_false_positive_visit_or_shopping(self):
        for body, _ in SAFETY_CASES:
            with self.subTest(body=body):
                title = suggest_note_title(body)
                self.assertFalse(title.startswith('Визит к '))
                self.assertNotEqual(title, 'Поход в магазин')

    def test_all_titles_bounded_and_deterministic(self):
        records = SHOP_CASES + VISIT_CASES + ACTION_CASES + SAFETY_CASES
        self.assertGreaterEqual(len(records), 80)
        for body, _ in records:
            with self.subTest(body=body):
                title = suggest_note_title(body)
                self.assertTrue(title.strip())
                self.assertLessEqual(len(title), 60)
                self.assertEqual(title, suggest_note_title(body))

    def test_drafts_keep_original_body_and_schedule(self):
        # Independent schedule and event times must not be conflated by title generation.
        records = SHOP_CASES + VISIT_CASES + ACTION_CASES + SAFETY_CASES
        for body, _ in records:
            with self.subTest(body=body):
                response = intent_response(
                    f'Создай напоминание на 15 октября в 12:00, что {body}',
                    1, now=datetime(2026, 10, 12, 9, 0),
                )
                draft = response['action_draft']
                self.assertEqual(draft['body'], ' '.join(body.split()))
                self.assertEqual(draft['once_at'], '2026-10-15T12:00:00')
                self.assertEqual(draft['title'], suggest_note_title(body.strip()))


if __name__ == '__main__':
    unittest.main()

import unittest
from app.services.companion import CompanionEngine
from app.services.local_ai import LocalAIError


class DialogueCompleteness(unittest.TestCase):
    def test_inline_numbered_list_is_complete(self):
        CompanionEngine._validate_dialogue_completeness('1) Документы 2) Одежда 3) Зарядное устройство','Составь список')

    def test_introduction_and_single_item_fail(self):
        for answer in ('Вот список:', '1) Документы'):
            with self.subTest(answer=answer), self.assertRaises(LocalAIError):
                CompanionEngine._validate_dialogue_completeness(answer,'Составь список')

    def test_clarification_is_allowed(self):
        CompanionEngine._validate_dialogue_completeness('Для какой поездки нужен список?','Составь список')

    def test_repeated_paragraphs_do_not_count_as_list_items(self):
        with self.assertRaises(LocalAIError):
            CompanionEngine._validate_dialogue_completeness('Вещи для поездки\n\nВещи для поездки\n\nВещи для поездки','Составь список')

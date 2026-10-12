import unittest
from unittest.mock import patch
from app.services.intent_router import classify_intent, conversational_response
from app.services.companion import CompanionEngine


class IntentRouterTests(unittest.TestCase):
    def test_greetings_and_identity(self):
        for query in ['Привет!', 'Кто ты?', 'Спасибо']:
            with self.subTest(query=query):
                answer = conversational_response(query)
                self.assertEqual(answer['intent'], 'small_talk')
                self.assertEqual(answer['sources'], [])

    def test_help_topics(self):
        cases = {'Как настроить синонимы?': 'synonyms',
                 'Как создать пространство?': 'workspaces',
                 'Как прикрепить контекст?': 'attach_notes',
                 'Какие горячие клавиши есть?': 'quick_actions',
                 'Как подключить ИИ?': 'ai_settings'}
        for query, topic in cases.items():
            with self.subTest(query=query):
                self.assertEqual(conversational_response(query)['help_topic'], topic)
        self.assertIn('пока не поддерживается', conversational_response('Как создать пространство?')['text'])

    def test_note_recall_does_not_use_help(self):
        for query in ['Что я записывал про синонимы?', 'Найди инструкцию как подключить ИИ',
                      'Что в заметке про рабочее пространство?']:
            self.assertEqual(classify_intent(query), 'note_rag')
            self.assertIsNone(conversational_response(query))

    def test_command_and_negation(self):
        self.assertEqual(classify_intent('Напомни проверить отчет каждый понедельник в 10:00'), 'tool_call')
        self.assertNotEqual(classify_intent('Не создавай задачу'), 'tool_call')
        self.assertNotEqual(classify_intent('Составь план'), 'tool_call')

    def test_help_ignores_pins_without_reading_database_or_model(self):
        engine = CompanionEngine('missing.db', client_factory=lambda **kwargs: self.fail('model called'))
        with patch('app.services.companion.MemoryStore', side_effect=AssertionError('database called')):
            result = engine.ask('Как настроить синонимы?', attached_note_ids=[3])
        self.assertEqual(result['intent'], 'app_help')
        self.assertEqual(result['sources'], [])

import tempfile
import unittest
from pathlib import Path
from tools.conversational_quality_gate import run, run_live_tools


class ConversationalQualityGateTests(unittest.TestCase):
    def test_explicit_timezone_is_not_silently_replaced(self):
        from app.services.assistant_actions import intent_response
        for suffix in ('UTC', 'по московскому времени'):
            answer = intent_response('Напомни позвонить завтра в 10:00 ' + suffix, 1)
            self.assertNotIn('action_draft', answer)

    def test_reminder_help_is_local(self):
        from app.services.intent_router import conversational_response
        for query in ('Как настроить напоминание?', 'Где расписание?'):
            answer = conversational_response(query)
            self.assertEqual(answer['help_topic'], 'reminders')

    def test_production_routes_and_tools_pass_without_model_or_database(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'report.json'
            report = run(output)
            self.assertEqual(report['failed'], 0, report['cases'])
            self.assertEqual(len(report['cases']), 11)
            self.assertTrue(output.exists())
            self.assertIn('no live LLM', report['scope'])

    def test_live_tools_reject_malformed_model_json_without_execution(self):
        class Client:
            model = 'fixture'
            def chat(self, *args, **kwargs):
                self.assert_mode = kwargs['json_mode']
                return '{broken'
        client = Client()
        with tempfile.TemporaryDirectory() as folder:
            report = run_live_tools(client, Path(folder) / 'tools.json')
        self.assertEqual(report['failed'], 3)
        self.assertEqual(client.assert_mode, 'tools')

    def test_rrule_schema_excludes_prefix_and_missing_freq(self):
        import re
        from app.services.assistant_actions import TOOL_DEFINITIONS
        pattern = TOOL_DEFINITIONS[1]['function']['parameters']['properties']['recurrence']['pattern']
        self.assertIsNotNone(re.fullmatch(pattern, 'FREQ=DAILY;BYHOUR=15;BYMINUTE=30'))
        self.assertIsNone(re.fullmatch(pattern, 'RRULE:FREQ=DAILY;BYHOUR=15;BYMINUTE=30'))
        self.assertIsNone(re.fullmatch(pattern, 'WEEKLY;BYDAY=SU;BYHOUR=10;BYMINUTE=0'))

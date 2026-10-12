"""Independent context assembly checks with deterministic model fixtures."""
import json
import threading
import unittest

from app.services.local_ai import LocalAICancelled
from tests import test_companion_engine as fixtures


class NoteContextIntegrationTests(unittest.TestCase):
    setUp = fixtures.CompanionTests.setUp
    tearDown = fixtures.CompanionTests.tearDown
    note = fixtures.CompanionTests.note
    facts = staticmethod(fixtures.CompanionTests.facts)

    def test_greeting_does_not_read_pinned_context(self):
        identity = self.note('Selected', 'Встреча во вторник.')
        self.client.reply = self.facts
        result = self.engine.ask('Привет', attached_note_ids=[identity])
        self.assertEqual(result['status'], 'answered')
        self.assertEqual(result['sources'], [])
        self.assertIn('Привет', result['text'])
        self.assertEqual(self.client.messages, [])

    def test_foreign_history_never_enters_pinned_request(self):
        identity = self.note('Selected', 'Разрешённый контекст.')
        self.client.reply = self.facts
        self.engine.ask('Обсуди прикреплённое', attached_note_ids=[identity], history=[
            {'role': 'assistant', 'workspace_id': 2, 'content': 'FOREIGN_HISTORY_CANARY', 'sources': []},
            {'role': 'user', 'workspace_id': 1, 'content': 'Разрешённая история', 'sources': []}])
        payload = json.dumps(self.client.messages[0], ensure_ascii=False)
        self.assertNotIn('FOREIGN_HISTORY_CANARY', payload)
        self.assertIn('Разрешённая история', payload)

    def test_pre_cancelled_request_never_calls_model(self):
        identity = self.note('Selected', 'Контекст.')
        event = threading.Event(); event.set()
        with self.assertRaises(LocalAICancelled):
            self.engine.ask('Вопрос', attached_note_ids=[identity], cancel_event=event)
        self.assertEqual(self.client.messages, [])

    def test_changed_or_moved_pin_during_stream_never_publishes_old_fact(self):
        for change in ('edit', 'move'):
            with self.subTest(change=change):
                identity = self.note('Pinned ' + change, 'OLD_PINNED_FACT_731829')
                packets = []
                store = self.store

                class StreamingClient(fixtures.FakeClient):
                    def chat_stream(client, messages, **kwargs):
                        client.messages.append(messages)
                        packet = json.loads(messages[-1]['content'])
                        raw = json.dumps(fixtures.CompanionTests.facts(packet), ensure_ascii=False)
                        # The prefix is valid JSON-in-progress but cannot yet
                        # authorize a fact. Change its source before completion.
                        split = raw.index('OLD_PINNED_FACT_731829') + 8
                        client.on_stream_content(raw[:split])
                        if change == 'edit':
                            store.save_note(identity, 'Updated', '<p>New current context.</p>', None, False, '')
                        else:
                            store.execute('UPDATE notes SET workspace_id=2 WHERE id=?', (identity,))
                        client.on_stream_content(raw[split:])
                        return raw

                client = StreamingClient()
                self.engine.client_factory = lambda **kwargs: client
                if change == 'move':
                    with self.assertRaises(ValueError):
                        self.engine.ask('Обсуди прикреплённое', attached_note_ids=[identity],
                                        on_segment=packets.append)
                else:
                    result = self.engine.ask('Обсуди прикреплённое', attached_note_ids=[identity],
                                             on_segment=packets.append)
                    self.assertEqual(result['status'], 'search_only')
                    self.assertNotIn('OLD_PINNED_FACT_731829', result['text'])
                self.assertEqual(len(client.messages), 1)
                self.assertFalse(any(packet.get('kind') == 'segment' for packet in packets))
                self.assertTrue(any(packet.get('kind') == 'reset' for packet in packets))


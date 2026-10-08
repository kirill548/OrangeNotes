import unittest
import tempfile
from pathlib import Path
from unittest.mock import Mock, MagicMock, patch
from tools.benchmark_companion import run
from app.services.local_ai import LocalAIError, OllamaClient
from app.database.store import Store
from app.services.memory_store import MemoryStore


class CompanionBenchmark(unittest.TestCase):
    def test_context_safety_contract_suite(self):
        report = run()
        self.assertEqual(report['mode'], 'contract')
        self.assertEqual(len(report['cases']), 7)
        self.assertTrue(report['passed'], report)

    def test_model_failure_does_not_claim_installed_model_is_missing(self):
        from app.services.companion import CompanionEngine
        result = CompanionEngine._fallback([], 'Ответ не прошёл проверку источников.')
        self.assertNotIn('требует установленной', result['text'])
        self.assertIn('результаты поиска', result['text'])

    def test_overload_consumes_shared_deadline_before_any_network_call(self):
        client = OllamaClient()
        client.deadline = 10
        client._opener = Mock()
        with patch('app.services.local_ai.time.monotonic', return_value=11):
            with self.assertRaises(LocalAIError):
                client._request('/api/chat', {'messages': []})
        client._opener.open.assert_not_called()

    def test_late_reply_does_not_turn_into_success(self):
        client = OllamaClient()
        client.deadline = 30
        response = Mock()
        response.read.return_value = b'{"message":{"content":"late"}}'
        client._opener = MagicMock()
        client._opener.open.return_value.__enter__.return_value = response
        with patch('app.services.local_ai.time.monotonic', side_effect=[29, 29, 31]):
            with self.assertRaises(LocalAIError):
                client._request('/api/chat', {'messages': []})

    def test_semantic_false_positive_cannot_substitute_opaque_identifier(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / 'memory.db')
            memory = MemoryStore(store.path)
            try:
                secret = store.create_note()
                store.save_note(secret, 'Личные секреты', '<p>PRIVATE_CANARY_743921</p>', None, False, '')
                concept = store.create_note()
                store.save_note(concept, 'Идея', '<p>Помещение для творчества рядом с домом.</p>', None, False, '')
                def identical_vectors(texts):
                    return [[1.0, 0.0] for _ in texts]
                self.assertEqual(memory.search('КВАЗИОСЦИЛЛЯТОР783921', embedding_provider=identical_vectors), [])
                concepts = memory.search('Место для музыки', embedding_provider=identical_vectors)
                self.assertEqual({row['id'] for row in concepts}, {concept})
                exact = memory.search('PRIVATE_CANARY_743921', embedding_provider=identical_vectors)
                self.assertEqual({row['id'] for row in exact}, {secret})
                # Ordinary title search continues to find an explicitly requested secret note.
                self.assertEqual({row['id'] for row in memory.search('Личные секреты')}, {secret})
            finally:
                memory.close()
                store.db.close()

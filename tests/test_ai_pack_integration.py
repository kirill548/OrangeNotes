from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.services.companion import CompanionEngine
from app.services.local_ai import OllamaClient, LocalAIError
from app.services.local_runtime import ensure_runtime
from app.services.ai_pack_runtime import ManagedPackRuntime
from app.services.ai_onboarding import ModelManager


class PackIntegrationTests(unittest.TestCase):
    def test_managed_companion_uses_database_parent_and_refreshes_endpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = CompanionEngine(Path(directory) / 'notes.sqlite3')
            client = engine._client({'managed_pack': True, 'base_url': 'http://127.0.0.1:11436'})
            self.assertEqual(client.managed_runtime.root, Path(directory) / 'ai_packs')
            with patch.object(ManagedPackRuntime, 'ensure', return_value='http://127.0.0.1:11437') as start, patch.object(client, 'probe') as probe:
                self.assertTrue(ensure_runtime(client))
            self.assertEqual(client.base_url, 'http://127.0.0.1:11437')
            start.assert_called_once_with(None)
            probe.assert_not_called()

    def test_normal_external_client_keeps_endpoint_and_never_uses_pack_runtime(self):
        engine = CompanionEngine('notes.sqlite3')
        client = engine._client({'base_url': 'http://127.0.0.1:11438'})
        self.assertFalse(hasattr(client, 'managed_runtime'))
        with patch.object(client, 'probe', return_value={'available': True}), patch.object(ManagedPackRuntime, 'ensure') as start:
            self.assertTrue(ensure_runtime(client))
        self.assertEqual(client.base_url, 'http://127.0.0.1:11438')
        start.assert_not_called()

    def test_onboarding_cannot_download_into_external_server(self):
        manager = ModelManager({'base_url': 'http://127.0.0.1:11438'})
        with patch('app.services.ai_onboarding._pull') as pull, patch.object(manager.client, 'probe') as probe:
            with self.assertRaises(LocalAIError):
                manager.pull_missing()
        pull.assert_not_called()
        probe.assert_not_called()

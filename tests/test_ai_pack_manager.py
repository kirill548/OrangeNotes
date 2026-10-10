from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from app.services.ai_pack_manager import AIPackManager
from app.services.ai_pack_storage import StorageSafetyError
from app.services.local_ai import LocalAIError, LocalAICancelled
from test_ai_pack_storage import add_model


class FakeRuntime:
    def __init__(self):
        self.events = []
        self.on_stop = None

    def ensure(self, cancel_event=None):
        self.events.append('ensure')
        return 'http://127.0.0.1:11435'

    def stop(self):
        self.events.append('stop')
        if self.on_stop:
            self.on_stop()

    def switch_active_model(self, old, name, cancel_event=None):
        self.events.append(('switch', old, name))
        return 'http://127.0.0.1:11435'


class PackManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'pack'
        self.runtime = FakeRuntime()
        self.manager = AIPackManager(self.root, {'model': 'external:latest'}, self.runtime)
        self.manager.storage.initialize()

    def test_switch_changes_config_after_runtime_success_and_marks_scan_active(self):
        add_model(self.manager.storage.root, 'qwen3:4b', ['a' * 64])
        config = self.manager.switch_active_model('qwen3:4b')
        self.assertEqual(self.runtime.events, [('switch', None, 'qwen3:4b')])
        self.assertEqual(config['model'], 'qwen3:4b')
        self.assertEqual(config['base_url'], 'http://127.0.0.1:11435')
        self.assertTrue(config['managed_pack'])
        self.assertTrue(self.manager.scan()[0]['active'])
        config['model'] = 'mutated'
        self.assertEqual(self.manager.config['model'], 'qwen3:4b')

    def test_failed_switch_preserves_config(self):
        add_model(self.manager.storage.root, 'qwen3:4b', ['a' * 64])
        before = dict(self.manager.config)
        with patch.object(self.runtime, 'switch_active_model', side_effect=LocalAIError('failed')):
            with self.assertRaises(LocalAIError):
                self.manager.switch_active_model('qwen3:4b')
        self.assertEqual(self.manager.config, before)

    def test_switch_passes_previous_managed_model_for_unloading(self):
        add_model(self.manager.storage.root, 'qwen3:8b', ['a' * 64])
        self.manager.config.update(model='qwen3:4b', managed_pack=True)
        self.manager.switch_active_model('qwen3:8b')
        self.assertEqual(self.runtime.events, [('switch', 'qwen3:4b', 'qwen3:8b')])

    def test_missing_or_active_model_cannot_be_deleted_or_activated(self):
        with self.assertRaises(LocalAIError):
            self.manager.switch_active_model('qwen3:4b')
        self.manager.config.update(model='qwen3:4b', managed_pack=True)
        with self.assertRaises(LocalAIError):
            self.manager.delete('qwen3:4b')
        self.assertEqual(self.runtime.events, [])

    def test_cancellation_stops_writer_before_removing_only_new_partials(self):
        blobs = self.manager.storage.root / 'blobs'
        blobs.mkdir()
        old = blobs / ('sha256-' + 'a' * 64 + '-partial')
        old.write_bytes(b'old partial')
        new = blobs / ('sha256-' + 'b' * 64 + '-partial-0')
        complete = blobs / ('sha256-' + 'c' * 64)
        external = Path(self.temp.name) / 'external-model'
        external.write_bytes(b'external model')
        event = threading.Event()

        def pull(client, model, cancel_event, progress, deadline):
            new.write_bytes(b'new partial')
            complete.write_bytes(b'complete blob')
            event.set()
            raise LocalAICancelled('cancelled')

        self.runtime.on_stop = lambda: self.assertTrue(new.exists(), 'cleanup must follow writer shutdown')
        with patch('app.services.ai_onboarding._pull', side_effect=pull):
            with self.assertRaises(LocalAICancelled):
                self.manager.download('qwen3:4b', event)
        self.assertEqual(self.runtime.events, ['ensure', 'stop'])
        self.assertFalse(new.exists())
        self.assertEqual(old.read_bytes(), b'old partial')
        self.assertEqual(complete.read_bytes(), b'complete blob')
        self.assertEqual(external.read_bytes(), b'external model')

    def test_cancelled_before_start_never_calls_runtime_or_pull(self):
        event = threading.Event()
        event.set()
        with patch('app.services.ai_onboarding._pull') as pull:
            with self.assertRaises(LocalAICancelled):
                self.manager.download('qwen3:4b', event)
        pull.assert_not_called()
        self.assertEqual(self.runtime.events, [])

    def test_unowned_models_folder_never_reaches_runtime(self):
        other_root = Path(self.temp.name) / 'unowned-pack'
        models = other_root / 'models'
        models.mkdir(parents=True)
        keep = models / 'keep'
        keep.write_text('external')
        manager = AIPackManager(other_root, {}, self.runtime)
        with patch('app.services.ai_onboarding._pull') as pull:
            with self.assertRaises(StorageSafetyError):
                manager.download('qwen3:4b')
        pull.assert_not_called()
        self.assertEqual(self.runtime.events, [])
        self.assertEqual(keep.read_text(), 'external')

    def test_invalid_paths_are_rejected_before_runtime(self):
        for action in (self.manager.download, self.manager.delete, self.manager.switch_active_model):
            with self.assertRaises((ValueError, LocalAIError)):
                action('../outside')
        self.assertEqual(self.runtime.events, [])

    def test_failed_ensure_does_not_claim_unowned_new_partial(self):
        partial = self.manager.storage.root / 'blobs' / ('sha256-' + 'd' * 64 + '-partial')
        def fail(cancel_event=None):
            partial.parent.mkdir()
            partial.write_bytes(b'other writer')
            raise LocalAIError('cannot own server')
        with patch.object(self.runtime, 'ensure', side_effect=fail), patch('app.services.ai_onboarding._pull') as pull:
            with self.assertRaises(LocalAIError):
                self.manager.download('qwen3:4b')
        pull.assert_not_called()
        self.assertEqual(partial.read_bytes(), b'other writer')

    def test_failed_ownership_verification_never_pulls_wrong_server(self):
        with patch.object(self.runtime, 'verify_owned', create=True, side_effect=LocalAIError('wrong listener')), patch('app.services.ai_onboarding._pull') as pull:
            with self.assertRaises(LocalAIError):
                self.manager.download('qwen3:4b')
        pull.assert_not_called()
        self.assertEqual(self.runtime.events, ['ensure', 'stop'])

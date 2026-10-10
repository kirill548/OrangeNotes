import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app.services.ai_pack_storage import AIPackStorage, StorageSafetyError
from app.services.ai_pack_runtime import ManagedPackRuntime, PackRuntimeError, _registry


class PackRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / 'pack'
        AIPackStorage(self.root / 'models').initialize()
        self.runtime = ManagedPackRuntime(self.root)
        self.process = Mock(pid=123)
        self.process.poll.return_value = None

    def tearDown(self):
        _registry.clear()
        self.temp.cleanup()

    def test_foreign_listener_never_receives_unload(self):
        _registry[self.runtime._key] = (self.process, 45678)
        with patch('app.services.ai_pack_runtime._listener_pids', return_value={999}), patch.object(self.runtime, '_request') as request:
            with self.assertRaises(PackRuntimeError):
                self.runtime.unload('qwen:latest')
            request.assert_not_called()
            self.process.terminate.assert_not_called()

    def test_cancelled_ensure_starts_nothing(self):
        event = threading.Event()
        event.set()
        with patch('app.services.ai_pack_runtime.subprocess.Popen') as spawn:
            with self.assertRaises(PackRuntimeError):
                self.runtime.ensure(event)
            spawn.assert_not_called()

    def test_stop_only_registered_child(self):
        _registry[self.runtime._key] = (self.process, 45678)
        self.runtime.stop()
        self.process.terminate.assert_called_once()
        self.runtime.stop()
        self.process.terminate.assert_called_once()

    def test_marker_required(self):
        (self.root / 'models' / AIPackStorage.MARKER).unlink()
        with self.assertRaises(StorageSafetyError):
            self.runtime.ensure()

    def test_owned_unload_payload(self):
        _registry[self.runtime._key] = (self.process, 45678)
        with patch('app.services.ai_pack_runtime._listener_pids', return_value={123}), patch.object(self.runtime, '_request') as request:
            self.runtime.unload('qwen:latest')
            request.assert_called_once_with(45678, '/api/generate', {'model': 'qwen:latest', 'keep_alive': 0, 'stream': False})

    def test_launch_uses_isolated_models_and_profile(self):
        exe = Path(self.temp.name) / 'ollama'
        exe.write_text('fake')
        with patch('app.services.ai_pack_runtime.runtime_candidates', return_value=[(exe, Path('foreign-models'))]), patch('app.services.ai_pack_runtime.subprocess.Popen', return_value=self.process) as spawn, patch('app.services.ai_pack_runtime._listener_pids', return_value={123}), patch.object(self.runtime, '_request'):
            url = self.runtime.ensure()
            env = spawn.call_args.kwargs['env']
            self.assertEqual(env['OLLAMA_MODELS'], str(self.root / 'models'))
            self.assertEqual(env['HOME'], str(self.root / 'ollama_profile'))
            self.assertNotEqual(url, 'http://127.0.0.1:11434')
            self.assertEqual(self.runtime.verify_owned(), url)

    def test_same_root_reuses_only_registered_owned_process(self):
        _registry[self.runtime._key] = (self.process, 45678)
        with patch('app.services.ai_pack_runtime._listener_pids', return_value={123}), patch.object(ManagedPackRuntime, '_request'), patch('app.services.ai_pack_runtime.subprocess.Popen') as spawn:
            self.assertEqual(ManagedPackRuntime(self.root).ensure(), 'http://127.0.0.1:45678')
            spawn.assert_not_called()

    def test_foreign_listener_during_startup_stops_only_child(self):
        exe = Path(self.temp.name) / 'ollama'
        exe.write_text('fake')
        with patch('app.services.ai_pack_runtime.runtime_candidates', return_value=[(exe, self.root)]), patch('app.services.ai_pack_runtime.subprocess.Popen', return_value=self.process), patch('app.services.ai_pack_runtime._listener_pids', return_value={999}), patch.object(self.runtime, '_request') as request:
            with self.assertRaises(PackRuntimeError):
                self.runtime.ensure()
            request.assert_not_called()
            self.process.terminate.assert_called_once()
            self.assertNotIn(self.runtime._key, _registry)

    def test_switch_unloads_before_returning_new_endpoint(self):
        with patch.object(self.runtime, 'unload') as unload, patch.object(self.runtime, 'ensure', return_value='http://127.0.0.1:45678') as ensure:
            self.assertEqual(self.runtime.switch_active_model('old:latest', 'new:latest'), 'http://127.0.0.1:45678')
            unload.assert_called_once_with('old:latest', None)
            ensure.assert_called_once_with(None)

    def test_existing_foreign_profile_is_rejected(self):
        profile = self.root / 'ollama_profile'
        profile.mkdir()
        (profile / 'foreign-file').write_text('keep')
        with patch('app.services.ai_pack_runtime.subprocess.Popen') as spawn:
            with self.assertRaises(StorageSafetyError):
                self.runtime.ensure()
            spawn.assert_not_called()
        self.assertEqual((profile / 'foreign-file').read_text(), 'keep')

    def test_live_orphan_is_not_adopted_or_stopped(self):
        import json
        self.runtime._record_path.write_text(json.dumps({'owner_pid': 555, 'pid': 666, 'port': 45678}))
        with patch('app.services.ai_pack_runtime._process_alive', return_value=True), patch('app.services.ai_pack_runtime.subprocess.Popen') as spawn:
            with self.assertRaises(PackRuntimeError):
                self.runtime.ensure()
            with self.assertRaises(PackRuntimeError):
                self.runtime.stop()
            spawn.assert_not_called()
        self.assertTrue(self.runtime._record_path.exists())

    def test_stale_record_requires_no_processes_and_no_listener(self):
        import json
        self.runtime._record_path.write_text(json.dumps({'owner_pid': 555, 'pid': 666, 'port': 45678}))
        with patch('app.services.ai_pack_runtime._process_alive', return_value=False), patch('app.services.ai_pack_runtime._listener_pids', return_value={777}):
            with self.assertRaises(PackRuntimeError):
                self.runtime.stop()
        with patch('app.services.ai_pack_runtime._process_alive', return_value=False), patch('app.services.ai_pack_runtime._listener_pids', return_value=set()):
            self.runtime.stop()
        self.assertFalse(self.runtime._record_path.exists())

    def test_log_and_record_symlinks_are_never_written(self):
        target = Path(self.temp.name) / 'outside'
        target.write_text('untouched')
        for name in ('ollama.log', '.notes-ai-pack-process'):
            link = self.root / name
            try:
                link.symlink_to(target)
            except OSError:
                self.skipTest('Symlink creation is unavailable')
            try:
                with patch('app.services.ai_pack_runtime.subprocess.Popen') as spawn:
                    with self.assertRaises(StorageSafetyError):
                        self.runtime.ensure()
                    with self.assertRaises(StorageSafetyError):
                        self.runtime.stop()
                    spawn.assert_not_called()
                self.assertEqual(target.read_text(), 'untouched')
            finally:
                link.unlink()

    def test_stop_removes_record_after_owned_child_exits(self):
        _registry[self.runtime._key] = (self.process, 45678)
        self.runtime._write_record(123, 45678, exclusive=True)
        self.runtime.stop()
        self.assertFalse(self.runtime._record_path.exists())
        self.process.terminate.assert_called_once()


if __name__ == '__main__':
    unittest.main()

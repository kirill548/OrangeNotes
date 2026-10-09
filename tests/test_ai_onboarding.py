import json
from pathlib import Path
import tempfile
import threading
import time
import os
import unittest
from unittest.mock import patch

from app.services.ai_onboarding import ModelManager, PullProgress, REQUIRED_MODELS
from app.services.local_ai import LocalAIError, LocalAICancelled


class ModelSetupTests(unittest.TestCase):
    def test_detect_never_starts_or_pulls(self):
        manager = ModelManager({})
        with patch.object(manager.client, 'probe', return_value={'available': False, 'models': [], 'model_ready': False, 'error': 'offline'}), patch('app.services.ai_onboarding.ensure_runtime') as start, patch('app.services.ai_onboarding._pull') as pull:
            state = manager.detect()
        self.assertEqual(state['missing_models'], list(REQUIRED_MODELS))
        self.assertEqual(state['error'], 'offline')
        start.assert_not_called(); pull.assert_not_called()

    def test_embedding_missing_is_distinct_from_chat(self):
        manager = ModelManager({})
        with patch.object(manager.client, 'probe', return_value={'available': True, 'models': [REQUIRED_MODELS[0]], 'model_ready': True, 'error': None}):
            state = manager.detect()
        self.assertTrue(state['model_ready']); self.assertFalse(state['embedding_ready'])
        self.assertEqual(state['missing_models'], [REQUIRED_MODELS[1]])

    def test_cancelled_detect_does_not_probe(self):
        manager = ModelManager({}); event = threading.Event(); event.set()
        with patch.object(manager.client, 'probe') as probe, self.assertRaises(LocalAICancelled):
            manager.detect(event)
        probe.assert_not_called()

    def test_remote_server_is_rejected(self):
        with self.assertRaises(LocalAIError): ModelManager({'base_url': 'https://external.example'})

    def test_pull_only_missing_allowlisted_models(self):
        manager = ModelManager({})
        states = [{'available': True, 'missing_models': [REQUIRED_MODELS[1]]},
                  {'available': True, 'missing_models': []}]
        with patch.object(manager, 'detect', side_effect=states), patch('app.services.ai_onboarding._pull') as pull:
            result = manager.pull_missing()
        self.assertEqual(pull.call_args.args[1], REQUIRED_MODELS[1])
        self.assertEqual(result['status'], 'installed')

    def test_pull_rejects_unapproved_or_unavailable_service(self):
        manager = ModelManager({})
        for state in [{'available': False}, {'available': True, 'missing_models': ['other:cloud']}]:
            with self.subTest(state=state), patch.object(manager, 'detect', return_value=state), patch('app.services.ai_onboarding._pull') as pull, self.assertRaises(LocalAIError):
                manager.pull_missing()
            pull.assert_not_called()

    def test_connect_pack_accepts_parent_and_runtime_directory(self):
        manager = ModelManager({})
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); exe = root/'runtime/ollama/ollama.exe'; exe.parent.mkdir(parents=True); exe.touch()
            models = root/'runtime/models'; models.mkdir()
            for selected in (root, root/'runtime'):
                with patch.object(manager.client, 'probe', return_value={'available': False}), patch('app.services.ai_onboarding.runtime_candidates', return_value=[(exe, models)]), patch('app.services.ai_onboarding.ensure_runtime', return_value=True) as start, patch.object(manager, 'detect', return_value={'available': True}):
                    result = manager.connect_pack(selected)
                self.assertEqual(result['runtime_root'], str(root.resolve()))
                self.assertEqual(start.call_args.kwargs['runtime_root'], root.resolve())

    def test_pack_candidates_are_canonicalized_before_containment(self):
        import os
        manager = ModelManager({})
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp:
            root = Path(tmp)
            exe = root/'runtime/ollama/ollama.exe'; exe.parent.mkdir(parents=True); exe.touch()
            models = root/'runtime/models'; models.mkdir()
            relative_exe = Path(os.path.relpath(exe, Path.cwd()))
            relative_models = Path(os.path.relpath(models, Path.cwd()))
            with patch.object(manager.client, 'probe', return_value={'available': False}), patch('app.services.ai_onboarding.runtime_candidates', return_value=[(relative_exe, relative_models)]), patch('app.services.ai_onboarding.ensure_runtime', return_value=True), patch.object(manager, 'detect', return_value={'available': True}):
                result = manager.connect_pack(root)
            self.assertEqual(result['runtime_root'], str(root.resolve()))

    def test_pack_permission_error_is_actionable(self):
        manager = ModelManager({})
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); exe = root/'runtime/ollama/ollama.exe'; exe.parent.mkdir(parents=True); exe.touch()
            models = root/'runtime/models'; models.mkdir()
            with patch.object(manager.client, 'probe', return_value={'available': False}), patch('app.services.ai_onboarding.runtime_candidates', return_value=[(exe, models)]), patch('app.services.ai_onboarding.ensure_runtime', side_effect=PermissionError()), self.assertRaisesRegex(LocalAIError, 'правом записи'):
                manager.connect_pack(root)

    def test_pull_cannot_report_success_while_model_is_still_missing(self):
        manager = ModelManager({})
        state = {'available': True, 'missing_models': [REQUIRED_MODELS[0]]}
        with patch.object(manager, 'detect', return_value=state), patch('app.services.ai_onboarding._pull'), self.assertRaisesRegex(LocalAIError, 'пока не появились'):
            manager.pull_missing()

    def test_existing_server_never_claims_selected_pack_is_active(self):
        manager = ModelManager({})
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); exe = root/'runtime/ollama/ollama.exe'; exe.parent.mkdir(parents=True); exe.touch()
            models = root/'runtime/models'; models.mkdir()
            with patch.object(manager.client, 'probe', return_value={'available': True}), patch('app.services.ai_onboarding.runtime_candidates', return_value=[(exe, models)]), patch('app.services.ai_onboarding.ensure_runtime') as start, self.assertRaisesRegex(LocalAIError, 'уже запущен'):
                manager.connect_pack(root)
            start.assert_not_called()
            self.assertIsNone(manager.runtime_root)

    def test_runtime_rejects_explicit_pack_when_server_appears_during_setup(self):
        from app.services.local_runtime import ensure_runtime
        manager = ModelManager({})
        with patch.object(manager.client, 'probe', return_value={'available': True}):
            self.assertTrue(ensure_runtime(manager.client))
            self.assertFalse(ensure_runtime(manager.client, runtime_root=Path('selected-pack')))

    def test_bad_pack_never_starts_server(self):
        manager = ModelManager({})
        with tempfile.TemporaryDirectory() as tmp, patch('app.services.ai_onboarding.ensure_runtime') as start, self.assertRaises(LocalAIError):
            manager.connect_pack(tmp)
        start.assert_not_called()


class PullProtocolTests(unittest.TestCase):
    def test_progress_split_lines_and_success(self):
        events = []; parser = PullProgress(REQUIRED_MODELS[0], events.append)
        parser.feed(b'{"status":"downloading","total":10,')
        parser.feed(b'"completed":4}\n{"status":"success"}')
        parser.finish()
        self.assertEqual(events[0]['percent'], 40)
        self.assertEqual(events[-1]['status'], 'success')

    def test_oversize_line_and_total(self):
        with self.assertRaises(LocalAIError): PullProgress('x').feed(b'x'*(256*1024+1))
        parser = PullProgress('x'); parser.size = 4*1024*1024
        with self.assertRaises(LocalAIError): parser.feed(b'x')

    def test_error_malformed_and_incomplete(self):
        for raw in (b'{"error":"disk full"}\n', b'[]\n', b'{"status":"downloading"}\n', b'{"status":"x","total":true}\n'):
            with self.subTest(raw=raw), self.assertRaises(LocalAIError):
                parser = PullProgress('x'); parser.feed(raw); parser.finish()


class PullCancellationIntegration(unittest.TestCase):
    def test_qt_aborts_silent_local_pull_without_download(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
        from PySide6.QtWidgets import QApplication
        from PySide6.QtCore import QThread
        from PySide6.QtTest import QTest
        from app.services.ai_onboarding import _pull
        app = QApplication.instance() or QApplication([])
        started, release, cancel = threading.Event(), threading.Event(), threading.Event()
        requests = []
        class Silent(BaseHTTPRequestHandler):
            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                requests.append((self.path, payload))
                self.send_response(200); self.send_header('Content-Type', 'application/x-ndjson')
                self.end_headers(); self.wfile.flush(); started.set(); release.wait(3)
            def log_message(self, *args): pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Silent); server.daemon_threads = True
        server_thread = threading.Thread(target=server.serve_forever, daemon=True); server_thread.start()
        errors = []
        class Worker(QThread):
            def run(self):
                manager = ModelManager({'base_url': 'http://127.0.0.1:'+str(server.server_port)})
                try: _pull(manager.client, REQUIRED_MODELS[0], cancel, None, time.monotonic()+1800)
                except Exception as error: errors.append(error)
        worker = Worker()
        try:
            worker.start(); end = time.monotonic()+2
            while not started.is_set() and time.monotonic()<end: QTest.qWait(10)
            self.assertTrue(started.is_set())
            before = time.perf_counter(); cancel.set()
            while worker.isRunning() and time.perf_counter()-before<1: QTest.qWait(10)
            self.assertFalse(worker.isRunning(), 'Qt reply must abort a silent connection promptly')
            self.assertLess(time.perf_counter()-before, 1)
            self.assertEqual(len(errors), 1)
            self.assertIsInstance(errors[0], LocalAICancelled)
            self.assertEqual(requests, [('/api/pull', {'model': REQUIRED_MODELS[0], 'stream': True})])
        finally:
            cancel.set(); release.set(); worker.wait(3000)
            server.shutdown(); server.server_close(); app.processEvents()

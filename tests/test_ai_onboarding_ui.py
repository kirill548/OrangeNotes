"""First-use AI setup checks: fake workers only, no downloads or model launch."""
import threading
import time
import unittest
from PySide6.QtCore import QThread, Signal, QCoreApplication, QEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from app.ui.ai_onboarding import AIOnboardingWizard


class OnboardingUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.calls = []
        self.dialogs = []
        self.delay = False
        self.cancelled = threading.Event()
        self.status = dict(available=False, model_ready=False, embedding_ready=False,
                           models=[], missing_models=['qwen3:4b', 'qwen3-embedding:0.6b'], model='qwen3:4b', transport='NDJSON stream=true', latency_ms=12)

    def factory(self, kind, config, path=''):
        suite = self
        suite.calls.append((kind, dict(config), path))
        class Worker(QThread):
            completed = Signal(int, object, object)
            progress = Signal(int, object)
            def __init__(self):
                super().__init__()
                self.cancel_event = threading.Event()
            def run(self):
                self.progress.emit(0, dict(status='downloading', completed=50, total=100))
                if suite.delay:
                    self.cancel_event.wait(2)
                if self.cancel_event.is_set():
                    suite.cancelled.set()
                    self.completed.emit(0, None, 'Cancelled')
                else:
                    self.completed.emit(0, dict(suite.status), None)
        return Worker()

    def dialog(self, **kwargs):
        dialog = AIOnboardingWizard(dict(provider='ollama', base_url='http://127.0.0.1:11434',
                                        model='qwen3:4b', embedding_model='qwen3-embedding:0.6b'),
                                    worker_factory=self.factory, initial_status=dict(self.status), **kwargs)
        self.dialogs.append(dialog)
        dialog.show()
        self.app.processEvents()
        return dialog

    def wait(self, predicate):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return
            QTest.qWait(5)
        self.fail('Onboarding worker did not settle')

    def tearDown(self):
        for dialog in self.dialogs:
            for worker in dialog.findChildren(QThread):
                worker.cancel_event.set()
                worker.wait(3000)
            dialog.close()
            dialog.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()

    def test_missing_server_prevents_download_without_implicit_action(self):
        dialog = self.dialog()
        self.assertFalse(dialog.pull_button.isEnabled())
        self.assertFalse(any(kind == 'pull' for kind, _, _ in self.calls))
        self.assertTrue(dialog.pack_button.isEnabled())
        self.assertTrue(dialog.skip_button.isEnabled())

    def test_ready_server_missing_model_allows_explicit_download(self):
        self.status.update(available=True)
        dialog = self.dialog()
        self.assertTrue(dialog.pull_button.isEnabled())
        self.assertEqual(self.calls, [])
        self.status.update(model_ready=True, embedding_ready=True)
        dialog.pull_button.click()
        self.wait(lambda: len(self.calls) == 1)
        self.wait(lambda: dialog.check_button.isEnabled())
        self.assertEqual(self.calls[0][0], 'pull')

    def test_detection_does_not_modify_original_configuration(self):
        dialog = self.dialog()
        original = dict(dialog.config)
        configured = []
        dialog.configured.connect(configured.append)
        dialog.check_button.click()
        self.wait(lambda: dialog.check_button.isEnabled())
        self.assertEqual(self.calls[0][0], 'detect')
        self.assertEqual(dialog.config, original)
        self.assertEqual(configured, [])

    def test_cancel_download_sets_worker_event_and_keeps_ui_usable(self):
        self.status.update(available=True)
        self.delay = True
        dialog = self.dialog()
        dialog.pull_button.click()
        self.wait(lambda: dialog.cancel_button.isEnabled())
        dialog.cancel_button.click()
        self.wait(self.cancelled.is_set)
        self.wait(lambda: dialog.check_button.isEnabled())
        self.assertTrue(dialog.skip_button.isEnabled())

    def test_skip_setup_emits_search_choice_without_downloading(self):
        dialog = self.dialog()
        skipped = []
        dialog.skipped.connect(lambda: skipped.append(True))
        dialog.skip_button.click()
        self.wait(lambda: bool(skipped))
        self.assertFalse(any(kind == 'pull' for kind, _, _ in self.calls))

    def test_ready_connection_has_transport_and_latency_information(self):
        self.status.update(available=True, model_ready=True, embedding_ready=True)
        dialog = self.dialog()
        labels = ' '.join(label.text() for label in
                          (dialog.server_label, dialog.model_label, dialog.embedding_label, dialog.details_label))
        self.assertIn('qwen3:4b', labels)
        self.assertIn('NDJSON', labels)


    def test_skip_during_download_waits_for_cancel_before_closing(self):
        self.status.update(available=True)
        self.delay = True
        dialog = self.dialog()
        skipped = []
        dialog.skipped.connect(lambda: skipped.append(True))
        dialog.pull_button.click()
        dialog.skip_button.click()
        self.wait(lambda: bool(skipped))
        self.assertTrue(self.cancelled.is_set())
        self.assertFalse(dialog.busy)
        self.assertEqual(skipped, [True])

    def test_ready_model_without_embedding_does_not_block_conversation(self):
        self.status.update(available=True, model_ready=True, embedding_ready=False)
        dialog = self.dialog()
        self.assertTrue(dialog.finish_button.isEnabled())
        self.assertIn('обычный поиск доступен', dialog.embedding_label.text())
        configured = []
        dialog.configured.connect(configured.append)
        dialog.finish_button.click()
        self.assertEqual(len(configured), 1)
        self.assertTrue(configured[0]['onboarding_seen'])
        self.assertEqual(configured[0]['provider'], 'ollama')

    def test_error_content_is_plain_text(self):
        from PySide6.QtCore import Qt
        self.status['error'] = '<b>connection error</b>'
        dialog = self.dialog()
        self.assertEqual(dialog.status_label.textFormat(), Qt.PlainText)
        self.assertIn('<b>connection error</b>', dialog.status_label.text())

    def test_empty_pack_path_gives_actionable_message_without_worker(self):
        dialog = self.dialog()
        dialog.pack_button.click()
        self.assertEqual(self.calls, [])
        self.assertIn('Выберите папку', dialog.status_label.text())

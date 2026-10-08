"""Production companion onboarding integration with fully fake local services."""
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from app.database.store import Store
from app.ui.companion import CompanionDialog


class OnboardingIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'notes.sqlite3')
        self.dialogs = []
        self.calls = []
        self.main_thread = threading.get_ident()
        self.status = dict(available=False, model_ready=False, embedding_ready=False,
                           missing_models=['qwen3:4b', 'qwen3-embedding:0.6b'], latency_ms=7)
        suite = self
        class Manager:
            def __init__(self, config):
                suite.calls.append(('construct', dict(config), threading.get_ident()))
            def detect(self, cancel_event):
                suite.calls.append(('detect', None, threading.get_ident()))
                return dict(suite.status)
            def connect_pack(self, path, cancel_event):
                suite.calls.append(('pack', path, threading.get_ident()))
                return dict(suite.status)
            def pull_missing(self, cancel_event, progress):
                suite.calls.append(('pull', None, threading.get_ident()))
                progress(dict(model='qwen3:4b', status='pulling', percent=42))
                return dict(suite.status)
        self.manager_patch = patch('app.services.ai_onboarding.ModelManager', Manager)
        self.manager_patch.start()
        self.runtime_patch = patch('app.services.local_runtime.ensure_runtime', side_effect=AssertionError('Implicit runtime launch'))
        self.runtime_patch.start()

    def wait(self, predicate):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return
            QTest.qWait(5)
        self.fail('Production onboarding did not settle')

    def dialog(self):
        dialog = CompanionDialog(self.store)
        self.dialogs.append(dialog)
        dialog.show()
        self.wait(lambda: not dialog.busy and any(call[0] == 'detect' for call in self.calls))
        return dialog

    def wizard(self, dialog):
        self.wait(lambda: dialog._onboarding_dialog is not None)
        return dialog._onboarding_dialog

    def tearDown(self):
        for dialog in self.dialogs:
            if dialog._onboarding_dialog:
                wizard = dialog._onboarding_dialog
                if wizard._thread:
                    wizard._thread.cancel_event.set()
                    wizard._thread.wait(3000)
                wizard.close()
            dialog.request_close()
            self.wait(lambda: not dialog.busy)
            dialog.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()
        self.runtime_patch.stop()
        self.manager_patch.stop()
        self.store.db.close()
        self.temp.cleanup()

    def test_first_open_detects_without_launch_or_download_off_gui_thread(self):
        dialog = self.dialog()
        wizard = self.wizard(dialog)
        self.assertIn('не подключён', dialog.connection_banner.text())
        self.assertFalse(wizard.pull_button.isEnabled())
        self.assertEqual([call[0] for call in self.calls], ['construct', 'detect'])
        self.assertNotEqual(self.calls[1][2], self.main_thread)
        self.assertFalse(dialog.settings_path.exists())

    def test_skip_persists_search_choice_and_does_not_reopen_setup(self):
        dialog = self.dialog()
        self.wizard(dialog).skip_button.click()
        saved = json.loads(dialog.settings_path.read_text(encoding='utf-8'))
        self.assertEqual(saved['provider'], 'search')
        self.assertTrue(saved['onboarding_seen'])
        second = CompanionDialog(self.store)
        self.dialogs.append(second)
        second.show()
        QTest.qWait(70)
        self.app.processEvents()
        self.assertIsNone(second._onboarding_dialog)
        self.assertEqual(second.config['provider'], 'search')

    def test_ready_without_embedding_has_status_and_no_forced_setup(self):
        self.status.update(available=True, model_ready=True)
        dialog = self.dialog()
        self.assertIsNone(dialog._onboarding_dialog)
        self.assertIn('NDJSON stream=true', dialog.connection_banner.text())
        self.assertIn('поиск по смыслу ещё не настроен', dialog.connection_banner.text())
        dialog.show_settings()
        self.assertIn('NDJSON', dialog._settings_dialog.result_label.text())

    def test_explicit_pack_ready_choice_persists_pack_path(self):
        dialog = self.dialog()
        wizard = self.wizard(dialog)
        self.status.update(available=True, model_ready=True, embedding_ready=True, missing_models=[])
        pack = str(Path(self.temp.name) / 'AI-pack')
        wizard.pack_path.setText(pack)
        wizard.pack_button.click()
        self.wait(lambda: not wizard.busy)
        self.assertTrue(wizard.finish_button.isEnabled())
        self.assertFalse(dialog.settings_path.exists())
        wizard.finish_button.click()
        saved = json.loads(dialog.settings_path.read_text(encoding='utf-8'))
        self.assertEqual(saved['ai_pack_path'], pack)
        self.assertTrue(saved['onboarding_seen'])
        self.assertEqual(saved['provider'], 'ollama')
        self.assertIn('ИИ подключён', dialog.status.text())

    def test_real_model_manager_progress_shape_updates_percent(self):
        dialog = self.dialog()
        wizard = self.wizard(dialog)
        class Pending:
            cancel_event = threading.Event()
        wizard._thread = Pending()
        try:
            wizard._progress(0, dict(model='qwen3:4b', status='pulling manifest', percent=42))
            self.assertEqual(wizard.progress.maximum(), 100)
            self.assertEqual(wizard.progress.value(), 42)
            self.assertIn('qwen3:4b', wizard.status_label.text())
        finally:
            wizard._thread = None

    def blocking_pull(self):
        started, cancelled, release = threading.Event(), threading.Event(), threading.Event()
        def pull(manager, cancel_event, progress):
            started.set()
            if cancel_event.wait(2):
                cancelled.set()
            release.wait(2)
            raise RuntimeError('cancelled')
        return started, cancelled, release, pull

    def test_close_during_setup_retains_worker_until_shutdown_ready(self):
        dialog = self.dialog()
        wizard = self.wizard(dialog)
        wizard._apply_status({**self.status, 'available': True})
        started, cancelled, release, pull = self.blocking_pull()
        # Patch the exact fake manager class currently installed in production worker.
        from app.services.ai_onboarding import ModelManager
        shutdown = []
        dialog.shutdown_ready.connect(lambda: shutdown.append(True))
        with patch.object(ModelManager, 'pull_missing', pull):
            wizard.pull_button.click()
            self.wait(started.is_set)
            dialog.request_close()
            self.wait(cancelled.is_set)
            self.assertTrue(dialog.busy)
            self.assertFalse(dialog.isVisible())
            self.assertEqual(shutdown, [])
            release.set()
            self.wait(lambda: not dialog.busy and bool(shutdown))
        self.assertEqual(shutdown, [True])
        self.assertFalse(wizard.busy)

    def test_cancel_setup_reaches_settled_status(self):
        dialog = self.dialog()
        wizard = self.wizard(dialog)
        wizard._apply_status({**self.status, 'available': True})
        started, cancelled, release, pull = self.blocking_pull()
        from app.services.ai_onboarding import ModelManager
        with patch.object(ModelManager, 'pull_missing', pull):
            wizard.pull_button.click()
            self.wait(started.is_set)
            wizard.cancel_button.click()
            self.wait(cancelled.is_set)
            self.assertTrue(wizard.busy)
            release.set()
            self.wait(lambda: not wizard.busy)
        self.assertIn('Операция отменена', wizard.status_label.text())
        self.assertNotIn('Отменяю', wizard.status_label.text())
        self.assertTrue(wizard.check_button.isEnabled())

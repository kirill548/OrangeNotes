import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from app.database.store import Store
from app.ui.reminder_status import ReminderStatusDialog


class ReminderStatusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])
        cls.app.setFont(QFont('Segoe UI',10))

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.store=Store(Path(self.temp.name)/'notes.sqlite3')
        self.dialog=ReminderStatusDialog(self.store)

    def tearDown(self):
        self.dialog.close()
        self.dialog.deleteLater()
        self.app.processEvents()
        self.store.db.close()
        self.temp.cleanup()

    def heartbeat(self, age):
        (self.store.path.parent/'worker_status.json').write_text(json.dumps({
            'running':True,'heartbeat_utc':(datetime.now(timezone.utc)-timedelta(seconds=age)).isoformat()}),encoding='utf-8')
        self.dialog.refresh()

    def test_stale_heartbeat_never_claims_worker_is_running(self):
        self.heartbeat(600)
        self.assertIn('Нет свежего отклика',self.dialog.service.text())
        self.heartbeat(0)
        self.assertIn('Служба работает',self.dialog.service.text())

    def test_empty_history_and_nonmodal_escape(self):
        self.assertIn('Ожидают: 0',self.dialog.counts.text())
        self.assertEqual(self.dialog.windowModality(),Qt.NonModal)
        self.dialog.show();self.dialog.activateWindow();QTest.qWait(80)
        QTest.keyClick(self.dialog,Qt.Key_Escape)
        self.assertFalse(self.dialog.isVisible())

    def test_corrupt_status_is_readable_and_errors_are_plain_text(self):
        (self.store.path.parent/'worker_status.json').write_text('{broken',encoding='utf-8')
        self.dialog.refresh()
        self.assertIn('Нет данных',self.dialog.service.text())
        self.assertEqual(self.dialog.delivery.textFormat(),Qt.PlainText)

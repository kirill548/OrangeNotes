import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timedelta
from PySide6.QtWidgets import QApplication
from app.database.store import Store
from app.services.scheduler import Scheduler
from app.ui.window import Window


class ReminderVisibility(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_overdue_and_snoozed_reminder_remain_visible(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / 'notes.db')
            window = Window(store, background_managed=True)
            try:
                now = datetime.now()
                note = store.create_note()
                store.save_note(note, '', '<p>Посмотреть почту</p>', None, False, '')
                store.save_reminder(note, 'once', (now + timedelta(minutes=1)).isoformat(), [], [])
                store.execute('UPDATE reminders SET created_utc=NULL,created_at=?,once_at=?',
                              ((now - timedelta(minutes=2)).isoformat(), (now - timedelta(minutes=1)).isoformat()))
                Scheduler(store, lambda *args: None).tick()
                window.scope = ('reminders', None)
                window.refresh_list()
                self.assertEqual(window.notes.count(), 1)
                window.load_note(note)
                window.update_reminder_label()
                self.assertIn('Не отмечено выполненным', window.reminder_label.text())
                event = store.notification_event(store.rows('SELECT id FROM reminder_events')[0][0])
                store.handle_notification_action(event['id'], event['token'], 'snooze')
                window.refresh_list()
                window.update_reminder_label()
                self.assertEqual(window.notes.count(), 1)
                self.assertIn('Отложено до', window.reminder_label.text())
            finally:
                window.clock.stop()
                window.startup_tick.stop()
                window.tray.hide()
                window.deleteLater()
                self.app.processEvents()
                store.db.close()

import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from app.database.store import Store
from app.ui.window import Window


class CompanionWindowFlowTests(unittest.TestCase):
    def test_first_user_greeting_create_reminder_and_pet_dock(self):
        app=QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            store=Store(Path(directory)/'notes.sqlite3')
            window=Window(store,background_managed=True)
            window.clock.stop();window.show();app.processEvents()
            try:
                self.assertEqual(window.splitter.count(),3)
                pet=window._mini_companion
                self.assertIs(pet.parentWidget(),window.companion_dock)
                self.assertFalse(pet.geometry().intersects(window.notes.geometry()))
                QTest.keyClick(pet,Qt.Key_Return)
                dialog=window._companion_dialog
                dialog.config['provider']='search'
                def ask(text):
                    dialog.input.setPlainText(text);dialog.send()
                    deadline=time.monotonic()+5
                    while dialog.busy and time.monotonic()<deadline:
                        app.processEvents()
                        time.sleep(.01)
                    self.assertFalse(dialog.busy)
                ask('Привет!')
                self.assertIn('Привет!',dialog.chat.toPlainText())
                self.assertNotIn('не нашёл',dialog.chat.toPlainText())
                ask('Создай заметку посмотреть почту завтра в 09:00')
                self.assertIsNotNone(dialog._action_draft)
                self.assertEqual(store.rows('SELECT COUNT(*) FROM notes')[0][0],0)
                QTest.mouseClick(dialog.draft_create,Qt.LeftButton)
                self.assertEqual(store.rows('SELECT COUNT(*) FROM notes')[0][0],1)
                note=store.rows('SELECT * FROM notes')[0]
                self.assertEqual(window.current,note['id'])
                reminder=store.reminder(note['id'])
                self.assertEqual(datetime.fromisoformat(reminder['once_at']).date(),(datetime.now()+timedelta(days=1)).date())
                self.assertEqual(pet.bubble.text(),'Заметка и напоминание созданы.')
                QTest.mouseClick(pet.dismiss,Qt.LeftButton)
                self.assertFalse(window.companion_dock.isVisible())
                window.show_companion();app.processEvents()
                self.assertTrue(window.companion_dock.isVisible())
            finally:
                if window._companion_dialog:window._companion_dialog.close()
                window.quitting=True;window.tray.hide();window.close();window.deleteLater();app.processEvents();store.db.close()

import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from PySide6.QtCore import Qt, QCoreApplication, QEvent, QPoint
from PySide6.QtGui import QContextMenuEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from app.database.store import Store
from app.ui.window import Window


class CompanionWindowFlowTests(unittest.TestCase):
    def test_first_user_greeting_create_reminder_and_desktop_pet(self):
        app=QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            store=Store(Path(directory)/'notes.sqlite3')
            window=Window(store,background_managed=True)
            window.clock.stop();window.show();app.processEvents()
            try:
                self.assertEqual(window.splitter.count(),3)
                pet=window._mini_companion
                self.assertIsNone(pet.parentWidget())
                self.assertFalse(pet.isVisible())
                QTest.mouseClick(window.pet_toggle,Qt.LeftButton)
                self.assertIsNone(window._companion_dialog)
                self.assertTrue(window.app_menu_button.menu().pet_action.isChecked())
                self.assertTrue(pet.isVisible())
                window.showMinimized()
                app.processEvents()
                self.assertTrue(pet.isVisible())
                point = QPoint(100, 90)
                target = pet.mapToGlobal(point) - QPoint(100, 40)
                QTest.mousePress(pet, Qt.LeftButton, pos=point)
                QTest.mouseMove(pet, pet.mapFromGlobal(target))
                QTest.mouseRelease(pet, Qt.LeftButton, pos=pet.mapFromGlobal(target))
                self.assertTrue(window.isMinimized())
                self.assertIsNone(window._companion_dialog)
                window.showNormal()
                app.processEvents()
                QTest.mouseClick(window.pet_toggle,Qt.LeftButton)
                self.assertFalse(pet.isVisible())
                QTest.mouseClick(window.companion_button,Qt.LeftButton)
                self.assertFalse(pet.isVisible())
                dialog=window._companion_dialog
                self.assertTrue(dialog.isVisible())
                dialog.config['provider']='search'
                pet_action = window.app_menu_button.menu().pet_action
                self.assertFalse(pet_action.isChecked())
                pet_action.trigger()
                self.assertTrue(pet_action.isChecked())
                self.assertTrue(pet.isVisible())
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
                point = QPoint(100, 90)
                app.sendEvent(pet, QContextMenuEvent(QContextMenuEvent.Mouse, point, pet.mapToGlobal(point)))
                actions = pet._pet_menu.actions()
                self.assertEqual([action.text() for action in actions], ['Скрыть питомца'])
                self.assertFalse(actions[0].icon().isNull())
                actions[0].trigger()
                self.assertFalse(pet.isVisible())
                self.assertFalse(pet_action.isChecked())
                self.assertFalse(window.pet_toggle.isChecked())
                self.assertTrue(dialog.isVisible())
                dialog.close()
                QTest.mouseClick(window.companion_button, Qt.LeftButton)
                self.assertTrue(dialog.isVisible())
                self.assertFalse(pet.isVisible())
                pet_action.trigger()
                self.assertTrue(pet.isVisible())
                pet_action.trigger()
                self.assertFalse(pet.isVisible())
            finally:
                if window._companion_dialog:window._companion_dialog.close()
                # A close can be deferred while saving. Explicitly stop DB
                # readers before releasing the fixture's SQLite owner, then
                # flush deferred deletion (processEvents alone does not do it).
                window.stop_knowledge_workers()
                window.quitting=True;window.tray.hide();window.close();window.deleteLater()
                QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)
                app.processEvents();store.db.close()

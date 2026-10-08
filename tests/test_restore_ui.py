import tempfile
import unittest
from pathlib import Path
from contextlib import nullcontext
from unittest.mock import patch
from PySide6.QtWidgets import QApplication
from app.database.store import Store
from app.ui.window import Window
from app.ui.database_restore_dialog import RestoreDialog


class RestoreUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def test_restore_rebinds_state_and_resumes_clock(self):
        with tempfile.TemporaryDirectory() as directory:
            store=Store(Path(directory)/'current.sqlite3')
            backup=Store(Path(directory)/'backup.sqlite3')
            note=backup.create_note();backup.save_note(note,'Restored','text',None,False,'');backup.db.close()
            with patch.object(Window,'tick',lambda self:None):window=Window(store,background_managed=True)
            window.new_note();window.body.setPlainText('Latest current edit')
            try:
                with patch('app.services.platform_background.pause_database_worker',return_value=nullcontext()) as paused:
                    self.assertTrue(window._perform_database_restore(Path(directory)/'backup.sqlite3'))
                    paused.assert_called_once_with(store.path)
                self.assertTrue(window.clock.isActive())
                self.assertIs(window.scheduler.store,store)
                self.assertIs(window.day_focus.store,store)
                self.assertEqual(store.rows('SELECT title FROM notes')[0][0],'Restored')
                self.assertIsNone(window._undo)
                self.assertFalse(window._dirty)
            finally:
                window.clock.stop();window.startup_tick.stop();window.debounce.stop();window.quitting=True;window.tray.hide();window.close();window.deleteLater();self.app.processEvents();store.db.close()

    def test_styled_confirmation_explicit_callback(self):
        dialog=RestoreDialog({'path':Path('copy.sqlite3'),'notes':7},lambda:True,None)
        self.assertEqual(dialog.done_button.text(),'Отмена')
        self.assertEqual(dialog.restore_button.text(),'Восстановить из копии')
        dialog.close();dialog.deleteLater();self.app.processEvents()

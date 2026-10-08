import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PySide6.QtWidgets import QApplication, QInputDialog
from app.database.store import Store
from app.ui.window import Window


class FolderAssignmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def test_assignment_persists_and_badge_is_not_editable(self):
        with tempfile.TemporaryDirectory() as directory:
            store=Store(Path(directory)/'notes.db')
            window=Window(store,background_managed=True)
            window.clock.stop()
            try:
                window.new_note()
                self.assertEqual(window.folder.label.text(),'Без папки')
                self.assertFalse(window.folder.model.isVisible())
                with patch.object(QInputDialog,'getText',return_value=('Проект',True)):
                    window.create_and_assign_folder()
                folder_id=window.folder.currentData()
                self.assertEqual(window.folder.label.text(),'Проект')
                self.assertEqual(store.rows('SELECT folder_id FROM notes WHERE id=?',(window.current,))[0][0],folder_id)
                window.folder.setCurrentIndex(0)
                window.save()
                window.load_note(window.current)
                self.assertEqual(window.folder.label.text(),'Без папки')
                self.assertIsNone(window.folder.currentData())
            finally:
                window.quitting=True
                window.tray.hide()
                window.close()
                store.db.close()

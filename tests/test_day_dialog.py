"""My Day suggestions UI; data and windows are isolated from the user's app."""
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication,QLabel,QMainWindow,QMessageBox
from app.database.store import Store
from app.services.day_focus import DayFocus
from app.ui.day_dialog import DaySuggestionsDialog


class DayDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        scratch=Path(__file__).resolve().parents[3]/'work';scratch.mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(prefix='day-dialog-tests-',dir=scratch)
        self.store=Store(Path(self.temp.name)/'notes.sqlite3')
        self.parent=QMainWindow();self.parent.resize(1100,740);self.parent.show()
        self.app.processEvents()
        self.dialogs=[]

    def tearDown(self):
        for dialog in self.dialogs:dialog.close();dialog.deleteLater()
        self.parent.close();self.parent.deleteLater()
        self.app.processEvents()
        self.store.db.close();self.temp.cleanup()

    def note(self,title='Тестовая заметка',body='<p>☐ Купить &lt;tag&gt; 🧡</p>'):
        note=self.store.create_note(favorite=True)
        self.store.save_note(note,title,body,None,True,'важно')
        return note

    def dialog(self):
        dialog=DaySuggestionsDialog(self.store,self.parent);self.dialogs.append(dialog)
        dialog.show();self.app.processEvents()
        return dialog

    def snapshot_originals(self):
        return {table:[tuple(row) for row in self.store.rows('SELECT * FROM '+table+' ORDER BY rowid')]
                for table in ('notes','folders','tags','note_tags','reminders','reminder_days','reminder_times','reminder_events')}

    def test_first_use_is_nonmodal_clear_and_does_not_resize_parent(self):
        self.note()
        geometry=self.parent.geometry();state=self.parent.windowState()
        dialog=self.dialog()
        self.assertFalse(dialog.isModal())
        self.assertEqual(dialog.windowModality(),Qt.NonModal)
        self.assertGreaterEqual(dialog.minimumWidth(),460)
        self.assertEqual(self.parent.geometry(),geometry)
        self.assertEqual(self.parent.windowState(),state)
        self.assertIn('полночь',dialog.help_text.text())
        self.assertIn('сохраняются',dialog.help_text.text())
        self.assertTrue(dialog.scroll.widgetResizable())

    def test_title_fallback_plain_preview_and_reason(self):
        nid=self.note(title='   ')
        dialog=self.dialog()
        texts=[label.text() for label in dialog.findChildren(QLabel)]
        self.assertIn('Без названия',texts)
        self.assertTrue(any('☐ Купить <tag> 🧡' in text for text in texts))
        self.assertIn(nid,dialog.add_buttons)
        self.assertTrue(any('избран' in text.lower() for text in texts),texts)
        title_labels=[label for label in dialog.findChildren(QLabel) if label.text()=='Без названия']
        self.assertEqual(title_labels[0].textFormat(),Qt.PlainText)

    def test_add_button_updates_selection_removes_suggestion_and_emits_once(self):
        nid=self.note()
        self.store.save_reminder(nid,'repeat',None,[0,1,2,3,4,5,6],['09:00','18:30'])
        before=self.snapshot_originals()
        dialog=self.dialog();changed=[];dialog.changed.connect(lambda:changed.append(True))
        button=dialog.add_buttons[nid]
        QTest.mouseClick(button,Qt.LeftButton);self.app.processEvents()
        self.assertEqual(DayFocus(self.store).selected_ids(),[nid])
        self.assertNotIn(nid,dialog.add_buttons)
        self.assertEqual(len(changed),1)
        self.assertFalse(dialog._add_note(nid))
        self.assertEqual(len(changed),1)
        self.assertEqual(self.snapshot_originals(),before)

    def test_add_error_keeps_row_enabled_and_data(self):
        nid=self.note();dialog=self.dialog();before=self.snapshot_originals()
        with patch.object(dialog.focus,'add',side_effect=sqlite3.OperationalError('database locked')),patch.object(QMessageBox,'warning') as warning:
            self.assertFalse(dialog._add_note(nid))
        warning.assert_called_once()
        self.assertTrue(dialog.add_buttons[nid].isEnabled())
        self.assertEqual(DayFocus(self.store).selected_ids(),[])
        self.assertEqual(self.snapshot_originals(),before)

    def test_deleted_source_during_open_does_not_enter_my_day(self):
        nid=self.note();dialog=self.dialog()
        self.store.execute('UPDATE notes SET deleted=1 WHERE id=?',(nid,))
        with patch.object(QMessageBox,'warning') as warning:
            self.assertFalse(dialog._add_note(nid))
        warning.assert_called_once()
        self.assertEqual(DayFocus(self.store).selected_ids(),[])
        self.assertTrue(dialog.refresh())
        self.assertNotIn(nid,dialog.add_buttons)

    def test_many_suggestions_scroll_and_preserve_maximized_parent(self):
        for number in range(25):self.note('Заметка '+str(number))
        self.parent.showMaximized();self.app.processEvents()
        geometry=self.parent.geometry();state=self.parent.windowState()
        dialog=self.dialog()
        self.assertGreater(len(dialog.add_buttons),5)
        self.assertEqual(self.parent.geometry(),geometry)
        self.assertEqual(self.parent.windowState(),state)
        dialog.resize(520,340);self.app.processEvents()
        self.assertGreater(dialog.scroll.verticalScrollBar().maximum(),0)


if __name__=='__main__':unittest.main(verbosity=2)

"""Run with: python -m unittest discover -s tests -v (from notes_app)."""
import os
import sys
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from contextlib import closing

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import Qt, QTime, QCoreApplication, QEvent, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton
from app.database.store import Store
from app.ui.window import Window


class UserRegressions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.directory.name) / 'notes.db')
        self.window = Window(self.store)
        self.window.clock.stop()
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        if self.window.panel:
            self.window.panel.hide()
        self.window.quitting = True
        self.window.tray.hide()
        self.window.close()
        self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()
        self.store.db.close()
        self.directory.cleanup()

    def test_real_weekday_clicks_and_multiple_times(self):
        w = self.window
        w.new_note()
        w.edit_reminder()
        panel = w.panel
        panel.mode.setCurrentIndex(3)
        self.app.processEvents()
        for checkbox in panel.days:
            checkbox.setChecked(False)
        for day in (0, 2, 4):
            checkbox = panel.days[day]
            QTest.mouseClick(checkbox, Qt.LeftButton, pos=checkbox.rect().center())
        QTest.mouseClick(panel.add_button, Qt.LeftButton)
        panel.times[0].setTime(QTime(9, 0))
        panel.times[1].setTime(QTime(18, 30))
        panel.validate()
        reminder = self.store.reminder(w.current)
        self.assertEqual(reminder['days'], [0, 2, 4])
        self.assertEqual(reminder['times'], ['09:00', '18:30'])

    def test_enter_does_not_close_and_once_does_not_become_daily(self):
        w = self.window
        w.new_note()
        w.edit_reminder()
        panel = w.panel
        QTest.mouseClick(panel.add_button, Qt.LeftButton)
        self.assertEqual(panel.mode.currentIndex(), 1)
        self.assertEqual(len(panel.times), 1)
        panel.mode.hidePopup()
        panel.mode.setFocus()
        QTest.keyClick(panel.mode, Qt.Key_Return)
        self.assertTrue(panel.isVisible())
        self.assertIsNone(self.store.reminder(w.current))

    def test_autosave_filter_does_not_change_delete_target_and_undo(self):
        w = self.window
        first = self.store.create_note(favorite=True)
        second = self.store.create_note(favorite=True)
        w.scope = ('favorite', None)
        w.refresh_list()
        w.load_note(first)
        w.favorite.setChecked(False)
        w.delete()
        self.assertEqual(self.store.rows('SELECT deleted FROM notes WHERE id=?', (first,))[0][0], 1)
        self.assertEqual(self.store.rows('SELECT deleted FROM notes WHERE id=?', (second,))[0][0], 0)
        w.undo_change()
        self.assertEqual(w.current, first)
        self.assertEqual(w.statusBar().currentMessage(), 'Действие отменено')

    def test_tag_context_and_backup_preserve_content(self):
        w = self.window
        tag = self.store.execute('INSERT INTO tags(name) VALUES (?)', ('Работа',)).lastrowid
        w.scope = ('tag', tag)
        w.refresh_nav()
        w.new_note()
        w.title.setText('План 🟠')
        w.body.setPlainText('Не потерять текст')
        w.save()
        self.assertEqual(w.scope, ('tag', tag))
        self.assertEqual(self.store.rows('SELECT tag_id FROM note_tags WHERE note_id=?', (w.current,))[0][0], tag)
        destination = Path(self.directory.name) / 'backup.db'
        self.store.backup_to(destination)
        with closing(sqlite3.connect(destination)) as backup:
            self.assertEqual(backup.execute('SELECT title FROM notes').fetchone()[0], 'План 🟠')
            self.assertEqual(backup.execute('PRAGMA integrity_check').fetchone()[0], 'ok')

    def test_manual_icon_selection_survives_rename_and_copy(self):
        w=self.window
        w.new_note()
        note=w.current
        def pick(label):
            dialog=self.app.activeModalWidget()
            next(button for button in dialog.findChildren(QPushButton) if button.text()==label).click()
        QTimer.singleShot(30,lambda:pick('Книга'))
        w.icon_button.click()
        self.assertEqual(self.store.rows('SELECT icon_name FROM notes WHERE id=?',(note,))[0][0],'book')
        w.title.setText('Позвонить другу')
        w.save()
        w.duplicate_note()
        self.assertEqual(self.store.rows('SELECT icon_name FROM notes WHERE id=?',(w.current,))[0][0],'book')
        QTimer.singleShot(30,lambda:pick('Автоматически'))
        w.icon_button.click()
        self.assertIsNone(self.store.rows('SELECT icon_name FROM notes WHERE id=?',(w.current,))[0][0])

    def test_existing_database_icon_migration_preserves_note(self):
        path=Path(self.directory.name)/'legacy.db'
        with closing(sqlite3.connect(path)) as db:
            db.execute("CREATE TABLE notes(id INTEGER PRIMARY KEY,title TEXT NOT NULL DEFAULT '',body TEXT NOT NULL DEFAULT '',folder_id INTEGER,favorite INTEGER NOT NULL DEFAULT 0,archived INTEGER NOT NULL DEFAULT 0,deleted INTEGER NOT NULL DEFAULT 0,updated_at TEXT NOT NULL)")
            db.execute("INSERT INTO notes(title,body,updated_at) VALUES ('Старая заметка','Не потерять','2026-10-02T12:00:00')")
            db.commit()
        migrated=Store(path)
        try:
            note=migrated.rows('SELECT * FROM notes')[0]
            self.assertEqual(note['title'],'Старая заметка')
            self.assertEqual(note['body'],'Не потерять')
            self.assertIsNone(note['icon_name'])
        finally:
            migrated.db.close()

    def test_insert_and_reorder_preserve_unchanged_card_widgets(self):
        w=self.window
        for _ in range(30): self.store.create_note()
        w.refresh_list()
        before={w.notes.item(i).data(Qt.UserRole):w.notes.itemWidget(w.notes.item(i)) for i in range(w.notes.count())}
        w.new_note()
        after={w.notes.item(i).data(Qt.UserRole):w.notes.itemWidget(w.notes.item(i)) for i in range(w.notes.count())}
        self.assertTrue(all(after[note] is widget for note,widget in before.items()))
        moved=min(before)
        self.store.execute('UPDATE notes SET updated_at=? WHERE id=?',('2099-01-01T00:00:00',moved))
        w.refresh_list()
        after={w.notes.item(i).data(Qt.UserRole):w.notes.itemWidget(w.notes.item(i)) for i in range(w.notes.count())}
        self.assertEqual(w.notes.item(0).data(Qt.UserRole),moved)
        self.assertTrue(all(after[note] is widget for note,widget in before.items() if note!=moved))

    def test_failed_schedule_save_keeps_input_and_cancel_keeps_panel(self):
        w = self.window
        w.new_note()
        w.edit_reminder()
        panel = w.panel
        panel.mode.setCurrentIndex(2)
        panel.add_next_time()
        values = panel.values()
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.Cancel):
            self.assertFalse(w.close_reminder_panel())
        with patch.object(self.store, 'save_reminder', side_effect=sqlite3.OperationalError('locked')), patch.object(QMessageBox, 'warning'):
            panel.validate()
        self.assertTrue(panel.isVisible())
        self.assertEqual(panel.values(), values)
        self.assertIsNone(self.store.reminder(w.current))


if __name__ == '__main__':
    unittest.main()

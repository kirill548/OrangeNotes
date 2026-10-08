"""Independent first-user regression cases using native Qt input and a disposable DB."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor, QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from app.database.store import Store
from app.ui.window import Window
from app.ui.editor import NoteEditor

class EditorFirstUserQA(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'first-user.db')
        with patch.object(Window, 'tick', lambda self: None):
            self.window = Window(self.store, background_managed=True)
        self.window.clock.stop()
        self.window.startup_tick.stop()
        self.window.show()
        self.window.new_note()
        self.app.processEvents()

    def tearDown(self):
        w = self.window
        w.debounce.stop()
        w.quitting = True
        w.tray.hide()
        w.close()
        w.deleteLater()
        self.app.processEvents()
        self.store.db.close()
        self.tmp.cleanup()

    def test_bold_italic_are_visible_toggles_and_follow_cursor(self):
        w = self.window
        w.body.setPlainText('Простой текст')
        w.body.moveCursor(QTextCursor.End)
        for key in ('bold', 'italic'):
            action = w.editor_actions[key]
            self.assertTrue(action.isCheckable(), key)
            action.trigger()
            self.app.processEvents()
            self.assertTrue(action.isChecked(), key)
            action.trigger()
            self.app.processEvents()
            self.assertFalse(action.isChecked(), key)
        w.body.selectAll()
        w.editor_actions['bold'].trigger()
        self.assertEqual(w.body.toPlainText(), 'Простой текст')
        self.assertTrue(w.editor_actions['bold'].isChecked())
        w.body.moveCursor(QTextCursor.End)
        self.assertGreaterEqual(w.body.fontWeight(), QFont.Bold)
        w.body.setHtml('<p><b>Жирный</b> Обычный</p>')
        w.body.moveCursor(QTextCursor.End)
        self.app.processEvents()
        self.assertFalse(w.editor_actions['bold'].isChecked())

    def test_checklist_enter_exit_html_reload_and_undo(self):
        e = self.window.body
        e.setPlainText('☑ Купить хлеб')
        e.moveCursor(QTextCursor.End)
        QTest.keyClick(e, Qt.Key_Return)
        self.assertEqual(e.toPlainText(), '☑ Купить хлеб\n☐ ')
        QTest.keyClick(e, Qt.Key_Return)
        self.assertEqual(e.toPlainText(), '☑ Купить хлеб\n')
        e.undo()
        self.assertEqual(e.toPlainText(), '☑ Купить хлеб\n☐ ')
        html = e.toHtml()
        e.setHtml(html)
        self.assertEqual(e.toPlainText(), '☑ Купить хлеб\n☐ ')

    def test_checklist_click_changes_once_readonly_does_not_change(self):
        e = self.window.body
        e.setPlainText('☐ Купить хлеб')
        e.moveCursor(QTextCursor.Start)
        self.app.processEvents()
        point = e.cursorRect().center()
        point.setX(point.x() + 4)
        QTest.mouseClick(e.viewport(), Qt.LeftButton, pos=point)
        self.assertEqual(e.toPlainText(), '☑ Купить хлеб')
        e.undo()
        self.assertEqual(e.toPlainText(), '☐ Купить хлеб')
        e.setReadOnly(True)
        QTest.mouseClick(e.viewport(), Qt.LeftButton, pos=point)
        self.assertEqual(e.toPlainText(), '☐ Купить хлеб')


    def due_event(self, body='Проверить почту'):
        from datetime import datetime, timedelta
        from app.services.scheduler import Scheduler
        now = datetime.now()
        note = self.store.create_note()
        self.store.save_note(note, '', '<p>'+body+'</p>', None, False, '')
        self.store.save_reminder(note, 'once', (now+timedelta(minutes=1)).isoformat(), [], [])
        self.store.execute('UPDATE reminders SET created_utc=NULL,created_at=?,once_at=? WHERE note_id=?',
            ((now-timedelta(minutes=2)).isoformat(), (now-timedelta(minutes=1)).isoformat(), note))
        Scheduler(self.store, lambda *args: None, batch_size=10).tick()
        event = self.store.rows('SELECT e.id FROM reminder_events e JOIN reminders r ON r.id=e.reminder_id WHERE r.note_id=?', (note,))[0][0]
        return note, self.store.notification_event(event)

    def test_notified_attention_persists_and_done_snooze_remove(self):
        w = self.window
        first, event = self.due_event()
        second, another = self.due_event('Позвонить врачу')
        w.body.setFocus()
        self.app.processEvents()
        focused = self.app.focusWidget()
        self.assertEqual(w.update_attention(), {first, second})
        self.assertIs(self.app.focusWidget(), focused)
        self.assertIsNone(self.app.activeModalWidget())
        self.assertFalse(w.attention_button.isHidden())
        self.assertIn('2', w.attention_button.text())
        # New SQLite connection models reopening the persisted database.
        reopened = Store(Path(self.tmp.name) / 'first-user.db')
        try:
            self.assertEqual(reopened.notification_event(event['id'])['status'], 'notified')
        finally:
            reopened.db.close()
        self.store.handle_notification_action(event['id'], event['token'], 'done')
        self.assertEqual(w.update_attention(), {second})
        self.store.handle_notification_action(another['id'], another['token'], 'snooze')
        self.assertEqual(w.update_attention(), set())
        self.assertTrue(w.attention_button.isHidden())

    def test_attention_filter_excludes_archived_deleted_disabled(self):
        w = self.window
        active, _ = self.due_event()
        archived, _ = self.due_event()
        deleted, _ = self.due_event()
        disabled, _ = self.due_event()
        self.store.execute('UPDATE notes SET archived=1 WHERE id=?', (archived,))
        self.store.execute('UPDATE notes SET deleted=1 WHERE id=?', (deleted,))
        self.store.execute('UPDATE reminders SET enabled=0 WHERE note_id=?', (disabled,))
        self.assertEqual(w.update_attention(), {active})
        w.show_attention_reminders()
        self.assertEqual(w.scope, ('attention', None))
        self.assertEqual(w.notes.count(), 1)

    def test_ten_missed_reminders_accessible_from_tray_without_modal(self):
        w = self.window
        expected = {self.due_event('Задача '+str(i))[0] for i in range(10)}
        self.assertEqual(w.update_attention(), expected)
        self.assertIn('10', w.attention_button.text())
        self.assertIsNone(self.app.activeModalWidget())
        action = next(a for a in w.tray.contextMenu().actions() if a.text() == 'Требуют внимания')
        action.trigger()
        self.assertEqual(w.scope, ('attention', None))
        self.assertEqual(w.notes.count(), 10)

    def test_repeated_note_backlog_explains_action_target(self):
        note,event=self.due_event()
        self.store.execute("INSERT INTO reminder_events(reminder_id,scheduled_at,status,revision,title,body,token,due_at) SELECT reminder_id,'2000-01-01T09:00:00','notified',revision,title,body,'backlog-test','2000-01-01T09:00:00' FROM reminder_events WHERE id=?",(event['id'],))
        self.window.load_note(note)
        self.window.update_reminder_label()
        self.assertIn('Незавершённых событий: 2',self.window.reminder_label.text())
        self.assertIn('ближайшему',self.window.reminder_label.text())
        self.window.update_attention()
        self.assertIn('заметок: 1',self.window.attention_button.text())




    def test_bullet_numbered_conversion_continue_exit_and_reload(self):
        from PySide6.QtGui import QTextListFormat
        e = self.window.body
        e.setPlainText('Один\nДва')
        e.selectAll()
        e.toggle_list(QTextListFormat.ListDisc)
        self.assertEqual(e.document().begin().textList().format().style(), QTextListFormat.ListDisc)
        e.toggle_list(QTextListFormat.ListDecimal)
        listing = e.document().begin().textList()
        self.assertEqual(listing.count(), 2)
        self.assertEqual(listing.format().style(), QTextListFormat.ListDecimal)
        e.moveCursor(QTextCursor.End)
        QTest.keyClick(e, Qt.Key_Return)
        self.assertEqual(e.textCursor().currentList().count(), 3)
        QTest.keyClick(e, Qt.Key_Return)
        self.assertIsNone(e.textCursor().currentList())
        html = e.toHtml()
        e.setHtml(html)
        self.assertEqual(e.document().begin().textList().count(), 2)
        self.assertEqual(e.toPlainText(), 'Один\nДва\n')

    def test_checklist_and_list_toggle_off_preserve_words(self):
        from PySide6.QtGui import QTextListFormat
        w = self.window
        e = w.body
        e.setPlainText('Первая\nВторая')
        e.selectAll()
        w.checklist()
        self.assertEqual(e.toPlainText(), '☐ Первая\n☐ Вторая')
        w.checklist()
        self.assertEqual(e.toPlainText(), 'Первая\nВторая')
        e.toggle_list(QTextListFormat.ListDisc)
        e.toggle_list(QTextListFormat.ListDisc)
        self.assertIsNone(e.document().begin().textList())
        self.assertEqual(e.toPlainText(), 'Первая\nВторая')


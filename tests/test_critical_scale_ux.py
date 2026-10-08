"""First-user stress scenarios; temporary SQLite and actual Qt widgets."""
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtGui import QKeySequence
from app.utils.shortcuts import shortcut as native_shortcut
from PySide6.QtWidgets import QApplication
from app.database.store import Store
from app.ui.command_palette import CommandPalette
from app.ui.window import Window


class CriticalScaleUX(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.directory.name) / 'stress.sqlite3')
        with patch.object(Window, 'tick', lambda self: None):
            self.window = Window(self.store, background_managed=True)
        self.window.clock.stop()
        self.window.startup_tick.stop()
        self.window.show()
        self.window.activateWindow()
        self.assertTrue(QTest.qWaitForWindowActive(self.window,3000))
        self.app.processEvents()

    def tearDown(self):
        self.window.debounce.stop()
        self.window._dirty = False
        self.window.quitting = True
        self.window.tray.hide()
        self.window.close()
        self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()
        self.store.db.close()
        self.directory.cleanup()

    def test_million_character_note_palette_rapid_query_no_data_loss(self):
        w = self.window
        w.new_note()
        note = w.current
        payload = ('Почта café 東京 0123456789\n' * 44000) + 'FINAL_CANARY'
        self.assertGreater(len(payload), 1000000)
        w.body.setPlainText(payload)
        started = time.monotonic()
        w.show_command_palette()
        self.app.processEvents()
        palette = w._command_dialog
        for query in ('п', 'по', 'пом', 'помощь', 'новая', 'архив', 'поиск') * 30:
            palette.query.setText(query)
        self.assertEqual(palette.actions.currentItem().data(Qt.UserRole), 'search')
        QTest.keyClick(palette.query, Qt.Key_Escape)
        self.app.processEvents()
        self.assertEqual(w.body.toPlainText(), payload)
        self.assertTrue(w.save(refresh=False))
        self.assertIn('FINAL_CANARY', self.store.rows('SELECT body FROM notes WHERE id=?', (note,))[0][0])
        # Wide limit catches pathological hangs without pretending to benchmark hardware.
        self.assertLess(time.monotonic() - started, 15)

    def test_ctrl_k_keeps_one_palette_and_enter_does_not_edit_note(self):
        w = self.window
        w.new_note()
        w.body.setPlainText('Unchanged body')
        w.body.setFocus()
        for _ in range(8):
            QTest.keySequence(w.body, QKeySequence(native_shortcut('Ctrl+K')))
            self.app.processEvents()
        visible = [p for p in w.findChildren(CommandPalette) if p.isVisible()]
        self.assertEqual(len(visible), 1)
        visible[0].query.setText('архив')
        QTest.keyClick(visible[0].query, Qt.Key_Return)
        self.app.processEvents()
        self.assertEqual(w.scope, ('archive', None))
        self.assertNotIn('kkkk', self.store.rows('SELECT body FROM notes ORDER BY id DESC')[0][0])

    def test_fifty_missed_and_twenty_snoozed_events_count_notes_not_events(self):
        now = datetime.now()
        expected = set()
        for i in range(70):
            note = self.store.create_note()
            self.store.save_note(note, '', f'<p>Проверить задачу {i}</p>', None, False, '')
            self.store.save_reminder(note, 'once', (now + timedelta(days=1)).isoformat(), [], [])
            reminder = self.store.reminder(note)
            due = now - timedelta(days=2) if i < 50 else now + timedelta(hours=1)
            if i < 50:
                expected.add(note)
            self.store.execute('INSERT INTO reminder_events(reminder_id,scheduled_at,status,revision,due_at,token) VALUES(?,?,?,?,?,?)',
                (reminder['id'], (now - timedelta(days=2)).isoformat(), 'notified', reminder['revision'], due.isoformat(), f'critical-{i}'))
        self.store.db.commit()
        self.window.body.setFocus()
        self.app.processEvents()
        focused = self.app.focusWidget()
        self.assertEqual(self.window.update_attention(), expected)
        self.assertIs(self.app.focusWidget(), focused)
        self.assertIsNone(self.app.activeModalWidget())
        self.assertIn('50', self.window.tray.toolTip())
        self.window.show_attention_reminders()
        self.assertEqual(self.window.notes.count(), 50)
        self.assertEqual(self.window.scope, ('attention', None))
        self.assertEqual(self.store.rows('PRAGMA integrity_check')[0][0], 'ok')

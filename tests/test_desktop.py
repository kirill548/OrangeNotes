"""Native Qt desktop regressions; all data lives in a temporary database."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import Qt, QCoreApplication, QEvent
from PySide6.QtGui import QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton, QSystemTrayIcon
from app.database.store import Store
from app.ui.window import Window
from app.utils.shortcuts import shortcut as native_shortcut


class DesktopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tick_patch = patch.object(Window, 'tick', lambda self: None)
        self.tick_patch.start()
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'desktop.sqlite3')
        self.window = Window(self.store)
        # Clean up even if native activation fails during setUp.
        self.addCleanup(self.cleanup_window)
        self.window.clock.stop()
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()
        self.window.windowHandle().requestActivate()
        # Window-scoped QShortcuts dispatch only after native activation completes.
        # A fixed delay races focus restoration from dialogs in earlier tests.
        self.assertTrue(QTest.qWaitForWindowActive(self.window, 3000))
        self.window.new_note()
        self.window.title.setText('Проверка рабочего окна')
        self.window.body.setPlainText('Текст, который нельзя потерять.')
        self.window.save()

    def cleanup_window(self):
        w = self.window
        if w.panel:
            w.panel.hide()
        w.debounce.stop()
        w.quitting = True
        w.tray.hide()
        w.close()
        w.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.app.processEvents()
        self.store.db.close()
        self.tmp.cleanup()
        self.tick_patch.stop()

    def key(self, sequence, target=None):
        QTest.keySequence(target or self.app.focusWidget() or self.window, QKeySequence(native_shortcut(sequence)))
        QTest.qWait(30)

    def test_shortcuts_keep_text_and_focus(self):
        w = self.window
        self.key('Ctrl+F')
        self.assertIs(self.app.focusWidget(), w.search)
        w.body.setFocus()
        w.body.selectAll()
        original = w.body.toPlainText()
        self.key('Ctrl+B')
        self.key('Ctrl+I')
        self.assertEqual(w.body.toPlainText(), original)
        self.key('Ctrl+S')
        self.assertFalse(w._dirty)
        self.key('Tab')
        self.assertIsNot(self.app.focusWidget(), w.body)
        source = w.current
        self.key('Ctrl+Shift+D')
        self.assertNotEqual(w.current, source)
        self.assertEqual(w.body.toPlainText(), original)
        duplicate = w.current
        self.key('Ctrl+N')
        self.assertNotEqual(w.current, duplicate)
        self.assertEqual(w.body.toPlainText(), '')
        self.assertIs(self.app.focusWidget(), w.title)

    def test_resize_and_window_states_with_panel(self):
        w = self.window
        note = w.current
        for start_width in (900, 1500):
            w.resize(start_width, 650)
            self.app.processEvents()
            w.edit_reminder()
            self.app.processEvents()
            for width, height in [(900,540),(1800,900),(1100,560),(1500,700)] * 3:
                w.resize(width, height)
                self.app.processEvents()
                self.assertEqual(w.current, note)
                self.assertEqual(w.body.toPlainText(), 'Текст, который нельзя потерять.')
                self.assertTrue(w.panel.isVisible())
                save = w.panel.findChild(QPushButton,'primary')
                self.assertFalse(save.visibleRegion().isEmpty())
            w.showMaximized()
            self.app.processEvents()
            self.assertTrue(w.isMaximized())
            w.showMinimized()
            self.app.processEvents()
            w.open_window()
            self.app.processEvents()
            self.assertTrue(w.isMaximized())
            w.showNormal()
            w.panel.reject()

    def test_escape_floating_panel_and_tray(self):
        w = self.window
        w.resize(900,540)
        w.edit_reminder()
        self.app.processEvents()
        panel = w.panel
        available = w.screen().availableGeometry()
        self.assertTrue(available.intersects(panel.frameGeometry()))
        panel.activateWindow()
        panel.mode.setFocus()
        QTest.qWait(80)
        self.key('Escape', panel)
        self.assertFalse(panel.isVisible())
        self.assertTrue(w.isVisible())
        if QSystemTrayIcon.isSystemTrayAvailable():
            w.close()
            self.assertFalse(w.isVisible())
            w.open_window()
            self.assertTrue(w.isVisible())

    def test_floating_panel_stays_on_screen_for_offscreen_parent(self):
        w = self.window
        w.resize(900,540)
        w.move(-850,0)
        self.app.processEvents()
        w.edit_reminder()
        self.app.processEvents()
        available = w.screen().availableGeometry()
        self.assertGreaterEqual(w.panel.frameGeometry().left(),available.left())


if __name__ == '__main__':
    unittest.main(verbosity=2)


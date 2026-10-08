import unittest
import tempfile
from pathlib import Path
from PySide6.QtCore import Qt, QCoreApplication, QEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from app.ui.command_palette import CommandPalette, COMMANDS
from app.database.store import Store
from app.ui.window import Window


class CommandPaletteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.palette=CommandPalette()
        self.received=[]
        self.palette.commandTriggered.connect(self.received.append)
        self.palette.show()
        self.app.processEvents()

    def tearDown(self):
        self.palette.close()
        self.palette.deleteLater()
        self.app.processEvents()

    def test_alias_filter_and_enter_dispatch_exactly_once(self):
        self.palette.query.setText('пчелка')
        self.assertEqual(self.palette.actions.count(),1)
        QTest.keyClick(self.palette.query,Qt.Key_Return)
        self.assertEqual(self.received,['companion'])
        self.assertFalse(self.palette.isVisible())

    def test_empty_results_do_not_execute(self):
        self.palette.query.setText('несуществующее действие')
        QTest.keyClick(self.palette.query,Qt.Key_Return)
        self.assertEqual(self.received,[])
        self.assertTrue(self.palette.isVisible())

    def test_keyboard_selection_and_escape(self):
        self.assertEqual(self.palette.actions.count(),len(COMMANDS))
        QTest.keyClick(self.palette.query,Qt.Key_Down)
        QTest.keyClick(self.palette.query,Qt.Key_Return)
        self.assertEqual(self.received,['search'])

    def test_escape_closes_without_action(self):
        QTest.keyClick(self.palette.query,Qt.Key_Escape)
        self.assertEqual(self.received,[])
        self.assertFalse(self.palette.isVisible())

    def test_missed_reminders_are_discoverable_by_user_language(self):
        self.palette.query.setText('пропущенные')
        QTest.keyClick(self.palette.query, Qt.Key_Return)
        self.assertEqual(self.received, ['attention'])


class CommandPaletteIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.store=Store(Path(self.directory.name)/'notes.db')
        self.window=Window(self.store)
        self.window.clock.stop()
        self.window.startup_tick.stop()
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.window.quitting=True
        self.window.tray.hide()
        self.window.close()
        self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)
        self.app.processEvents()
        self.store.db.close()
        self.directory.cleanup()

    def activate(self, query):
        self.window.show_command_palette()
        self.app.processEvents()
        palette=next(p for p in self.window.findChildren(CommandPalette) if p.isVisible())
        palette.query.setText(query)
        QTest.keyClick(palette.query,Qt.Key_Return)
        self.app.processEvents()

    def test_navigation_and_creation_use_existing_window_actions(self):
        self.activate('открыть архив')
        self.assertEqual(self.window.scope,('archive',None))
        before=self.store.rows('SELECT COUNT(*) FROM notes')[0][0]
        self.activate('создать заметку')
        self.assertEqual(self.store.rows('SELECT COUNT(*) FROM notes')[0][0],before+1)
        self.assertIsNotNone(self.window.current)

    def test_menu_exposes_keyboard_discovery(self):
        menu=self.window.app_menu_button.menu()
        entries=[action for action in menu.actions() if action.text().startswith('Быстрые действия')]
        self.assertEqual(len(entries),1)
        self.assertTrue(entries[0].isVisible())
        entries[0].trigger()
        self.app.processEvents()
        self.assertTrue(any(p.isVisible() for p in self.window.findChildren(CommandPalette)))

"""Real Qt popup interaction, without notification or file-dialog side effects."""
import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from app.database.store import Store
from app.ui.window import Window


class AppMenuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.store=Store(Path(self.tmp.name)/'notes.db')
        self.calls=[]
        self.patches=[]
        for name in ('show_help','reminder_status','export_note','backup_database'):
            p=patch.object(Window,name,lambda _,name=name:self.calls.append(name))
            p.start();self.patches.append(p)
        self.window=Window(self.store,background_managed=True)
        self.window.clock.stop()
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.window.tray.hide()
        self.window.quitting=True
        self.window.close()
        for p in self.patches:p.stop()
        self.store.db.close()
        self.tmp.cleanup()

    def popup(self,callback):
        errors=[]
        def inspect():
            try:callback(self.window.app_menu_button.menu())
            except Exception as error:errors.append(error)
            finally:self.window.app_menu_button.menu().hide()
        QTimer.singleShot(30,inspect)
        QTest.mouseClick(self.window.app_menu_button,Qt.LeftButton)
        if errors:raise errors[0]

    def test_real_click_escape_keeps_maximized_window(self):
        self.window.showMaximized();self.app.processEvents()
        size=self.window.size()
        button=self.window.app_menu_button
        self.assertEqual((button.width(),button.height()),(36,36))
        self.assertEqual(button.accessibleName(),'Меню приложения')
        self.assertFalse(button.icon().isNull())
        def inspect(menu):
            self.assertTrue(menu.isVisible())
            QTest.keyClick(menu,Qt.Key_Escape)
            self.assertFalse(menu.isVisible())
        self.popup(inspect)
        self.assertTrue(self.window.isMaximized())
        self.assertEqual(self.window.size(),size)

    def test_keyboard_action_and_export_availability(self):
        def inspect(menu):
            self.assertFalse(menu.export_action.isEnabled())
            menu.setActiveAction(menu.help_action)
            QTest.keyClick(menu,Qt.Key_Return)
        self.popup(inspect)
        self.assertEqual(self.calls,['show_help'])
        self.window.current=123
        self.popup(lambda menu:self.assertTrue(menu.export_action.isEnabled()))

    def test_high_dpi_icon_is_centered_and_menu_has_no_native_square_shadow(self):
        button=self.window.app_menu_button
        image=button.icon().pixmap(22,22).toImage()
        pixels=[(x,y) for y in range(image.height()) for x in range(image.width()) if image.pixelColor(x,y).alpha()>0]
        self.assertTrue(pixels)
        center=(min(y for x,y in pixels)+max(y for x,y in pixels))/2
        self.assertLess(abs(center-(image.height()-1)/2),2)
        menu=button.menu()
        self.assertTrue(menu.windowFlags()&Qt.NoDropShadowWindowHint)
        def inspect(popup):
            self.assertFalse(popup.mask().contains(popup.rect().topLeft()))
            self.assertTrue(popup.mask().contains(popup.rect().center()))
        self.popup(inspect)

    def test_callbacks_and_sections(self):
        menu=self.window.app_menu_button.menu()
        self.assertEqual([a.text() for a in menu.actions() if not a.isEnabled() and a!=menu.export_action],['Справка','Данные'])
        self.window.current=1;menu.update_actions()
        for action in (menu.help_action,menu.status_action,menu.export_action,menu.backup_action):
            self.assertFalse(action.icon().isNull());action.trigger()
        self.assertEqual(self.calls,['show_help','reminder_status','export_note','backup_database'])


if __name__=='__main__':unittest.main()

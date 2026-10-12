import unittest
from PySide6.QtWidgets import QApplication, QLabel
from app.ui.help_dialog import HelpDialog, AboutDialog
from app.ui.reminders import ReminderDialog
from app.ui.notification_fallback import NotificationFallback


class DialogBrandIconTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_dialogs_have_real_window_and_visible_brand_icons(self):
        for factory in (HelpDialog, AboutDialog, lambda: ReminderDialog(None)):
            with self.subTest(factory=factory):
                dialog = factory()
                try:
                    self.assertFalse(dialog.windowIcon().isNull())
                    brand = dialog.findChild(QLabel, 'applicationBrandIcon')
                    self.assertIsNotNone(brand)
                    self.assertFalse(brand.pixmap().isNull())
                finally:
                    dialog.close()
                    dialog.deleteLater()

    def test_fallback_uses_brand_asset(self):
        card = NotificationFallback(lambda: None, lambda: None)
        self.assertFalse(card.brand.pixmap().isNull())
        card.deleteLater()

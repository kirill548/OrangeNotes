import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from PySide6.QtWidgets import QApplication, QMessageBox
from app.ui.localization import install_russian_dialogs


class DialogLocalizationTests(unittest.TestCase):
    def test_confirmation_and_unsaved_buttons_are_russian(self):
        app = QApplication.instance() or QApplication([])
        install_russian_dialogs(app)
        box = QMessageBox()
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No | QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        for button, label in ((QMessageBox.Yes, 'Да'), (QMessageBox.No, 'Нет'),
                              (QMessageBox.Save, 'Сохранить'), (QMessageBox.Discard, 'Не сохранять'),
                              (QMessageBox.Cancel, 'Отмена')):
            self.assertEqual(box.button(button).text().replace('&', ''), label)
        box.setDefaultButton(QMessageBox.No)
        self.assertEqual(box.standardButton(box.defaultButton()), QMessageBox.No)
        before = app._orange_translators
        install_russian_dialogs(app)
        self.assertIs(app._orange_translators, before)

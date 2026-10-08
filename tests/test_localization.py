import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QKeySequence
from app.ui.localization import DialogTranslator
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

    def test_untranslated_context_and_label_preserve_qt_fallback(self):
        app=QApplication.instance() or QApplication([])
        install_russian_dialogs(app)
        translator=DialogTranslator()
        self.assertIsNone(translator.translate('QShortcut','Ctrl'))
        self.assertIsNone(translator.translate('QPlatformTheme','Unrecognised action'))
        self.assertEqual(QCoreApplication.translate('OrangeNotesUntranslated','Keep this label'),'Keep this label')

    def test_native_shortcuts_remain_parseable_after_dialog_translation(self):
        app=QApplication.instance() or QApplication([])
        install_russian_dialogs(app)
        for text in ('Ctrl+F','Ctrl+K','Ctrl+B','Ctrl+I','Ctrl+Shift+D'):
            with self.subTest(sequence=text):
                sequence=QKeySequence(text)
                self.assertFalse(sequence.isEmpty())
                self.assertEqual(sequence,QKeySequence(text,QKeySequence.PortableText))
        for standard in (QKeySequence.StandardKey.SelectAll,QKeySequence.StandardKey.Paste,QKeySequence.StandardKey.Copy):
            sequence=QKeySequence(standard)
            self.assertFalse(sequence.isEmpty())

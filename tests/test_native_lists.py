import unittest
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QTextCursor, QTextListFormat
from PySide6.QtTest import QTest
from PySide6.QtCore import Qt
from app.ui.editor import NoteEditor

class NativeListTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app=QApplication.instance() or QApplication([])
    def setUp(self): self.editor=NoteEditor();self.editor.show()
    def tearDown(self): self.editor.close();self.editor.deleteLater();self.app.processEvents()
    def test_convert_checklist_numbered_bullets_and_toggle_off_undo(self):
        e=self.editor;e.setPlainText('☐ One\n☑ Two');e.selectAll()
        e.toggle_list(QTextListFormat.ListDecimal)
        self.assertEqual(e.toPlainText(),'One\nTwo')
        self.assertEqual(e.document().begin().textList().count(),2)
        html=e.toHtml(); e.setHtml(html);e.selectAll()
        self.assertEqual(e.document().begin().textList().format().style(),QTextListFormat.ListDecimal)
        e.toggle_list(QTextListFormat.ListDisc)
        self.assertEqual(e.document().begin().textList().format().style(),QTextListFormat.ListDisc)
        e.toggle_list(QTextListFormat.ListDisc)
        self.assertIsNone(e.document().begin().textList())
        e.undo()
        self.assertEqual(e.document().begin().textList().format().style(),QTextListFormat.ListDisc)
    def test_enter_continues_and_empty_exits_numbering(self):
        e=self.editor;e.setPlainText('One');e.moveCursor(QTextCursor.End)
        e.toggle_list(QTextListFormat.ListDecimal)
        QTest.keyClick(e,Qt.Key_Return)
        self.assertIsNotNone(e.textCursor().block().textList())
        QTest.keyClick(e,Qt.Key_Return)
        self.assertIsNone(e.textCursor().block().textList())
        self.assertEqual(e.textCursor().blockFormat().indent(),0)
    def test_readonly_cannot_change_list(self):
        e=self.editor;e.setPlainText('One');e.setReadOnly(True)
        e.toggle_list(QTextListFormat.ListDisc)
        self.assertIsNone(e.document().begin().textList())

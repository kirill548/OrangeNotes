"""Clipboard and literal Markdown regression checks; no user data is touched."""
import sys
import tempfile
import unittest
import gc
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import Qt, QMimeData, QUrl, QPointF, QCoreApplication, QEvent
from PySide6.QtGui import QFont, QTextCursor, QDragEnterEvent, QDropEvent, QKeySequence
from PySide6.QtWidgets import QApplication, QToolBar, QPushButton
from PySide6.QtTest import QTest
from app.database.store import Store
from app.ui.window import Window
from app.utils.shortcuts import shortcut as native_shortcut


class ClipboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.saved_clipboard = {}
        original = self.app.clipboard().mimeData()
        if original:
            for mime in original.formats():
                self.saved_clipboard[mime] = bytes(original.data(mime))
        del original
        self.directory = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.directory.name) / 'notes.sqlite3')
        self.window = Window(self.store)
        self.window.clock.stop()
        self.window.show()
        self.window.activateWindow()
        self.assertTrue(QTest.qWaitForWindowActive(self.window,3000))
        QTest.mouseClick(self.window.findChild(QPushButton, 'new'), Qt.LeftButton)
        self.note_id = self.window.current

    def tearDown(self):
        self.window.quitting = True
        self.window.tray.hide()
        self.window.close()
        self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.store.db.close()
        self.directory.cleanup()
        restored = QMimeData()
        for mime, data in self.saved_clipboard.items():
            restored.setData(mime, data)
        if self.saved_clipboard:
            self.app.clipboard().setMimeData(restored)
        else:
            # Restore an originally empty clipboard as empty, not as an owned
            # empty MIME object (offscreen Qt crashes destroying that object).
            self.app.clipboard().clear()
        del restored
        self.saved_clipboard = None
        self.window = None
        self.store = None
        gc.collect()
        self.app.processEvents()

    def paste(self, value):
        body = self.window.body
        QTest.mouseClick(body.viewport(), Qt.LeftButton)
        QTest.keySequence(body, QKeySequence(QKeySequence.StandardKey.SelectAll))
        self.app.clipboard().setText(value)
        QTest.keySequence(body, QKeySequence(QKeySequence.StandardKey.Paste))
        self.app.processEvents()

    def copy_all(self):
        body = self.window.body
        QTest.keySequence(body, QKeySequence(QKeySequence.StandardKey.SelectAll))
        QTest.keySequence(body, QKeySequence(QKeySequence.StandardKey.Copy))
        return self.app.clipboard().text()

    def reopen(self):
        QTest.keySequence(self.window.body, QKeySequence(QKeySequence.StandardKey.Save))
        self.window.load_note(self.note_id)

    def test_literal_markdown_unicode_roundtrip(self):
        value = ('# Заголовок\n\n| Имя | Значение |\n| --- | --- |\n| α | 42 |\n\n'
                 '- первый\n  - вложенный\n    1. элемент\n\n'
                 '```python\nif x < 3:\n\tprint("<b>literal</b> & ")\n```\n\n'
                 r'$$\frac{a}{b}=\sqrt{x}$$' + '\n'
                 'Русский 中文 日本語 café e\u0301 👨‍👩‍👧‍👦 🧡\n'
                 'العربية עברית English 123\n')
        self.paste(value)
        self.assertEqual(self.window.body.toPlainText(), value)
        self.assertEqual(self.copy_all(), value)
        self.reopen()
        self.assertEqual(self.window.body.toPlainText(), value)
        self.assertEqual(self.copy_all(), value)

    def test_rich_clipboard_is_pasted_as_plain_text(self):
        mime = QMimeData()
        mime.setText('literal <script> Ω')
        mime.setHtml('<h1>Injected</h1><img src="https://example.invalid/image">')
        self.app.clipboard().setMimeData(mime)
        QTest.mouseClick(self.window.body.viewport(), Qt.LeftButton)
        QTest.keySequence(self.window.body, QKeySequence(QKeySequence.StandardKey.Paste))
        self.assertEqual(self.window.body.toPlainText(), 'literal <script> Ω')
        self.assertNotIn('example.invalid', self.window.body.toHtml())

    def test_bold_italic_save_reopen_and_plain_code_copy(self):
        value = 'code <div> & markdown **literal**\nreturn x != y;'
        self.paste(value)
        QTest.keySequence(self.window.body, QKeySequence(QKeySequence.StandardKey.SelectAll))
        QTest.keySequence(self.window.body, QKeySequence(native_shortcut('Ctrl+B')))
        QTest.keySequence(self.window.body, QKeySequence(native_shortcut('Ctrl+I')))
        self.reopen()
        cursor = self.window.body.textCursor()
        cursor.setPosition(2)
        fmt = cursor.charFormat()
        self.assertGreaterEqual(fmt.fontWeight(), QFont.Bold)
        self.assertTrue(fmt.fontItalic())
        self.assertEqual(self.copy_all(), value)

    def test_file_drop_does_not_read_or_embed_file_contents(self):
        path = Path(self.directory.name) / 'private.html'
        secret = '<script>secret-content-not-to-be-imported</script>'
        path.write_text(secret, encoding='utf-8')
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(path))])
        viewport = self.window.body.viewport()
        enter = QDragEnterEvent(viewport.rect().center(), Qt.CopyAction, mime,
                               Qt.LeftButton, Qt.NoModifier)
        self.app.sendEvent(viewport, enter)
        drop = QDropEvent(QPointF(viewport.rect().center()), Qt.CopyAction, mime,
                          Qt.LeftButton, Qt.NoModifier)
        self.app.sendEvent(viewport, drop)
        self.assertNotIn(secret, self.window.body.toPlainText())
        self.assertNotIn('secret-content-not-to-be-imported', self.window.body.toHtml())
        self.assertNotIn('<img', self.window.body.toHtml())
        self.assertEqual(list(Path(self.directory.name).glob('*.html')), [path])


if __name__ == '__main__':
    unittest.main(verbosity=2)

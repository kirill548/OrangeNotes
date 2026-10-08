"""Readable frameless help, including narrow and enlarged-font layouts."""
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import Qt,QPoint
from PySide6.QtGui import QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication,QLabel,QMainWindow,QPushButton,QWidget
from app.ui.help_dialog import HelpDialog,StyledDialog
from app.utils.shortcuts import shortcut_label


class HelpDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])
        cls.app.setStyle('Fusion')
        cls.app.setFont(QFont('Segoe UI',10))

    def setUp(self):
        self.original_font=QFont(self.app.font())
        self.parent=QMainWindow();self.parent.resize(1050,740);self.parent.show()
        self.dialogs=[];self.app.processEvents()

    def tearDown(self):
        for dialog in self.dialogs:dialog.close();dialog.deleteLater()
        self.parent.close();self.parent.deleteLater();self.app.processEvents();self.app.setFont(self.original_font)

    def dialog(self):
        dialog=HelpDialog(self.parent);self.dialogs.append(dialog);dialog.show();self.app.processEvents();return dialog

    def assert_readable(self,dialog):
        content=dialog.content
        for label in dialog.findChildren(QLabel):
            if not label.text() or not content.isAncestorOf(label):continue
            self.assertEqual(label.textFormat(),Qt.PlainText)
            self.assertGreater(label.contentsRect().width(),30,label.text())
            required=label.fontMetrics().boundingRect(0,0,label.contentsRect().width(),10000,int(Qt.TextWordWrap),label.text())
            self.assertGreaterEqual(label.height()+3,required.height(),label.text())
            position=label.mapTo(content,QPoint(0,0))
            self.assertGreaterEqual(position.x(),0,label.text())
            self.assertLessEqual(position.x()+label.width(),content.width()+1,label.text())

    def test_frameless_nonmodal_close_accessibility_and_parent_unchanged(self):
        geometry=self.parent.geometry();dialog=self.dialog()
        self.assertTrue(dialog.windowFlags()&Qt.FramelessWindowHint)
        self.assertFalse(dialog.isModal());self.assertEqual(dialog.windowModality(),Qt.NonModal)
        self.assertEqual(self.parent.geometry(),geometry)
        self.assertEqual(dialog.close_button.accessibleName(),'Закрыть справку')
        self.assertFalse(dialog.close_button.icon().isNull())
        QTest.mouseClick(dialog.close_button,Qt.LeftButton);self.assertFalse(dialog.isVisible())
        dialog.show();self.app.processEvents();QTest.keyClick(dialog,Qt.Key_Escape);self.assertFalse(dialog.isVisible())
        dialog.show();self.app.processEvents();QTest.mouseClick(dialog.done_button,Qt.LeftButton);self.assertFalse(dialog.isVisible())

    def test_content_truthful_and_keyboard_shortcuts_present(self):
        dialog=self.dialog();text=' '.join(label.text() for label in dialog.findChildren(QLabel))
        for expected in ['С чего начать','Напоминания','Порядок в заметках','Быстрые клавиши','Ctrl+K','Ctrl+N','Ctrl+F','Ctrl+S','Ctrl+Shift+D','Мой день','в полночь','фоновая служба настроена','войти в Windows','явном выборе','без ИИ-комплекта']:
            self.assertIn(shortcut_label(expected).casefold(),text.casefold())
        self.assertNotIn('API',text)
        self.assertTrue(dialog.scroll.widgetResizable())

    def test_quick_actions_and_fallback_explain_actual_controls_and_limits(self):
        dialog=self.dialog();text=' '.join(label.text() for label in dialog.findChildren(QLabel))
        for expected in [shortcut_label('Ctrl+K'),'↑ и ↓','Enter выполняет','Escape закрывает',
                         'поиск команд приложения','Резервная карточка в Windows',
                         '«Посмотреть» открывает «Требуют внимания»','«Почему?» — диагностику',
                         'число ожидающих событий','не включает уведомления ОС',
                         'когда окно приложения закрыто']:
            self.assertIn(expected,text)

    def test_narrow_640_layout_has_readable_labels_and_vertical_scroll(self):
        dialog=self.dialog();dialog.resize(640,480);self.app.processEvents()
        self.assert_readable(dialog)
        self.assertGreater(dialog.scroll.verticalScrollBar().maximum(),0)
        self.assertEqual(dialog.scroll.horizontalScrollBar().maximum(),0)
        self.assertLessEqual(dialog.maximumHeight(),dialog.screen().availableGeometry().height())

    def test_enlarged_font_at_640_keeps_text_readable_and_saves_preview(self):
        font=QFont(self.original_font);font.setPointSize(16);self.app.setFont(font)
        dialog=self.dialog();dialog.resize(640,660);self.app.processEvents()
        self.assert_readable(dialog)
        self.assertGreater(dialog.scroll.verticalScrollBar().maximum(),0)
        self.assertEqual(dialog.scroll.horizontalScrollBar().maximum(),0)
        # The ordinary preview intentionally uses the application's normal font.
        dialog.close();self.app.setFont(self.original_font)
        preview=self.dialog()
        target=Path(__file__).resolve().parents[1]/'help_dialog_preview.png'
        self.assertTrue(preview.grab().save(str(target)))

    def test_common_chrome_accepts_status_content_and_footer_action(self):
        dialog=StyledDialog('Напоминания','Состояние фоновой службы',self.parent,symbol='bell');self.dialogs.append(dialog)
        label=QLabel('Проверка службы');label.setWordWrap(True);dialog.body_layout.addWidget(label)
        refresh=QPushButton('Проверить');dialog.footer_layout.insertWidget(0,refresh)
        dialog.resize(640,620);dialog.show();self.app.processEvents()
        self.assertFalse(dialog.isModal());self.assertTrue(label.isVisible());self.assertTrue(refresh.isVisible())
        self.assertTrue(dialog.windowFlags()&Qt.FramelessWindowHint)


if __name__=='__main__':unittest.main(verbosity=2)

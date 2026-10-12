"""Clicks on custom card widgets must select the actual note reliably."""
import tempfile
import re
import unittest
from pathlib import Path
from unittest.mock import patch
from PySide6.QtCore import Qt, QPoint
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from app.database.store import Store
from app.ui.window import Window
from app.ui.design import STYLE


class NoteCardClickTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.store=Store(Path(self.tmp.name)/'notes.db')
        self.ids=[self.store.create_note() for _ in range(2)]
        self.window=Window(self.store,background_managed=True)
        self.window.clock.stop()
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.window.tray.hide()
        self.window.quitting=True
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.store.db.close()
        self.tmp.cleanup()

    def item(self,note_id):
        return next(self.window.notes.item(i) for i in range(self.window.notes.count()) if self.window.notes.item(i).data(Qt.UserRole)==note_id)

    def test_title_body_icon_and_blank_card_select_each_time(self):
        for region in ('heading','snippet','tile','blank'):
            for _ in range(3):
                for note_id in self.ids:
                    item=self.item(note_id)
                    card=self.window.notes.itemWidget(item)
                    self.window.notes.scrollToItem(item)
                    self.app.processEvents()
                    target=card if region=='blank' else card.findChild(type(card.heading),'tile') if region=='tile' else getattr(card,region)
                    point=QPoint(card.width()//2,card.height()-8) if region=='blank' else target.rect().center()
                    QTest.mouseClick(target,Qt.LeftButton,pos=point)
                    self.app.processEvents()
                    self.assertEqual(self.window.current,note_id,region)
                    self.assertEqual(self.window.notes.currentItem().data(Qt.UserRole),note_id,region)

    def test_repeat_current_card_click_keeps_unsaved_cursor_and_body(self):
        note_id=self.window.current
        card=self.window.notes.itemWidget(self.item(note_id))
        self.window.body.setPlainText('Новый текст')
        self.window.debounce.stop()
        with patch.object(self.window,'load_note',wraps=self.window.load_note) as load:
            QTest.mouseClick(card,Qt.LeftButton,pos=QPoint(card.width()//2,card.height()-8))
            self.assertEqual(self.window.body.toPlainText(),'Новый текст')
            load.assert_not_called()

    def test_favorite_click_does_not_switch_note(self):
        current=self.window.current
        other=next(i for i in self.ids if i!=current)
        card=self.window.notes.itemWidget(self.item(other))
        QTest.mouseClick(card.star,Qt.LeftButton)
        self.app.processEvents()
        self.assertEqual(self.window.current,current)
        self.assertTrue(self.store.db.execute('SELECT favorite FROM notes WHERE id=?',(other,)).fetchone()[0])

    def test_failed_save_preserves_old_selection(self):
        current=self.window.current
        other=next(i for i in self.ids if i!=current)
        card=self.window.notes.itemWidget(self.item(other))
        with patch.object(self.window,'save',return_value=False):
            QTest.mouseClick(card,Qt.LeftButton,pos=QPoint(card.width()//2,card.height()-8))
        self.assertEqual(self.window.current,current)
        self.assertEqual(self.window.notes.currentItem().data(Qt.UserRole),current)

    def test_focus_style_removes_container_outline_without_hiding_selection(self):
        rules=dict(re.findall(r'([^{}]+)\{([^{}]*)\}',STYLE))
        rules={selector.strip():body for selector,body in rules.items()}
        self.assertRegex(rules['QListWidget:focus'],r'border\s*:\s*0\s*;')
        self.assertRegex(rules['QListWidget:focus'],r'outline\s*:\s*0\s*;')
        self.assertRegex(rules['QListWidget#cards::item:focus'],r'outline\s*:\s*0\s*;')
        self.assertIn('background:#fffaf3',rules['QListWidget#cards::item:selected'])
        self.assertNotIn('QListWidget',next(selector for selector in rules if selector.startswith('QPushButton:focus')))

    def test_keyboard_focus_keeps_arrow_selection_and_container_border_clean(self):
        notes=self.window.notes
        notes.setCurrentRow(0)
        self.window.title.setFocus()
        self.app.processEvents()
        before=notes.grab().toImage()
        notes.setFocus(Qt.TabFocusReason)
        self.app.processEvents()
        self.assertTrue(notes.hasFocus())
        focused=notes.grab().toImage()
        # The outer edge must not gain the former orange focus rectangle.
        for x in range(before.width()):
            for y in (0,before.height()-1):
                self.assertEqual(before.pixelColor(x,y),focused.pixelColor(x,y))
        for y in range(before.height()):
            for x in (0,before.width()-1):
                self.assertEqual(before.pixelColor(x,y),focused.pixelColor(x,y))
        QTest.keyClick(notes,Qt.Key_Down)
        self.app.processEvents()
        self.assertEqual(notes.currentRow(),1)
        self.assertTrue(notes.currentItem().isSelected())
        self.assertEqual(self.window.current,notes.currentItem().data(Qt.UserRole))

    def test_clicking_partially_visible_card_preserves_scroll(self):
        self.window.notes.setFixedHeight(170)
        self.app.processEvents()
        first=self.window.notes.item(0)
        second=self.window.notes.item(1)
        self.window.activate_note_card(first.data(Qt.UserRole))
        bar=self.window.notes.verticalScrollBar()
        bar.setValue(0)
        self.app.processEvents()
        before=bar.value()
        card=self.window.notes.itemWidget(second)
        visible=self.window.notes.visualItemRect(second).intersected(self.window.notes.viewport().rect())
        self.assertFalse(visible.isEmpty())
        point=card.mapFrom(self.window.notes.viewport(),visible.center())
        QTest.mouseClick(card,Qt.LeftButton,pos=point)
        self.app.processEvents()
        self.assertEqual(self.window.current,second.data(Qt.UserRole))
        self.assertEqual(bar.value(),before)

    def test_delete_opens_previous_visible_note(self):
        previous=self.window.notes.item(0).data(Qt.UserRole)
        deleted=self.window.notes.item(1).data(Qt.UserRole)
        self.window.activate_note_card(deleted)
        self.window.delete();self.app.processEvents()
        self.assertEqual(self.window.current,previous)
        self.assertEqual(self.window.notes.currentItem().data(Qt.UserRole),previous)
        self.assertEqual(self.window.editor_stack.currentIndex(),1)
        self.assertTrue(self.window.editor.isEnabled())

    def test_delete_first_opens_next_and_last_shows_empty(self):
        deleted=self.window.notes.item(0).data(Qt.UserRole)
        remaining=self.window.notes.item(1).data(Qt.UserRole)
        self.window.activate_note_card(deleted);self.window.delete();self.app.processEvents()
        self.assertEqual(self.window.current,remaining)
        self.assertEqual(self.window.editor_stack.currentIndex(),1)
        self.window.delete();self.app.processEvents()
        self.assertIsNone(self.window.current)
        self.assertEqual(self.window.notes.count(),0)
        self.assertEqual(self.window.editor_stack.currentIndex(),0)

    def test_delete_middle_prefers_previous_over_next(self):
        self.store.create_note();self.window.refresh_list();self.app.processEvents()
        ids=[self.window.notes.item(i).data(Qt.UserRole) for i in range(self.window.notes.count())]
        self.window.activate_note_card(ids[1]);self.window.delete();self.app.processEvents()
        self.assertEqual(self.window.current,ids[0])
        self.assertEqual(self.window.editor_stack.currentIndex(),1)

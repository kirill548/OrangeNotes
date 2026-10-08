import unittest
from unittest.mock import patch
from PySide6.QtWidgets import QApplication,QWidget
from PySide6.QtCore import Qt,QRect
from PySide6.QtGui import QFontMetrics
from app.widgets.companion_mascot import MiniCompanion
class BeeFirstUserTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
 def setUp(self):self.parent=QWidget();self.parent.resize(640,480);self.parent.show();self.pet=MiniCompanion(self.parent);self.app.processEvents()
 def tearDown(self):self.parent.close();self.parent.deleteLater();self.app.processEvents()
 def test_updates_are_plain_text_and_do_not_claim_extra_actions(self):
  text='<b>Заметка сохранена</b>'
  self.assertTrue(self.pet.notify_event(text,'save'));self.assertEqual(self.pet.bubble.text(),text);self.assertEqual(self.pet.bubble.textFormat(),Qt.PlainText)
  self.assertFalse(self.pet.notify_event(text,'save'));self.assertTrue(self.pet.notify_event(text,'error'))
 def test_empty_updates_do_not_invent_activity_and_history_is_bounded(self):
  previous=self.pet.bubble.text();self.assertFalse(self.pet.notify_event('   '));self.assertEqual(self.pet.bubble.text(),previous)
  for i in range(100):self.pet.notify_event('Реальное событие '+str(i),'save')
  self.assertEqual(len(self.pet._recent_events),16)
 def test_hidden_pet_never_reappears_for_events(self):
  self.pet.hide();self.assertFalse(self.pet.notify_event('Напоминание готово','reminder'));self.assertFalse(self.pet.isVisible());self.assertFalse(self.pet.timer.isActive())
 def test_bubble_expires_without_opening_chat_or_writing(self):
  activations=[];self.pet.activated.connect(lambda:activations.append(True));self.pet.notify_event('Заметка сохранена','save');self.pet.bubble_expired();self.assertFalse(self.pet.bubble.isVisible());self.assertEqual(activations,[])
 def test_dedupe_allows_same_update_after_thirty_seconds(self):
  with patch('app.widgets.companion_mascot.time.monotonic',return_value=100):self.assertTrue(self.pet.notify_event('Заметка сохранена','save'))
  with patch('app.widgets.companion_mascot.time.monotonic',return_value=131):self.assertTrue(self.pet.notify_event('Заметка сохранена','save'))
 def test_long_notification_needs_readable_full_text(self):
  text='Не удалось сохранить заметку: файл базы данных временно занят другим приложением. Попробуйте позже.'
  self.pet.notify_event(text,'error');self.assertEqual(self.pet.bubble.toolTip(),text)
  bounds=QFontMetrics(self.pet.bubble.font()).boundingRect(QRect(0,0,self.pet.bubble.width()-12,1000),Qt.TextWordWrap,self.pet.bubble.text())
  self.assertLessEqual(bounds.height(),self.pet.bubble.height()-6,'Bubble text clips at current fixed height')
if __name__=='__main__':unittest.main()

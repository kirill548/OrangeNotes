import unittest
from unittest.mock import patch
from PySide6.QtWidgets import QApplication,QWidget
from PySide6.QtCore import Qt,QPoint,QRect
from PySide6.QtTest import QTest
from PySide6.QtGui import QFontMetrics
from app.widgets.companion_mascot import MiniCompanion
class BeeFirstUserTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
 def setUp(self):self.parent=QWidget();self.parent.resize(640,480);self.parent.show();self.pet=MiniCompanion(self.parent);self.pet.set_pet_visible(True);self.app.processEvents()
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

 def test_idle_flight_moves_across_the_screen_and_returns(self):
  pet = self.pet
  home = pet.pos()
  self.assertTrue(pet._outing_timer.isActive())
  pet._outing_timer.stop()
  pet._start_outing()
  pet._flight.setCurrentTime(pet._flight.duration())
  pet._outing.setCurrentTime(pet._outing.duration() // 3)
  outbound = pet._facing_right
  self.assertEqual(len(pet._wingbeats), 2)
  self.assertTrue(all(not frame.isNull() for frame in pet._wingbeats))
  self.assertFalse(pet._turn_sprite.isNull())
  self.assertIs(pet._flight_sprite(0), pet._wingbeats[0])
  self.assertIs(pet._flight_sprite(0.08), pet._wingbeats[1])
  pet._outing.setCurrentTime(pet._outing.duration() // 2)
  self.assertGreaterEqual((pet.pos() - home).manhattanLength(), 80)
  self.assertTrue(pet.screen().availableGeometry().contains(pet.geometry()))
  self.assertIs(pet._flight_sprite(0), pet._bee)
  pet._outing.setCurrentTime(pet._outing.duration() * 2 // 3)
  self.assertIsNot(pet._facing_right, outbound)
  self.assertIs(pet._flight_sprite(0), pet._wingbeats[0])
  pet._outing.setCurrentTime(pet._outing.duration())
  self.assertEqual(pet.pos(), home)
  self.assertIsNone(pet._flight_home)
  self.assertTrue(pet._outing_timer.isActive())

 def test_mouse_interaction_stops_idle_flight_without_opening_chat(self):
  pet = self.pet
  activated = []
  pet.activated.connect(lambda: activated.append(True))
  pet._start_outing()
  pet._outing.setCurrentTime(pet._outing.duration() // 2)
  point = QPoint(100, 90)
  new_position = pet.pos()
  QTest.mousePress(pet, Qt.LeftButton, pos=point)
  self.assertIsNone(pet._flight_home)
  self.assertEqual(pet.pos(), new_position)
  self.assertEqual(activated, [])
  pet.set_pet_visible(False)
  self.assertFalse(pet._outing_timer.isActive())

if __name__=='__main__':unittest.main()

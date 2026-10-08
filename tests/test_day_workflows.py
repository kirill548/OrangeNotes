"""First-user daily planning and occurrence actions: temporary database, no native sends."""
import sys,tempfile,unittest
from datetime import datetime,timedelta
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import Qt,QCoreApplication,QEvent
from PySide6.QtWidgets import QApplication,QMessageBox
from PySide6.QtTest import QTest
from app.database.store import Store
from app.ui.window import Window
from app.ui.reminder_history import ReminderHistoryDialog

class DayWorkflows(unittest.TestCase):
 @classmethod
 def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.store=Store(Path(self.tmp.name)/'notes.db');self.w=Window(self.store,background_managed=True);self.w.clock.stop();self.w.show();self.app.processEvents();self.w.new_note();self.note=self.w.current;self.w.title.setText('Почта 東京');self.w.body.setPlainText('Посмотреть почту café 📝');self.w.save()
  self.remove=patch('app.services.windows_notifications.WindowsNotifications.remove',return_value={'ok':True});self.remove.start()
  self.warning=patch.object(QMessageBox,'warning',side_effect=AssertionError('Unexpected modal warning'));self.warning.start()
  self.information=patch.object(QMessageBox,'information',side_effect=AssertionError('Unexpected modal information'));self.information.start()
 def tearDown(self):
  for d in [self.w._day_dialog,self.w._history_dialog,self.w.panel]:
   if d:d.hide();d.close()
  self.w.debounce.stop();self.w._dirty=False;self.w.quitting=True;self.w.tray.hide();self.w.close();self.w.deleteLater();QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete);self.app.processEvents();self.store.db.close();self.tmp.cleanup();self.remove.stop();self.warning.stop();self.information.stop()
 def click(self,button):QTest.mouseClick(button,Qt.LeftButton);self.app.processEvents()
 def events(self):
  s=self.store;s.save_reminder(self.note,'repeat',None,list(range(7)),['09:00','18:30']);r=s.reminder(self.note);now=datetime.now().replace(microsecond=0)
  ids=[]
  for minutes,status in [(-20,'notified'),(-10,'pending'),(40,'pending')]:
   stamp=(now+timedelta(minutes=minutes)).isoformat(timespec='seconds')
   ids.append(s.execute('INSERT INTO reminder_events(reminder_id,scheduled_at,status,revision,title,body,token,due_at) VALUES (?,?,?,?,?,?,?,?)',(r['id'],stamp,status,r['revision'],'Почта 東京','Посмотреть почту café 📝','token'+str(minutes),stamp)).lastrowid)
  self.w.update_reminder_label();return ids
 def test_add_remove_day_preserves_schedule_and_text(self):
  self.events();before=self.store.reminder(self.note);self.click(self.w.day_button);self.assertTrue(self.w.day_focus.contains(self.note));self.click(self.w.day_button);self.assertFalse(self.w.day_focus.contains(self.note));self.assertEqual(self.store.reminder(self.note),before);self.assertEqual(self.w.body.toPlainText(),'Посмотреть почту café 📝')
 def test_create_in_day_and_midnight_reset_is_not_deletion(self):
  self.w.scope=('myday',None);self.w.refresh_nav();self.w.refresh_list();self.w.new_note();new=self.w.current;self.assertEqual(self.w.scope,('myday',None));self.assertTrue(self.w.day_focus.contains(new));self.assertEqual(self.w.notes.count(),1)
  tomorrow=datetime.now().replace(hour=0,minute=0,second=1,microsecond=0)+timedelta(days=1)
  class Clock(datetime):
   @classmethod
   def now(cls,tz=None):return tomorrow
  with patch('app.services.day_focus.datetime',Clock),patch('app.ui.window.datetime',Clock):
   self.w.tick();self.assertEqual(self.w.notes.count(),0);self.assertEqual(self.w.day_focus.selected_ids(),[])
  self.assertEqual(len(self.store.rows('SELECT id FROM notes')),2);self.assertTrue(self.store.rows('SELECT note_id FROM note_days WHERE note_id=?',(new,)))
 def test_done_affects_earliest_occurrence_only(self):
  ids=self.events();self.click(self.w.done_reminder_button);self.assertEqual(self.store.rows('SELECT status FROM reminder_events WHERE id=?',(ids[0],))[0][0],'done');self.assertEqual(self.store.rows('SELECT status FROM reminder_events WHERE id=?',(ids[1],))[0][0],'pending');self.assertEqual(self.store.rows('SELECT status FROM reminder_events WHERE id=?',(ids[2],))[0][0],'pending');self.assertEqual(self.store.reminder(self.note)['times'],['09:00','18:30']);self.assertEqual(self.w.body.toPlainText(),'Посмотреть почту café 📝')
 def test_snooze_actual_due_persists_restart(self):
  ids=self.events();before=datetime.now();self.click(self.w.snooze_reminder_button);after=datetime.now();row=self.store.rows('SELECT * FROM reminder_events WHERE id=?',(ids[0],))[0];due=datetime.fromisoformat(row['due_at']);self.assertLessEqual(before+timedelta(minutes=10,seconds=-1),due);self.assertLessEqual(due,after+timedelta(minutes=10));self.assertEqual(row['status'],'pending');self.assertNotEqual(row['due_at'],row['scheduled_at'])
  other=Store(self.store.path)
  try:self.assertEqual(other.rows('SELECT due_at FROM reminder_events WHERE id=?',(ids[0],))[0][0],row['due_at']);self.assertEqual(other.reminder(self.note)['times'],['09:00','18:30'])
  finally:other.db.close()
 def test_history_unicode_status_reopen_cleanup(self):
  self.events();self.click(self.w.done_reminder_button)
  for _ in range(12):
   self.click(self.w.reminder_history_button);d=self.w._history_dialog;self.assertIsInstance(d,ReminderHistoryDialog);text='\n'.join(d.events.item(i).text() for i in range(d.events.count()));self.assertIn('Выполнено',text);self.assertIn('Посмотреть почту café 📝',text);self.assertIn('Почта 東京',text);d.close();QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete);self.app.processEvents();self.assertIsNone(self.w._history_dialog)
  self.assertFalse(self.w.findChildren(ReminderHistoryDialog))

if __name__=='__main__':unittest.main(verbosity=2)


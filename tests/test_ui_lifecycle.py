"""Widget lifecycle regressions using temporary SQLite only."""
import sys,tempfile,time,unittest,gc
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QTimer,QCoreApplication,QEvent,Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication,QDialog,QPushButton
from shiboken6 import isValid
from app.database.store import Store
from app.ui.window import Window
class LifecycleTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls): cls.app=QApplication.instance() or QApplication([])
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(); self.store=Store(Path(self.tmp.name)/'test.sqlite'); self.w=Window(self.store); self.w.show(); self.w.new_note(); self.w.save(); self.w.clock.stop()
 def flush(self):
  QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete); self.app.processEvents(); gc.collect()
 def tearDown(self):
  if isValid(self.w):
   self.w.quitting=True; self.w.tray.hide(); self.w.close(); self.w.deleteLater(); self.flush()
  if self.store.db is not None: self.store.db.close()
  self.tmp.cleanup()
 def test_repeated_icon_dialog_cleanup(self):
  before=len(self.w.findChildren(QDialog)); seen=[]
  def close_picker():
   dialog=self.app.activeModalWidget()
   seen.append(dialog is not None and all(not b.autoDefault() for b in dialog.findChildren(QPushButton)))
   if dialog: dialog.reject()
  for _ in range(30):
   QTimer.singleShot(20,close_picker); self.w.choose_icon(); self.flush()
  self.assertEqual(len(seen),30); self.assertTrue(all(seen))
  self.assertLessEqual(len(self.w.findChildren(QDialog)),before+1)
 def test_repeated_reminder_panels_cleanup(self):
  for _ in range(100): self.w.edit_reminder(); self.w.close_reminder_panel(); self.flush()
  self.assertLessEqual(len(self.w.findChildren(QDialog)),1)
 def test_rapid_close_flushes_debounce(self):
  note=self.w.current; self.w.title.setText('Must persist'); self.assertTrue(self.w.debounce.isActive()); self.w.quitting=True; self.w.tray.hide(); self.w.close()
  self.assertEqual(self.store.rows('SELECT title FROM notes WHERE id=?',(note,))[0][0],'Must persist')
 def test_destroy_before_startup_tick(self):
  calls=[]; self.w.scheduler.tick=lambda: calls.append(True)
  self.w.deleteLater(); self.flush(); self.store.db.close(); self.store.db=None; QTest.qWait(1100)
  self.assertEqual(calls,[])
  self.store.db=__import__('sqlite3').connect(Path(self.tmp.name)/'test.sqlite')
if __name__=='__main__': unittest.main()



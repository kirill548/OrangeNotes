"""Deterministic local stress tests; all data lives in temporary databases.
Run from notes_app: python -m unittest discover -s tests -p test_stress.py -v
Large text is a workload surrogate, not a measured tokenizer token count.
"""
import os,sys,random,sqlite3,tempfile,time,unittest
from pathlib import Path
from datetime import datetime,timedelta
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PySide6.QtWidgets import QApplication,QMessageBox
from PySide6.QtCore import QTimer, QCoreApplication, QEvent
from app.database.store import Store
from app.ui.window import Window


def memory_bytes():
    if os.name!='nt':return None
    import ctypes
    from ctypes import wintypes
    class Counters(ctypes.Structure):
        _fields_=[('cb',wintypes.DWORD),('PageFaultCount',wintypes.DWORD)]+[(name,ctypes.c_size_t) for name in ['PeakWorkingSetSize','WorkingSetSize','QuotaPeakPagedPoolUsage','QuotaPagedPoolUsage','QuotaPeakNonPagedPoolUsage','QuotaNonPagedPoolUsage','PagefileUsage','PeakPagefileUsage']]
    data=Counters();data.cb=ctypes.sizeof(data)
    kernel=ctypes.WinDLL('kernel32');kernel.GetCurrentProcess.restype=wintypes.HANDLE
    query=ctypes.WinDLL('psapi').GetProcessMemoryInfo
    query.argtypes=[wintypes.HANDLE,ctypes.POINTER(Counters),wintypes.DWORD]
    query.restype=wintypes.BOOL
    return data.WorkingSetSize if query(kernel.GetCurrentProcess(),ctypes.byref(data),data.cb) else None


class LocalStress(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.store=Store(Path(self.directory.name)/'notes.db');self.window=Window(self.store)
        self.window.clock.stop();self.window.show();self.app.processEvents()
    def tearDown(self):
        w=self.window
        if w.panel:w.panel.hide()
        w.debounce.stop();w._dirty=False;w.quitting=True;w.tray.hide();w.close();w.deleteLater()
        QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)
        self.app.processEvents();self.store.db.close();self.directory.cleanup()
    def integrity(self):
        self.assertEqual(self.store.rows('PRAGMA integrity_check')[0][0],'ok');self.assertFalse(self.store.rows('PRAGMA foreign_key_check'))
    def test_large_unicode_code_log_context(self):
        w=self.window;s=self.store;before=memory_bytes()
        # 130,000+ whitespace-separated words and >1MB Unicode/code/log text.
        text=('Ошибка INFO 2026-10-02 ключ=значение café 東京 📝 def save(x): return x + 1\n'*11000)
        self.assertGreater(len(text.split()),130000)
        self.assertGreater(len(text.encode('utf-8')),1000000)
        started=time.perf_counter();w.new_note();note=w.current;w.title.setText('Large Unicode log');w.body.setPlainText(text);self.assertTrue(w.save());save_seconds=time.perf_counter()-started
        w.new_note();w.title.setText('Small note');w.body.setPlainText('Independent');w.save()
        started=time.perf_counter();w.load_note(note);load_seconds=time.perf_counter()-started
        self.assertEqual(w.body.toPlainText(),text)
        w.search.setText('東京');self.assertEqual(w.notes.count(),1);w.reset_filters();self.assertEqual(w.notes.count(),2)
        after=memory_bytes();print(f'CONTEXT chars={len(text)} words={len(text.split())} utf8_bytes={len(text.encode("utf-8"))} save_s={save_seconds:.3f} load_s={load_seconds:.3f} memory_before={before} memory_after={after}')
        self.integrity()
    def test_locked_writes_keep_edits_and_correct_target(self):
        w=self.window;s=self.store;w.new_note();target=w.current;w.title.setText('Original');w.body.setPlainText('original');w.save();other=s.create_note();s.save_note(other,'Other','other',None,False,'')
        connection=sqlite3.connect(s.path);connection.execute('BEGIN IMMEDIATE')
        try:
            w.body.setPlainText('Unsaved Unicode 東京');count=len(s.rows('SELECT * FROM notes'));beat=[]
            with patch.object(QMessageBox,'warning',return_value=QMessageBox.Ok):
                for action in [lambda:w.save(),lambda:w.search.setText('Unsaved'),w.delete,w.duplicate_note]:
                    started=time.perf_counter();action();elapsed=time.perf_counter()-started;self.assertLess(elapsed,1.25)
                    self.assertEqual(w.current,target);self.assertEqual(w.body.toPlainText(),'Unsaved Unicode 東京');self.assertTrue(w._dirty)
                    QTimer.singleShot(0,lambda:beat.append(True));self.app.processEvents()
            self.assertEqual(len(beat),4);self.assertEqual(len(s.rows('SELECT * FROM notes')),count)
            self.assertEqual(s.rows('SELECT deleted FROM notes WHERE id=?',(target,))[0][0],0)
            self.assertEqual(s.rows('SELECT body FROM notes WHERE id=?',(other,))[0][0],'other')
        finally:connection.rollback();connection.close()
        self.assertTrue(w.save());self.assertIn('Unsaved Unicode',s.rows('SELECT body FROM notes WHERE id=?',(target,))[0][0]);self.integrity()
    def test_scheduler_locked_then_retries_once(self):
        w=self.window;s=self.store;note=s.create_note();past=datetime.now()-timedelta(seconds=5);s.save_reminder(note,'once',past.isoformat(timespec='seconds'),[],[])
        s.execute('UPDATE reminders SET created_utc=NULL,created_at=?',((past-timedelta(seconds=2)).isoformat(timespec='seconds'),));notices=[];w.scheduler.notify=lambda *args:notices.append(args)
        lock=sqlite3.connect(s.path);lock.execute('BEGIN IMMEDIATE')
        try:
            started=time.perf_counter();w.tick();self.assertLess(time.perf_counter()-started,1.25);self.assertEqual(notices,[])
        finally:lock.rollback();lock.close()
        w.tick();w.tick();self.assertEqual(len(notices),1);self.assertEqual(s.rows("SELECT count(*) FROM reminder_events WHERE status='notified'")[0][0],1);self.integrity()
    def test_monkey_seed_20261002_400_actions(self):
        rng=random.Random(20261002);w=self.window;s=self.store;w.new_note();w.title.setText('Anchor');w.save();actions=0
        with patch.object(QMessageBox,'warning',return_value=QMessageBox.Ok),patch.object(QMessageBox,'question',return_value=QMessageBox.No):
            for index in range(400):
                choice=rng.randrange(10)
                if choice==0:w.new_note()
                elif choice==1 and w.current:w.title.setText(f'Note {index} — café 東京')
                elif choice==2 and w.current:w.body.insertPlainText(f' line {index} 📝')
                elif choice==3:w.save()
                elif choice==4:w.search.setText(rng.choice(['','Note','東京','absent']))
                elif choice==5:w.reset_filters()
                elif choice==6 and w.current:w.archive()
                elif choice==7 and w.current:w.delete()
                elif choice==8:w.undo_change()
                elif choice==9 and w.current:w.duplicate_note()
                self.app.processEvents();actions+=1
                if index%40==0:self.integrity()
                if w.current:self.assertTrue(s.rows('SELECT id FROM notes WHERE id=?',(w.current,)))
        self.assertTrue(w.save());self.integrity();print(f'MONKEY seed=20261002 steps={actions} notes={len(s.rows("SELECT id FROM notes"))}')

if __name__=='__main__':unittest.main(verbosity=2)

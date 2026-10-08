"""Concurrent commits and debounce ordering against disposable databases."""
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from app.database.store import Store


class WALConcurrency(unittest.TestCase):
    def test_parallel_connections_commit_without_lost_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'notes.sqlite3'
            parent=Store(path)
            self.assertEqual(parent.rows('PRAGMA journal_mode')[0][0],'wal')
            self.assertEqual(parent.rows('PRAGMA busy_timeout')[0][0],5000)
            first=parent.create_note();second=parent.create_note()
            parent.save_reminder(second,'once','2030-01-01T10:00:00',[],[])
            reminder=parent.reminder(second)['id']
            parent.execute('INSERT INTO reminder_events(reminder_id,scheduled_at) VALUES (?,?)',(reminder,'2030-01-01T10:00:00'))
            parent.db.commit()
            barrier=threading.Barrier(2)
            def write(note):
                store=Store(path)
                try:
                    barrier.wait(timeout=10)
                    for i in range(120):
                        store.save_note(note,str(i),'unique-'+str(note)+'-'+str(i),None,False,'')
                        if note==second:
                            with store.db:
                                store.execute('UPDATE reminder_events SET attempts=attempts+1,status=? WHERE reminder_id=?',('notified' if i%2 else 'pending',reminder))
                finally:store.db.close()
            try:
                with ThreadPoolExecutor(max_workers=2) as pool:
                    a=pool.submit(write,first);b=pool.submit(write,second)
                    a.result(timeout=30);b.result(timeout=30)
                for note in (first,second):
                    self.assertEqual(parent.rows('SELECT body FROM notes WHERE id=?',(note,))[0][0],'unique-'+str(note)+'-119')
                self.assertEqual(parent.rows('PRAGMA integrity_check')[0][0],'ok')
                self.assertFalse(parent.rows('PRAGMA foreign_key_check'))
                event=parent.rows('SELECT attempts,status FROM reminder_events WHERE reminder_id=?',(reminder,))[0]
                self.assertEqual(tuple(event),(120,'notified'))
            finally:parent.db.close()


class AutosaveOrdering(unittest.TestCase):
    def test_pending_timer_switch_new_search_and_explicit_save(self):
        from PySide6.QtWidgets import QApplication
        from PySide6.QtTest import QTest
        from app.ui.window import Window
        app=QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            store=Store(Path(directory)/'notes.sqlite3')
            with patch.object(Window,'tick',lambda self:None):window=Window(store,background_managed=True)
            window.clock.stop();window.startup_tick.stop();window.show()
            try:
                window.new_note();first=window.current
                window.body.setPlainText('FIRST unsaved 東京');window.save()
                window.body.setPlainText('FIRST final 🐝')
                window.new_note();second=window.current
                window.body.setPlainText('SECOND final');window.search.setText('SECOND')
                window.save();QTest.qWait(700)
                self.assertIn('FIRST final',store.rows('SELECT body FROM notes WHERE id=?',(first,))[0][0])
                self.assertIn('SECOND final',store.rows('SELECT body FROM notes WHERE id=?',(second,))[0][0])
                self.assertNotIn('SECOND',store.rows('SELECT body FROM notes WHERE id=?',(first,))[0][0])
                self.assertFalse(store.db.in_transaction)
            finally:
                window.debounce.stop();window._dirty=False;window.quitting=True;window.tray.hide();window.close();window.deleteLater();app.processEvents();store.db.close()

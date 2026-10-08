"""Independent worker tests never use real notifications or the user's database."""
from datetime import datetime,timedelta
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock,patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.database.store import Store
from app.services import reminder_worker as worker


class WorkerTests(unittest.TestCase):
    def setUp(self):
        scratch=Path(__file__).resolve().parents[3]/'work'
        scratch.mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(prefix='worker-tests-',dir=scratch)
        self.directory=Path(self.temp.name)
        self.path=self.directory/'notes.sqlite3'

    def tearDown(self):
        self.temp.cleanup()

    def event(self):
        return {'title':'title 🧡','body':'literal <tag> & value','token':'secret-token','scheduled_at':(datetime.now()-timedelta(minutes=2)).isoformat()}

    def test_callback_preserves_plain_snapshot_and_requires_exact_ack(self):
        store=Mock();store.notification_event.return_value=self.event()
        transport=Mock();transport.show.return_value={'submitted':True,'historyVerified':True}
        delivered=Mock()
        receipt=worker.notification_callback(store,transport,delivered)('fallback',1,42)
        self.assertTrue(receipt['submitted'])
        args=transport.show.call_args.args
        self.assertIn('Пропущенное напоминание',args[1])
        self.assertIn('literal <tag> & value',args[1])
        self.assertEqual(args[2:],(42,'secret-token'))
        delivered.assert_called_once_with(42)
        for receipt in [None,{}, {'submitted':True,'historyVerified':False},{'submitted':'true','historyVerified':True}]:
            transport.show.return_value=receipt
            with self.assertRaises(OSError): worker.notification_callback(store,transport)('fallback',1,42)
        store.notification_event.return_value=None
        with self.assertRaises(OSError):worker.notification_callback(store,transport)('fallback',1,42)

    def test_callback_fresh_event_has_no_overdue_prefix(self):
        event=self.event();event['scheduled_at']=datetime.now().isoformat()
        store=Mock();store.notification_event.return_value=event
        transport=Mock();transport.show.return_value={'submitted':True,'historyVerified':True}
        worker.notification_callback(store,transport)('fallback',1,42)
        self.assertEqual(transport.show.call_args.args[1],event['body'])

    @unittest.skipUnless(os.name=='nt','Windows byte locks')
    def test_singleton_lock_release_and_second_instance(self):
        first=worker.WorkerLock(self.directory/'worker.lock')
        second=worker.WorkerLock(self.directory/'worker.lock')
        try:
            self.assertTrue(first.acquire())
            self.assertFalse(second.acquire())
            first.release()
            self.assertTrue(second.acquire())
        finally:
            first.release();second.release()

    @unittest.skipUnless(os.name=='nt','Windows byte locks')
    def test_process_termination_releases_worker_lock(self):
        ready=self.directory/'ready';lockpath=self.directory/'worker.lock'
        source="""
import sys,time
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from app.services.reminder_worker import WorkerLock
lock=WorkerLock(sys.argv[2]);assert lock.acquire()
Path(sys.argv[3]).write_text('ready')
time.sleep(60)
"""
        child=subprocess.Popen([sys.executable,'-c',source,str(Path(__file__).resolve().parents[1]),str(lockpath),str(ready)],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        try:
            deadline=time.monotonic()+10
            while not ready.exists() and child.poll() is None and time.monotonic()<deadline:time.sleep(.02)
            self.assertTrue(ready.exists())
            child.terminate();child.communicate(timeout=10)
            recovered=worker.WorkerLock(lockpath)
            try:self.assertTrue(recovered.acquire())
            finally:recovered.release()
        finally:
            if child.poll() is None:child.kill();child.communicate(timeout=10)

    @unittest.skipUnless(os.name=='nt','Windows worker')
    def test_worker_real_store_batch_ten_and_heartbeat(self):
        store=Store(self.path)
        past=(datetime.now()-timedelta(minutes=2)).isoformat(timespec='seconds')
        try:
            for number in range(11):
                note=store.create_note()
                store.save_note(note,'note '+str(number),'<p>☐ Body &lt;tag&gt; 🧡</p>',None,False,'')
                store.save_reminder(note,'once',past,[],[])
            store.execute('UPDATE reminders SET created_utc=NULL,created_at=?,checked_through=NULL',((datetime.now()-timedelta(minutes=3)).isoformat(timespec='seconds'),))
        finally:store.db.close()
        transport=Mock();transport.show.return_value={'submitted':True,'historyVerified':True}
        self.assertEqual(worker.run_worker(self.path,transport=transport,max_iterations=1),0)
        self.assertEqual(transport.show.call_count,10)
        store=Store(self.path)
        try:
            self.assertEqual(store.rows("SELECT COUNT(*) FROM reminder_events WHERE status='notified'")[0][0],10)
            self.assertEqual(store.rows("SELECT COUNT(*) FROM reminder_events WHERE status='pending'")[0][0],1)
        finally:store.db.close()
        status=json.loads((self.directory/'worker_status.json').read_text(encoding='utf8'))
        self.assertEqual(status['pid'],os.getpid())
        self.assertFalse(status['running'])
        self.assertIsNone(status['last_error'])
        self.assertIsNotNone(status['last_delivery'])
        self.assertIn('+00:00',status['heartbeat_utc'])
        self.assertIn('heartbeat_local',status)
        self.assertFalse(list(self.directory.glob('.worker-status-*')))
        self.assertTrue((self.directory/'reminder_worker.log').exists())
        lock=worker.WorkerLock(self.path.with_name(self.path.name+'.worker.lock'))
        try:self.assertTrue(lock.acquire())
        finally:lock.release()

    @unittest.skipUnless(os.name=='nt','Windows worker')
    def test_worker_io_failure_is_recorded_and_retries(self):
        scheduler=Mock();scheduler.tick.side_effect=[sqlite3.OperationalError('database locked'),None]
        with patch.object(worker,'Scheduler',return_value=scheduler):
            self.assertEqual(worker.run_worker(self.path,max_iterations=2,poll_interval=0),0)
        self.assertEqual(scheduler.tick.call_count,2)
        status=json.loads((self.directory/'worker_status.json').read_text(encoding='utf8'))
        self.assertIsNone(status['last_error'])
        self.assertIn('database locked',(self.directory/'reminder_worker.log').read_text(encoding='utf8'))
        with patch.object(worker,'Scheduler',return_value=Mock(tick=Mock(side_effect=OSError('transport refused')))):
            self.assertEqual(worker.run_worker(self.path,max_iterations=1),0)
        status=json.loads((self.directory/'worker_status.json').read_text(encoding='utf8'))
        self.assertEqual(status['last_error'],'transport refused')

    def test_status_replace_failure_preserves_previous_readable_json(self):
        previous={'pid':123,'running':True}
        worker._write_status(self.directory,previous)
        with patch.object(worker.os,'replace',side_effect=PermissionError('locked status')):
            with self.assertRaises(PermissionError):worker._write_status(self.directory,{'pid':456})
        self.assertEqual(json.loads((self.directory/'worker_status.json').read_text(encoding='utf8')),previous)
        self.assertFalse(list(self.directory.glob('.worker-status-*')))

    def test_blocked_os_setting_is_persisted_and_logged_without_note_payload(self):
        transport=Mock()
        transport.status.return_value={'setting':'DisabledForUser','fallback_required':True}
        with patch.object(worker,'Scheduler',return_value=Mock()):
            self.assertEqual(worker.run_worker(self.path,transport=transport,max_iterations=2,poll_interval=0),0)
        state=json.loads((self.directory/'worker_status.json').read_text(encoding='utf-8'))
        self.assertEqual(state['notification_status']['setting'],'DisabledForUser')
        log=(self.directory/'reminder_worker.log').read_text(encoding='utf-8')
        self.assertEqual(log.count('Native notification status:'),1)
        self.assertIn('DisabledForUser',log)
        transport.show.assert_not_called()

    def test_import_has_no_qt_dependency(self):
        source="import sys;sys.path.insert(0,sys.argv[1]);import app.services.reminder_worker;assert not any(name.startswith('PySide6') for name in sys.modules)"
        result=subprocess.run([sys.executable,'-c',source,str(Path(__file__).resolve().parents[1])],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)


if __name__=='__main__':unittest.main(verbosity=2)


class NativeReceiptReconciliationTests(unittest.TestCase):
    def test_external_gui_changes_withdraw_native_receipts_after_worker_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'notes.sqlite3'
            store = Store(path)
            gui = Store(path)
            try:
                store.execute('CREATE TABLE native_notification_receipts(platform TEXT,event_id INTEGER,native_id TEXT,PRIMARY KEY(platform,event_id))')
                events = []
                for number in range(5):
                    note = store.create_note()
                    store.save_reminder(note, 'once', datetime.now().isoformat(timespec='seconds'), [], [])
                    reminder = store.rows('SELECT id,revision FROM reminders WHERE note_id=?', (note,))[0]
                    event = store.execute("INSERT INTO reminder_events(reminder_id,scheduled_at,status,revision) VALUES(?,?,'notified',?)", (reminder['id'],datetime.now().isoformat(),reminder['revision'])).lastrowid
                    store.execute('INSERT INTO native_notification_receipts VALUES(?,?,?)', ('linux',event,str(100+number)))
                    events.append((event,note))
                gui.execute("UPDATE reminder_events SET status='done' WHERE id=?", (events[0][0],))
                gui.execute("UPDATE reminder_events SET status='pending',due_at=? WHERE id=?", ((datetime.now()+timedelta(minutes=10)).isoformat(),events[1][0]))
                gui.execute("UPDATE reminder_events SET status='cancelled' WHERE id=?", (events[2][0],))
                gui.execute('DELETE FROM notes WHERE id=?', (events[3][1],))
                transport = Mock(); transport.identifiers = {}
                worker.reconcile_notifications(store,transport,'linux')
                self.assertEqual([call.args[0] for call in transport.remove.call_args_list], [event for event,note in events[:4]])
                self.assertEqual(transport.identifiers[events[0][0]], 100)
                remaining = store.rows('SELECT event_id FROM native_notification_receipts')
                self.assertEqual([row[0] for row in remaining], [events[4][0]])
                worker.reconcile_notifications(store,transport,'linux')
                self.assertEqual(transport.remove.call_count,4)
            finally:
                gui.db.close(); store.db.close()

    def test_failed_withdrawal_keeps_receipt_for_next_poll(self):
        store = Mock()
        store.rows.side_effect = [[(7,'42')], []]
        transport = Mock(); transport.identifiers = {}; transport.remove.side_effect = OSError('desktop unavailable')
        with self.assertRaises(OSError):
            worker.reconcile_notifications(store,transport,'linux')
        store.execute.assert_not_called()

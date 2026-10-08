import tempfile
import unittest
import subprocess
import sys
import time
from pathlib import Path
from datetime import datetime, timezone, timedelta
from unittest.mock import patch
from app.database.store import Store
from app.services.scheduler import Scheduler, occurrences
from app.utils.timezones import resolve_local, utc_stamp


class TimezoneRecovery(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'notes.db'
        self.store = Store(self.path)
        self.note = self.store.create_note()

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def test_ambiguous_hour_has_one_first_fold_occurrence(self):
        r = {'enabled': 1, 'mode': 'repeat', 'days': [6], 'times': ['02:30'], 'timezone_id': 'Europe/Berlin'}
        with patch('app.services.scheduler.device_zone', return_value='UTC'), patch('app.utils.timezones.device_zone', return_value='UTC'):
            result = occurrences(r, datetime(2026,10,25,0), datetime(2026,10,25,3))
        self.assertEqual(result, [datetime(2026,10,25,0,30)])
        self.assertEqual(resolve_local(datetime(2026,10,25,2,30),'Europe/Berlin').fold, 0)

    def test_checkpoint_crosses_fold_without_duplicate_delivery(self):
        with patch('app.utils.timezones.device_zone',return_value='Europe/Berlin'):
            self.store.save_reminder(self.note,'repeat',None,[6],['02:30'])
        self.store.execute('UPDATE reminders SET created_at=?,created_utc=?,checked_through=?,checked_through_utc=?',
                           ('2026-10-25T01:00:00','2026-10-24T23:00:00','2026-10-25T02:00:00','2026-10-25T00:00:00'))
        with patch('app.services.scheduler.device_zone',return_value='Europe/Berlin'),patch('app.utils.timezones.device_zone',return_value='Europe/Berlin'):
            task=Scheduler(self.store,lambda *args: None)
            task._enqueue(datetime(2026,10,25,2,45,fold=0))
            task._enqueue(datetime(2026,10,25,2,15,fold=1))
            task._enqueue(datetime(2026,10,25,2,45,fold=1))
        rows=self.store.rows('SELECT scheduled_utc,fold FROM reminder_events')
        self.assertEqual([(r[0],r[1]) for r in rows],[('2026-10-25T00:30:00',0)])
        self.assertEqual(self.store.reminder(self.note)['checked_through_utc'],'2026-10-25T01:45:00')

    def test_gap_moves_forward_first_valid_minute(self):
        result = resolve_local(datetime(2026,3,29,2,30), 'Europe/Berlin')
        self.assertEqual(result.hour, 3)
        self.assertEqual(result.minute, 0)
        self.assertEqual(utc_stamp(result), '2026-03-29T01:00:00')

    def test_device_zone_change_keeps_original_repeat_zone(self):
        r = {'enabled':1,'mode':'repeat','days':[2],'times':['09:00'],'timezone_id':'Europe/Berlin'}
        with patch('app.services.scheduler.device_zone', return_value='America/New_York'), patch('app.utils.timezones.device_zone', return_value='America/New_York'):
            self.assertEqual(occurrences(r,datetime(2026,10,7,0),datetime(2026,10,7,12)),[datetime(2026,10,7,3)])

    def test_one_off_utc_is_persisted_and_claimed_after_travel(self):
        with patch('app.utils.timezones.device_zone', return_value='Europe/Berlin'):
            self.store.save_reminder(self.note,'once','2026-10-07T09:00:00',[],[])
        self.store.execute('UPDATE reminders SET created_at=?,created_utc=?',('2026-10-07T08:00:00','2026-10-07T06:00:00'))
        with patch('app.services.scheduler.device_zone',return_value='America/New_York'), patch('app.utils.timezones.device_zone',return_value='America/New_York'):
            task=Scheduler(self.store, lambda *args: None)
            task._enqueue(datetime(2026,10,7,3))
            event=task.claim_next(datetime(2026,10,7,3))
        self.assertIsNotNone(event)
        self.assertEqual(event['scheduled_utc'],'2026-10-07T07:00:00')
        self.assertEqual(event['utc_offset'],7200)
        self.assertEqual(event['scheduled_at'],'2026-10-07T09:00:00')

    def test_unknown_zone_is_actionable_value_error(self):
        from app.utils.timezones import zone
        with self.assertRaisesRegex(ValueError,'Unknown reminder timezone'):
            zone('Invalid/MissingZone')

    def test_live_device_zone_cache_refresh(self):
        import app.utils.timezones as module
        with patch('tzlocal.reload_localzone') as reload_zone, patch('tzlocal.get_localzone_name', side_effect=['Europe/Berlin','America/New_York']):
            self.assertEqual(module.refresh_device_zone(force=True),'Europe/Berlin')
            self.assertEqual(module.refresh_device_zone(force=True),'America/New_York')
            self.assertEqual(reload_zone.call_count,2)
        module.refresh_device_zone(force=True)

    def test_created_utc_is_authoritative_after_travel(self):
        with patch('app.utils.timezones.device_zone',return_value='Europe/Berlin'):
            self.store.save_reminder(self.note,'repeat',None,[2],['09:00'])
        # Edited while in New York: origin zone stays Berlin, created local display is New York.
        self.store.execute('UPDATE reminders SET created_at=?,created_utc=?,checked_through=NULL,checked_through_utc=NULL',
                           ('2026-10-07T02:00:00','2026-10-07T06:00:00'))
        with patch('app.services.scheduler.device_zone',return_value='America/New_York'),patch('app.utils.timezones.device_zone',return_value='America/New_York'):
            Scheduler(self.store,lambda *args: None)._enqueue(datetime(2026,10,7,3))
        self.assertEqual(self.store.rows('SELECT scheduled_utc FROM reminder_events')[0][0],'2026-10-07T07:00:00')

    def test_migration_preserves_history_and_is_idempotent(self):
        self.store.save_reminder(self.note,'once','2026-10-07T09:00:00',[],[])
        rid=self.store.reminder(self.note)['id']
        self.store.execute("INSERT INTO reminder_events(reminder_id,scheduled_at,status,token,due_at) VALUES (?,?,'done','history',?)",(rid,'2026-10-07T09:00:00','2026-10-07T09:10:00'))
        self.store.execute('UPDATE reminders SET timezone_id=NULL,created_utc=NULL,once_utc=NULL')
        self.store.db.close()
        self.store=Store(self.path)
        first=dict(self.store.rows('SELECT * FROM reminder_events')[0])
        self.assertEqual(first['status'],'done')
        self.assertEqual(first['token'],'history')
        self.assertIsNotNone(first['due_utc'])
        self.store.db.close()
        self.store=Store(self.path)
        self.assertEqual(dict(self.store.rows('SELECT * FROM reminder_events')[0]),first)

    def test_crash_during_snooze_or_reset_rolls_back_wal(self):
        self.store.save_reminder(self.note,'once','2026-10-07T09:00:00',[],[])
        reminder=self.store.reminder(self.note)
        self.store.execute("INSERT INTO reminder_events(reminder_id,scheduled_at,status,token,due_at) VALUES (?,?,'pending','stable',?)",(reminder['id'],'2026-10-07T09:00:00','2026-10-07T09:00:00'))
        event=self.store.rows('SELECT id FROM reminder_events')[0][0]
        for action in ('snooze','reset'):
            script='''import os,sys
from datetime import datetime
from app.database.store import Store
store=Store(sys.argv[1])
class CrashConnection:
    def __enter__(self): store_connection.__enter__();return self
    def __exit__(self,*args): return store_connection.__exit__(*args)
    def __getattr__(self,name): return getattr(store_connection,name)
    def execute(self,sql,args=()):
        result=store_connection.execute(sql,args)
        if sql.startswith('UPDATE reminder_events') or sql.startswith('UPDATE reminders SET enabled=0'): os._exit(77)
        return result
store_connection=store.db
store.db=CrashConnection()
if sys.argv[2]=='snooze': store.handle_notification_action(int(sys.argv[3]),'stable','snooze',datetime(2026,10,7,9))
else: store.save_reminder(1,None,None,[],[])
raise RuntimeError('Crash injection was not reached')
'''
            result=subprocess.run([sys.executable,'-c',script,str(self.path),action,str(event)],timeout=10)
            self.assertEqual(result.returncode,77)
            row=self.store.rows('SELECT * FROM reminder_events')[0]
            self.assertEqual((row['token'],row['status'],row['due_at']),('stable','pending','2026-10-07T09:00:00'))
            self.assertEqual(self.store.rows('PRAGMA integrity_check')[0][0],'ok')
            self.assertEqual(self.store.rows('PRAGMA foreign_key_check'),[])

    def test_ten_thousand_schedule_scan_is_bounded(self):
        stamp='2026-10-07T00:00:00'
        with self.store.db:
            self.store.db.executemany('INSERT INTO notes(id,updated_at) VALUES (?,?)',[(i,stamp) for i in range(2,10002)])
            self.store.db.executemany("INSERT INTO reminders(note_id,mode,created_at,timezone_id,enabled) VALUES (?,'repeat',?,'UTC',?)",[(i,stamp,i%2) for i in range(2,10002)])
        with self.store.db:
            self.store.db.execute('INSERT INTO reminder_days SELECT id,2 FROM reminders')
            self.store.db.execute("INSERT INTO reminder_times SELECT id,'18:30' FROM reminders")
        queries=[]
        self.store.db.set_trace_callback(queries.append)
        began=time.monotonic()
        with patch('app.services.scheduler.device_zone',return_value='UTC'),patch('app.utils.timezones.device_zone',return_value='UTC'):
            Scheduler(self.store,lambda *args: None)._enqueue(datetime(2026,10,7,0,1))
        elapsed=time.monotonic()-began
        self.store.db.set_trace_callback(None)
        select_count=sum(q.startswith('SELECT') for q in queries)
        self.assertLess(select_count,10)
        self.assertLess(elapsed,15)
        print('10000 schedules: %.3fs, %d SELECTs' % (elapsed,select_count))

if __name__=='__main__': unittest.main()

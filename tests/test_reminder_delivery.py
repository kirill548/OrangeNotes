"""Isolated durable-delivery scenarios with a simulated local clock."""
import tempfile
import sqlite3
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from app.database import store as store_module
from app.database.store import Store
from app.services import scheduler


class Clock(datetime):
    value = datetime(2026, 10, 5, 8)

    @classmethod
    def now(cls):
        return cls.value


class DurableDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / 'notes.sqlite3'
        Clock.value = datetime(2026, 10, 5, 8)
        self.clock_patches = [patch.object(module, 'datetime', Clock) for module in (scheduler, store_module)]
        for item in self.clock_patches:
            item.start()
        self.store = Store(self.path)
        self.store.db.create_function('strftime',3,lambda *_: Clock.value.isoformat(timespec='seconds'))
        from app.utils.timezones import utc_stamp
        self.store.db.create_function('strftime',2,lambda *_: utc_stamp(Clock.value))
        self.delivered = []
        self.task = scheduler.Scheduler(self.store, self.notify)

    def tearDown(self):
        self.store.db.close()
        for item in reversed(self.clock_patches):
            item.stop()
        self.directory.cleanup()

    def notify(self, title, note_id, event_id):
        self.delivered.append((title, note_id, event_id))

    def create_note(self, title='Напоминание', body='<p>Текст заметки</p>', repeat=False):
        note_id = self.store.create_note()
        self.store.save_note(note_id, title, body, None, False, '')
        self.store.save_reminder(note_id, 'repeat' if repeat else 'once',
                                 None if repeat else '2026-10-05T09:00:00',
                                 list(range(7)) if repeat else [],
                                 ['09:00', '18:30'] if repeat else [])
        return note_id

    def drain(self, count=100):
        for _ in range(count):
            self.task.tick()

    def restart(self):
        self.store.db.close()
        self.store = Store(self.path)
        self.store.db.create_function('strftime',3,lambda *_: Clock.value.isoformat(timespec='seconds'))
        from app.utils.timezones import utc_stamp
        self.store.db.create_function('strftime',2,lambda *_: utc_stamp(Clock.value))
        self.task = scheduler.Scheduler(self.store, self.notify)

    def test_ten_simultaneous_notes_have_independent_events(self):
        notes = {self.create_note(f'Заметка {index}') for index in range(10)}
        Clock.value = datetime(2026, 10, 5, 9)
        self.drain()
        self.assertEqual({item[1] for item in self.delivered}, notes)
        self.assertEqual(len(self.delivered), 10)
        self.assertEqual(len({item[2] for item in self.delivered}), 10)
        self.restart()
        self.drain()
        self.assertEqual(len(self.delivered), 10)

    def test_restart_catches_up_fourteen_days_since_schedule_creation(self):
        self.create_note(repeat=True)
        Clock.value = datetime(2026, 10, 19, 8)
        self.restart()
        self.drain()
        self.assertEqual(len(self.delivered), 28)
        self.assertEqual(len({item[2] for item in self.delivered}), 28)

    def test_once_overdue_across_restart_delivers_only_once(self):
        self.create_note()
        Clock.value = datetime(2026, 10, 19, 8)
        self.restart()
        self.drain()
        self.assertEqual(len(self.delivered), 1)
        self.restart()
        self.drain()
        self.assertEqual(len(self.delivered), 1)

    def test_archived_and_trashed_notes_are_suppressed(self):
        archived = self.create_note('Архив')
        trashed = self.create_note('Корзина')
        active = self.create_note('Активная')
        self.store.execute('UPDATE notes SET archived=1 WHERE id=?', (archived,))
        self.store.execute('UPDATE notes SET deleted=1 WHERE id=?', (trashed,))
        Clock.value = datetime(2026, 10, 5, 9)
        self.drain()
        self.assertEqual([item[1] for item in self.delivered], [active])

    def test_html_snapshot_and_empty_fallback(self):
        note = self.create_note('  Заголовок  ', '<html><head><style>p{color:red}</style></head><body><p>Первый &amp; второй</p><p>☐ Задача</p></body></html>')
        empty = self.create_note('', '')
        Clock.value = datetime(2026, 10, 5, 9)
        self.drain()
        snapshots = {note_id:self.store.notification_event(event_id) for _,note_id,event_id in self.delivered}
        self.assertEqual(snapshots[note]['title'], 'Заголовок')
        self.assertEqual(snapshots[note]['body'], 'Первый & второй\n☐ Задача')
        self.assertEqual(snapshots[empty]['title'], 'Без названия')
        self.assertTrue(snapshots[empty]['body'])
        self.store.save_note(note,'Новый','<p>Изменение</p>',None,False,'')
        self.assertEqual(self.store.notification_event(snapshots[note]['id'])['body'], snapshots[note]['body'])

    def test_actions_target_their_event_and_snooze_rotates_token(self):
        self.create_note('Первая'); self.create_note('Вторая')
        Clock.value = datetime(2026,10,5,9)
        self.drain()
        first,second = [self.store.notification_event(item[2]) for item in self.delivered]
        done=self.store.handle_notification_action(first['id'],first['token'],'done')
        self.assertEqual(done['note_id'], first['note_id'])
        self.assertEqual(self.store.handle_notification_action(first['id'],first['token'],'done')['status'], 'done')
        snooze=self.store.handle_notification_action(second['id'],second['token'],'snooze',now=Clock.value)
        self.assertEqual(snooze['due_at'],'2026-10-05T09:10:00')
        self.assertIsNone(self.store.handle_notification_action(second['id'],second['token'],'done'))
        self.task.tick(); self.assertEqual(len(self.delivered),2)
        self.restart(); Clock.value += timedelta(minutes=10); self.task.tick()
        self.assertEqual(len(self.delivered),3)
        changed=self.store.notification_event(second['id'])
        self.assertNotEqual(changed['token'],second['token'])
        self.assertEqual(self.store.handle_notification_action(changed['id'],changed['token'],'open')['note_id'],second['note_id'])

    def test_expired_crash_lease_is_reclaimed_after_restart(self):
        self.create_note(); Clock.value=datetime(2026,10,5,9)
        self.task._enqueue(Clock.value)
        claimed=self.task.claim_next(Clock.value)
        self.assertIsNotNone(claimed)
        self.restart(); self.task.tick()
        self.assertFalse(self.delivered)
        Clock.value+=timedelta(seconds=31); self.task.tick()
        self.assertEqual(len(self.delivered),1)

    def test_callback_runs_without_transaction_and_other_worker_cannot_claim_same(self):
        self.create_note(); Clock.value=datetime(2026,10,5,9)
        other=Store(self.path)
        def callback(*args):
            self.assertFalse(self.store.db.in_transaction)
            self.assertIsNone(scheduler.Scheduler(other,self.notify).claim_next(Clock.value))
            self.notify(*args)
        self.task.notify=callback
        try:
            self.task.tick()
        finally:
            other.db.close()
        self.assertEqual(len(self.delivered),1)

    def test_poison_event_backs_off_without_blocking_other_nine(self):
        notes=[self.create_note(str(index)) for index in range(10)]
        Clock.value=datetime(2026,10,5,9)
        def callback(title,note,event):
            if note==notes[0]: raise OSError('Windows notifications disabled')
            self.notify(title,note,event)
        self.task=scheduler.Scheduler(self.store,callback,batch_size=10)
        with self.assertRaises(OSError): self.task.tick()
        self.assertEqual(len(self.delivered),9)
        poison=self.store.rows("SELECT * FROM reminder_events WHERE status='pending'")[0]
        self.assertEqual(poison['attempts'],1)
        self.assertIn('disabled',poison['last_error'])
        self.task.notify=self.notify
        Clock.value+=timedelta(seconds=9); self.task.tick(); self.assertEqual(len(self.delivered),9)
        Clock.value+=timedelta(seconds=1); self.task.tick(); self.assertEqual(len(self.delivered),10)

    def test_checkpoint_failure_rolls_back_outbox(self):
        self.create_note(); Clock.value=datetime(2026,10,5,9)
        original=self.store.execute
        def fail(sql,args=()):
            if sql.startswith('UPDATE reminders SET checked_through='): raise sqlite3.OperationalError('disk full')
            return original(sql,args)
        with patch.object(self.store,'execute',fail):
            with self.assertRaises(sqlite3.OperationalError): self.task.tick()
        self.assertFalse(self.store.rows('SELECT * FROM reminder_events'))
        self.assertIsNone(self.store.reminder(1)['checked_through'])
        self.task.tick(); self.assertEqual(len(self.delivered),1)

    def test_archive_restore_skips_offline_archived_interval(self):
        note=self.create_note(repeat=True)
        self.store.execute('UPDATE notes SET archived=1 WHERE id=?',(note,))
        Clock.value=datetime(2026,10,19,8)
        self.store.execute('UPDATE notes SET archived=0 WHERE id=?',(note,))
        self.restart(); self.drain(); self.assertFalse(self.delivered)
        Clock.value=datetime(2026,10,19,9); self.task.tick()
        self.assertEqual(len(self.delivered),1)

    def test_schedule_revision_invalidates_old_action(self):
        note=self.create_note(); Clock.value=datetime(2026,10,5,9)
        self.task.tick(); event=self.store.notification_event(self.delivered[0][2])
        self.store.save_reminder(note,'once','2026-10-06T09:00:00',[],[])
        self.assertIsNone(self.store.notification_event(event['id']))
        self.assertIsNone(self.store.handle_notification_action(event['id'],event['token'],'snooze'))
        Clock.value=datetime(2026,10,6,9); self.task.tick()
        self.assertEqual(len(self.delivered),2)

    def test_disable_preserves_history_and_reenable_skips_paused_occurrences(self):
        note=self.create_note(repeat=True)
        Clock.value=datetime(2026,10,5,9)
        self.task.tick()
        event=self.store.notification_event(self.delivered[0][2])
        original=self.store.reminder(note)
        self.store.save_reminder(note,None,None,[],[])
        disabled=self.store.reminder(note)
        self.assertEqual(disabled['id'],original['id'])
        self.assertEqual(disabled['enabled'],0)
        self.assertEqual(disabled['times'],['09:00','18:30'])
        self.assertEqual(self.store.rows('SELECT status FROM reminder_events')[0][0],'cancelled')
        self.assertIsNone(self.store.handle_notification_action(event['id'],event['token'],'snooze'))
        self.store.save_reminder(note,None,None,[],[])
        self.assertEqual(self.store.reminder(note)['revision'],disabled['revision'])
        Clock.value=datetime(2026,10,7,10)
        self.restart()
        self.store.save_reminder(note,'repeat',None,original['days'],original['times'])
        self.task.tick()
        self.assertEqual(len(self.delivered),1)
        self.assertEqual(len(self.store.rows('SELECT * FROM reminder_events')),1)
        Clock.value=datetime(2026,10,7,18,30)
        self.task.tick()
        self.assertEqual(len(self.delivered),2)

    def test_disable_preserves_completed_and_pending_audit_records(self):
        note=self.create_note(repeat=True)
        Clock.value=datetime(2026,10,5,9)
        self.task.tick()
        event=self.store.notification_event(self.delivered[0][2])
        self.store.handle_notification_action(event['id'],event['token'],'done')
        Clock.value=datetime(2026,10,5,18,30)
        self.task._enqueue(Clock.value)
        self.store.save_reminder(note,None,None,[],[])
        self.assertEqual([r[0] for r in self.store.rows('SELECT status FROM reminder_events ORDER BY id')],['done','cancelled'])
        self.store.save_reminder(note,'once','2026-10-06T09:00:00',[],[])
        self.assertEqual(len(self.store.rows('SELECT * FROM reminder_events')),2)

    def test_native_failure_backoff_survives_restart_and_caps_at_five_minutes(self):
        self.create_note(); Clock.value=datetime(2026,10,5,9)
        def fail(*args): raise OSError('DisabledForUser')
        self.task.notify=fail
        for expected_delay in [10,20,40,80,160,300,300]:
            with self.assertRaises(OSError): self.task.tick()
            row=self.store.rows('SELECT * FROM reminder_events')[0]
            self.assertEqual((datetime.fromisoformat(row['next_attempt_at'])-Clock.value).total_seconds(),expected_delay)
            self.assertEqual(row['status'],'pending')
            Clock.value=datetime.fromisoformat(row['next_attempt_at'])
        self.restart(); self.task.tick()
        self.assertEqual(len(self.delivered),1)
        self.assertEqual(self.store.rows('SELECT attempts FROM reminder_events')[0][0],7)

    def test_legacy_queue_migration_preserves_event_and_action_token(self):
        path=Path(self.directory.name)/'legacy.sqlite3'
        connection=sqlite3.connect(path)
        connection.executescript('''
            CREATE TABLE notes(id INTEGER PRIMARY KEY,title TEXT NOT NULL DEFAULT '',body TEXT NOT NULL DEFAULT '',folder_id INTEGER,favorite INTEGER NOT NULL DEFAULT 0,archived INTEGER NOT NULL DEFAULT 0,deleted INTEGER NOT NULL DEFAULT 0,updated_at TEXT NOT NULL);
            CREATE TABLE reminders(id INTEGER PRIMARY KEY,note_id INTEGER NOT NULL UNIQUE,mode TEXT NOT NULL,once_at TEXT,enabled INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL);
            CREATE TABLE reminder_events(id INTEGER PRIMARY KEY,reminder_id INTEGER NOT NULL,scheduled_at TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',UNIQUE(reminder_id,scheduled_at));
            INSERT INTO notes(id,title,body,updated_at) VALUES (1,'Старая заметка','<p>Старый текст</p>','2026-10-05T08:00:00');
            INSERT INTO reminders VALUES(1,1,'once','2026-10-05T09:00:00',1,'2026-10-05T08:00:00');
            INSERT INTO reminder_events VALUES(99,1,'2026-10-05T09:00:00','pending');
        ''')
        connection.close()
        legacy=Store(path)
        try:
            event=legacy.notification_event(99)
            self.assertEqual(event['body'],'Старый текст')
            self.assertTrue(event['token'])
            token=event['token']
        finally: legacy.db.close()
        legacy=Store(path)
        try: self.assertEqual(legacy.notification_event(99)['token'],token)
        finally: legacy.db.close()


if __name__ == '__main__':
    unittest.main()

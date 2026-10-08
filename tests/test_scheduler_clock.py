"""Clock-correction recovery with disposable SQLite; no native OS-clock changes."""
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from app.database.store import Store
from app.services.scheduler import Scheduler


class SchedulerClockRecovery(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.directory.name) / 'clock.db')
        self.note = self.store.create_note()
        self.store.save_reminder(self.note, 'repeat', None, list(range(7)), ['09:00', '18:30'])
        self.task = Scheduler(self.store, lambda *args: None)
        self.now = datetime(2026, 10, 7, 8)
        self.store.execute('UPDATE reminders SET created_utc=NULL,created_at=?,checked_through=?',
                           ('2026-10-06T00:00:00', '2026-10-07T19:00:00'))

    def tearDown(self):
        self.store.db.close()
        self.directory.cleanup()

    def event(self, stamp, **changes):
        reminder = self.store.reminder(self.note)
        self.store.execute('INSERT INTO reminder_events(reminder_id,scheduled_at,status,revision,token,due_at) VALUES (?,?,?,?,?,?)',
                           (reminder['id'], stamp, 'pending', reminder['revision'], 'clock-token', stamp))
        event = self.store.rows('SELECT id FROM reminder_events')[0][0]
        for field, value in changes.items():
            self.store.execute('UPDATE reminder_events SET ' + field + '=? WHERE id=?', (value,event))
        return event

    def test_rollback_resets_checkpoint_and_resumes_new_occurrences(self):
        self.task._enqueue(self.now)
        self.assertEqual(self.store.reminder(self.note)['checked_through'], '2026-10-07T08:00:00')
        self.task._enqueue(self.now + timedelta(hours=1))
        self.assertEqual(self.store.rows('SELECT scheduled_at FROM reminder_events')[0][0], '2026-10-07T09:00:00')

    def test_rollback_does_not_repeat_already_delivered_minute(self):
        self.event('2026-10-07T09:00:00', status='notified')
        self.task._enqueue(self.now)
        self.task._enqueue(self.now + timedelta(hours=1))
        rows = self.store.rows('SELECT status FROM reminder_events')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], 'notified')
        self.assertIsNone(self.task.claim_next(self.now + timedelta(hours=1)))

    def test_rollback_bounds_lease_without_stealing_immediately(self):
        event = self.event('2026-10-06T09:00:00', lease_owner='other-worker', lease_until='2026-10-07T19:00:30')
        self.task._enqueue(self.now)
        self.assertIsNone(self.task.claim_next(self.now))
        self.assertIsNone(self.task.claim_next(self.now + timedelta(seconds=29)))
        self.assertEqual(self.task.claim_next(self.now + timedelta(seconds=30))['id'], event)

    def test_rollback_bounds_backoff_without_resetting_attempts_or_due_at(self):
        event = self.event('2026-10-06T09:00:00', next_attempt_at='2026-10-07T19:05:00', attempts=6)
        self.task._enqueue(self.now)
        row = self.store.rows('SELECT * FROM reminder_events WHERE id=?', (event,))[0]
        self.assertEqual(row['attempts'], 6)
        self.assertEqual(row['due_at'], '2026-10-06T09:00:00')
        self.assertIsNone(self.task.claim_next(self.now + timedelta(seconds=299)))
        self.assertEqual(self.task.claim_next(self.now + timedelta(seconds=300))['id'], event)

    def test_rollback_does_not_advance_snoozed_future_event(self):
        event = self.event('2026-10-06T09:00:00', due_at='2026-10-07T08:10:00', next_attempt_at='2026-10-07T19:05:00')
        self.task._enqueue(self.now)
        self.assertIsNone(self.task.claim_next(self.now + timedelta(minutes=5)))
        self.assertEqual(self.task.claim_next(self.now + timedelta(minutes=10))['id'], event)

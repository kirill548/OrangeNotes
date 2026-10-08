"""Deterministic simulated-clock tests; these do not represent real-time endurance."""
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from app.database.store import Store
from app.services import scheduler


class Clock(datetime):
    value = datetime(2026, 10, 5)

    @classmethod
    def now(cls):
        return cls.value


class SchedulerEdges(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.directory.name) / 'notes.sqlite3')
        Clock.value = datetime(2026, 10, 5)
        self.clock_patch = patch.object(scheduler, 'datetime', Clock)
        self.clock_patch.start()
        self.received = []
        self.note = self.store.create_note()
        self.store.save_reminder(self.note, 'repeat', None, list(range(7)), ['09:00', '18:30'])
        self.store.execute('UPDATE reminders SET created_utc=NULL,created_at=?', ('2026-10-04T00:00:00',))
        self.store.execute('UPDATE reminders SET checked_through=?', (Clock.value.isoformat(timespec='seconds'),))
        self.task = scheduler.Scheduler(self.store, self.notify)

    def tearDown(self):
        self.clock_patch.stop()
        self.store.db.close()
        self.directory.cleanup()

    def notify(self, title, note, event):
        self.received.append(event)

    def drain(self, ticks=40):
        for _ in range(ticks):
            self.task.tick()

    def test_simulated_fourteen_day_sleep(self):
        self.task.tick()
        Clock.value += timedelta(days=14)
        self.drain()
        self.assertEqual(len(self.received), 28)
        self.assertEqual(len(set(self.received)), 28)
        delivered_stamps = [self.store.rows('SELECT scheduled_at FROM reminder_events WHERE id=?', (event,))[0][0] for event in self.received]
        self.assertEqual(delivered_stamps, sorted(delivered_stamps))
        self.assertFalse(self.store.rows("SELECT * FROM reminder_events WHERE status='pending'"))

    def test_backward_clock_does_not_repeat_delivered_minutes(self):
        Clock.value = datetime(2026, 10, 5, 19)
        self.drain()
        self.assertEqual(len(self.received), 2)
        Clock.value = datetime(2026, 10, 5, 8)
        self.task.tick()
        Clock.value = datetime(2026, 10, 5, 19)
        self.drain()
        self.assertEqual(len(self.received), 2)
        Clock.value = datetime(2026, 10, 6, 19)
        self.drain()
        self.assertEqual(len(self.received), 4)

    def test_restart_midway_drains_persisted_queue(self):
        Clock.value += timedelta(days=14)
        self.task.tick()
        self.assertEqual(len(self.received), 1)
        self.task = scheduler.Scheduler(self.store, self.notify)
        self.drain()
        self.assertEqual(len(self.received), 28)
        self.assertEqual(len(set(self.received)), 28)

    def test_notify_exception_releases_guard_and_retries(self):
        Clock.value = datetime(2026, 10, 5, 9)
        def fail(*args):
            raise RuntimeError('transport unavailable')
        self.task.notify = fail
        with self.assertRaises(RuntimeError):
            self.task.tick()
        self.assertFalse(self.task._running)
        self.assertEqual(len(self.store.rows("SELECT * FROM reminder_events WHERE status='pending'")), 1)
        self.task.notify = self.notify
        Clock.value += timedelta(seconds=10)
        self.task.tick()
        self.assertEqual(len(self.received), 1)

    def test_enqueue_write_failure_can_be_retried(self):
        Clock.value = datetime(2026, 10, 5, 9)
        original = self.store.execute
        def fail(sql, args=()):
            if sql.startswith('INSERT OR IGNORE INTO reminder_events'):
                raise sqlite3.OperationalError('disk full')
            return original(sql, args)
        with patch.object(self.store, 'execute', fail):
            with self.assertRaises(sqlite3.OperationalError):
                self.task.tick()
        self.assertFalse(self.task._running)
        self.task.tick()
        self.assertEqual(len(self.received), 1)

    def test_failed_delivery_status_write_does_not_duplicate_notification(self):
        Clock.value = datetime(2026, 10, 5, 9)
        original = self.store.execute
        def fail(sql, args=()):
            if sql.startswith("UPDATE reminder_events SET status='notified'"):
                raise sqlite3.OperationalError('disk full after notification')
            return original(sql, args)
        with patch.object(self.store, 'execute', fail):
            try:
                self.task.tick()
            except sqlite3.OperationalError:
                pass
        self.task.tick()
        self.assertEqual(len(self.received), len(set(self.received)), 'The same event was delivered twice after a status write failed')

    def test_restart_after_successful_delivery_does_not_replay(self):
        Clock.value = datetime(2026, 10, 5, 9)
        self.task.tick()
        self.store.db.close()
        self.store = Store(Path(self.directory.name) / 'notes.sqlite3')
        self.task = scheduler.Scheduler(self.store, self.notify)
        self.task.tick()
        self.assertEqual(len(self.received), 1)


if __name__ == '__main__':
    unittest.main()

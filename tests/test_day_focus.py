import sqlite3
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

from app.database.store import Store
from app.services import day_focus
from app.services.day_focus import DayFocus


class Clock(datetime):
    value = datetime(2026,10,5,23,59)

    @classmethod
    def now(cls):
        return cls.value


class DayFocusTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name)/'notes.sqlite3'
        self.store = Store(self.path)
        self.focus = DayFocus(self.store)
        Clock.value = datetime(2026,10,5,23,59)
        self.clock = patch.object(day_focus,'datetime',Clock)
        self.clock.start()

    def tearDown(self):
        self.clock.stop()
        self.store.db.close()
        self.directory.cleanup()

    def note(self,title='Заметка',favorite=False):
        note = self.store.create_note()
        self.store.save_note(note,title,'<p>Текст</p>',None,favorite,'')
        return note

    def test_add_is_idempotent_remove_only_one_day(self):
        note = self.note()
        self.assertTrue(self.focus.add(note,'2026-10-05'))
        self.assertFalse(self.focus.add(note,date(2026,10,5)))
        self.assertFalse(self.focus.add(note,datetime(2026,10,5,12,30)))
        self.assertTrue(self.focus.add(note,'2026-10-06'))
        self.assertTrue(self.focus.remove(note,'2026-10-05'))
        self.assertFalse(self.focus.remove(note,'2026-10-05'))
        self.assertTrue(self.focus.contains(note,'2026-10-06'))
        self.assertEqual(len(self.store.rows('SELECT * FROM notes')),1)

    def test_restart_preserves_selection_and_new_day_is_empty(self):
        note = self.note()
        self.focus.add(note)
        self.store.db.close()
        self.store = Store(self.path)
        self.focus = DayFocus(self.store)
        self.assertEqual(self.focus.selected_ids(),[note])
        Clock.value = datetime(2026,10,6,0,0)
        self.assertEqual(self.focus.selected_ids(),[])
        self.assertTrue(self.focus.contains(note,'2026-10-05'))
        self.assertEqual(len(self.store.rows('SELECT * FROM notes')),1)

    def test_missing_archived_and_deleted_notes_rejected(self):
        with self.assertRaises(ValueError): self.focus.add(999)
        for column in ['archived','deleted']:
            note = self.note()
            self.focus.add(note)
            self.store.execute(f'UPDATE notes SET {column}=1 WHERE id=?',(note,))
            with self.assertRaises(ValueError): self.focus.add(note)
            self.assertFalse(self.focus.contains(note))
            self.assertNotIn(note,self.focus.selected_ids())
        self.assertEqual(len(self.store.rows('SELECT * FROM note_days')),2)

    def test_hard_delete_cascades_assignments(self):
        note = self.note()
        self.focus.add(note)
        self.store.execute('DELETE FROM notes WHERE id=?',(note,))
        self.assertFalse(self.store.rows('SELECT * FROM note_days'))
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.execute("INSERT INTO note_days VALUES(999,'2026-10-05')")

    def test_invalid_days_and_ids_do_not_write(self):
        note = self.note()
        for value in ['2026-2-01','2026-02-30','2026-10-05T09:00:00',5,True]:
            with self.assertRaises(ValueError): self.focus.add(note,value)
        for note_id in [True,0,-1,'1',None]:
            with self.assertRaises(ValueError): self.focus.add(note_id)
        self.assertFalse(self.store.rows('SELECT * FROM note_days'))

    def test_suggestions_priority_payload_and_daily_exclusion(self):
        overdue = self.note('Просроченная')
        today = self.note('На сегодня')
        favorite = self.note('Избранная',True)
        unranked = self.note('Обычная')
        self.store.save_reminder(overdue,'once','2026-10-04T09:00:00',[],[])
        reminder = self.store.reminder(overdue)
        self.store.execute("INSERT INTO reminder_events(reminder_id,scheduled_at,status) VALUES(?,?,'notified')",(reminder['id'],'2026-10-04T09:00:00'))
        self.store.save_reminder(today,'repeat',None,[0],['09:00'])
        rows = self.focus.suggestions('2026-10-05')
        self.assertEqual([row['id'] for row in rows],[overdue,today,favorite])
        self.assertEqual(rows[0]['reason'],'Просроченное напоминание')
        self.assertEqual(rows[1]['reason'],'Напоминание сегодня')
        self.assertEqual(rows[2]['body'],'<p>Текст</p>')
        self.focus.add(overdue)
        self.assertEqual([row['id'] for row in self.focus.suggestions()],[today,favorite])
        self.assertNotIn(unranked,[row['id'] for row in rows])

    def test_suggestions_exclude_inactive_done_and_stale_revision(self):
        note = self.note('Старое событие')
        self.store.save_reminder(note,'once','2026-10-04T09:00:00',[],[])
        reminder = self.store.reminder(note)
        self.store.execute("INSERT INTO reminder_events(reminder_id,scheduled_at,status) VALUES(?,?,'done')",(reminder['id'],'2026-10-04T09:00:00'))
        self.assertEqual(self.focus.suggestions(),[])
        self.store.execute("UPDATE reminder_events SET status='notified',revision=0")
        self.assertEqual(self.focus.suggestions(),[])
        active = self.note('Скрытое избранное',True)
        self.store.execute('UPDATE notes SET archived=1 WHERE id=?',(active,))
        self.assertEqual(self.focus.suggestions(),[])

    def event(self,note,scheduled_at,status='done',due_at=None):
        reminder=self.store.reminder(note)
        self.store.execute('''INSERT INTO reminder_events(reminder_id,scheduled_at,status,revision,due_at)
            VALUES(?,?,?,?,?)''',(reminder['id'],scheduled_at,status,reminder['revision'],due_at or scheduled_at))

    def test_completed_once_today_not_suggested_unless_favorite(self):
        note=self.note()
        self.store.save_reminder(note,'once','2026-10-05T09:00:00',[],[])
        self.event(note,'2026-10-05T09:00:00')
        self.assertEqual(self.focus.suggestions(),[])
        self.store.execute('UPDATE notes SET favorite=1 WHERE id=?',(note,))
        self.assertEqual(self.focus.suggestions()[0]['reason'],'Избранная заметка')

    def test_repeat_remaining_today_suggested_all_completed_excluded(self):
        note=self.note()
        self.store.save_reminder(note,'repeat',None,[0],['09:00','18:30'])
        self.event(note,'2026-10-05T09:00:00')
        self.assertEqual(self.focus.suggestions()[0]['id'],note)
        self.event(note,'2026-10-05T18:30:00')
        self.assertEqual(self.focus.suggestions(),[])

    def test_yesterday_schedule_snoozed_into_today_is_suggested(self):
        note=self.note()
        self.store.save_reminder(note,'once','2026-10-04T23:55:00',[],[])
        self.event(note,'2026-10-04T23:55:00','pending','2026-10-05T23:59:30')
        rows=self.focus.suggestions()
        self.assertEqual(rows[0]['id'],note)
        self.assertEqual(rows[0]['reason'],'Напоминание сегодня')


if __name__ == '__main__':
    unittest.main()

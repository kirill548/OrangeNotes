"""A local daily selection. Changing the day never deletes notes or history."""
from datetime import date, datetime, time

from app.services.scheduler import occurrences
from app.utils.timezones import utc_stamp, event_due_local

_DATETIME_TYPE = datetime


class DayFocus:
    def __init__(self, store):
        self.store = store
        self.store.execute('''CREATE TABLE IF NOT EXISTS note_days(
            note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
            day TEXT NOT NULL,
            PRIMARY KEY(note_id,day))''')
        self.store.execute('CREATE INDEX IF NOT EXISTS note_days_day ON note_days(day,note_id)')

    @staticmethod
    def _day(day=None):
        if day is None:
            return datetime.now().date()
        if isinstance(day, _DATETIME_TYPE):
            return day.date()
        if isinstance(day, date):
            return day
        if isinstance(day, str):
            try:
                parsed = date.fromisoformat(day)
            except ValueError:
                raise ValueError('Укажите дату в формате ГГГГ-ММ-ДД.') from None
            if parsed.isoformat() == day:
                return parsed
        raise ValueError('Укажите дату в формате ГГГГ-ММ-ДД.')

    @staticmethod
    def _note_id(note_id):
        if type(note_id) is not int or note_id <= 0:
            raise ValueError('Укажите существующую заметку.')
        return note_id

    def add(self, note_id, day=None):
        note_id = self._note_id(note_id)
        stamp = self._day(day).isoformat()
        with self.store.db:
            result = self.store.db.execute('''INSERT OR IGNORE INTO note_days(note_id,day)
                SELECT id,? FROM notes WHERE id=? AND archived=0 AND deleted=0''', (stamp,note_id))
            if not self.store.rows('SELECT id FROM notes WHERE id=? AND archived=0 AND deleted=0', (note_id,)):
                raise ValueError('Заметка удалена или находится в архиве.')
            return result.rowcount == 1

    def remove(self, note_id, day=None):
        result = self.store.execute('DELETE FROM note_days WHERE note_id=? AND day=?',
                                    (self._note_id(note_id),self._day(day).isoformat()))
        return result.rowcount == 1

    def contains(self, note_id, day=None):
        return bool(self.store.rows('''SELECT 1 FROM note_days d JOIN notes n ON n.id=d.note_id
            WHERE d.note_id=? AND d.day=? AND n.archived=0 AND n.deleted=0''',
                                    (self._note_id(note_id),self._day(day).isoformat())))

    def selected_ids(self, day=None):
        return [row['note_id'] for row in self.store.rows('''SELECT d.note_id FROM note_days d
            JOIN notes n ON n.id=d.note_id WHERE d.day=? AND n.archived=0 AND n.deleted=0
            ORDER BY n.updated_at DESC,n.id DESC''', (self._day(day).isoformat(),))]

    def suggestions(self, day=None):
        target = self._day(day)
        start = datetime.combine(target, time.min)
        end = datetime.combine(target, time.max)
        now = datetime.now()
        cutoff = now if target == now.date() else start
        selected = set(self.selected_ids(target))
        overdue = {row['note_id'] for row in self.store.rows('''SELECT DISTINCT r.note_id
            FROM reminder_events e JOIN reminders r ON r.id=e.reminder_id
            WHERE r.enabled=1 AND e.revision=r.revision AND e.status IN ('pending','notified')
                AND ((e.due_utc IS NOT NULL AND e.due_utc<?) OR (e.due_utc IS NULL AND COALESCE(e.due_at,e.scheduled_at)<?))''', (utc_stamp(cutoff),cutoff.isoformat(timespec='seconds')))}
        completed = {}
        due_today = set()
        for event in self.store.rows('''SELECT r.note_id,e.*
            FROM reminder_events e JOIN reminders r ON r.id=e.reminder_id
            WHERE r.enabled=1 AND e.revision=r.revision AND e.status IN ('done','pending','notified')'''):
            if event['status'] == 'done':
                completed.setdefault(event['note_id'],set()).add(event['scheduled_utc'] or event['scheduled_at'])
            elif start <= event_due_local(event) <= end:
                due_today.add(event['note_id'])
        ranked = []
        for row in self.store.rows('SELECT * FROM notes WHERE archived=0 AND deleted=0 ORDER BY updated_at DESC,id DESC'):
            note_id = row['id']
            if note_id in selected:
                continue
            if note_id in overdue:
                priority,reason = 0,'Просроченное напоминание'
            elif note_id in due_today or any(
                utc_stamp(stamp) not in completed.get(note_id,set()) and stamp.isoformat(timespec='seconds') not in completed.get(note_id,set())
                for stamp in occurrences(self.store.reminder(note_id), start, end)):
                priority,reason = 1,'Напоминание сегодня'
            elif row['favorite']:
                priority,reason = 2,'Избранная заметка'
            else:
                continue
            ranked.append((priority, {'id':note_id,'title':row['title'],'body':row['body'],'reason':reason}))
        # Stable sorting preserves the note modification order within each reason.
        ranked.sort(key=lambda item:item[0])
        return [item[1] for item in ranked]

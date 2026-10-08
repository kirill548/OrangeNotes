from datetime import datetime, timedelta, time, timezone
from app.utils.timezones import device_zone, zone, resolve_local, utc_stamp, refresh_device_zone
from uuid import uuid4
from app.database.store import plain_body


def occurrences(reminder, start, end):
    if not reminder or not reminder['enabled']:
        return []
    name = reminder.get('timezone_id')
    if name:
        start_utc = datetime.fromisoformat(utc_stamp(start)).replace(tzinfo=timezone.utc)
        end_utc = datetime.fromisoformat(utc_stamp(end)).replace(tzinfo=timezone.utc)
        result = []
        if reminder['mode'] == 'once':
            candidate = resolve_local(datetime.fromisoformat(reminder['once_at']), name)
            candidates = [candidate]
        else:
            day = start_utc.astimezone(zone(name)).date()
            last = end_utc.astimezone(zone(name)).date()
            candidates = []
            while day <= last:
                if day.weekday() in reminder['days']:
                    candidates.extend(resolve_local(datetime.combine(day, time.fromisoformat(value)), name) for value in reminder['times'])
                day += timedelta(days=1)
        for candidate in candidates:
            if start_utc <= candidate.astimezone(timezone.utc) <= end_utc:
                result.append(candidate.astimezone(zone(device_zone())).replace(tzinfo=None))
        return sorted(set(result))
    if reminder['mode'] == 'once':
        value = datetime.fromisoformat(reminder['once_at'])
        return [value] if start <= value <= end else []
    result = []
    day = start.date()
    while day <= end.date():
        if day.weekday() in reminder['days']:
            for value in reminder['times']:
                candidate = datetime.combine(day, time.fromisoformat(value))
                if start <= candidate <= end:
                    result.append(candidate)
        day += timedelta(days=1)
    return sorted(result)


def next_occurrence(reminder, now=None):
    now = now or datetime.now()
    if reminder and reminder['enabled'] and reminder['mode'] == 'once':
        at = resolve_local(datetime.fromisoformat(reminder['once_at']), reminder['timezone_id']).astimezone(zone(device_zone())).replace(tzinfo=None) if reminder.get('timezone_id') else datetime.fromisoformat(reminder['once_at'])
        return at if at >= now else None
    values = occurrences(reminder, now, now + timedelta(days=8))
    return values[0] if values else None


class Scheduler:
    def __init__(self, store, notify, batch_size=1):
        self.store, self.notify = store, notify
        self.last = datetime.now() - timedelta(seconds=60)
        self._running = False
        self._delivered = set()
        self.owner = uuid4().hex
        self.batch_size = max(1, int(batch_size))

    def tick(self):
        if self._running:
            return
        self._running = True
        try:
            refresh_device_zone()
            self._tick()
        finally:
            self._running = False

    def _tick(self):
        now = datetime.now()
        # A successful notification cannot be rolled back with a failed SQL write.
        # Retry its status first without sending it again in this process.
        for identity in tuple(self._delivered):
            self._acknowledge(identity)
            self._delivered.discard(identity)
        self._enqueue(now)
        first_error = None
        for _ in range(self.batch_size):
            event = self.claim_next(now)
            if event is None:
                break
            try:
                self.notify(event['title'] or 'Без названия', event['note_id'], event['id'])
            except Exception as error:
                attempts = event['attempts'] + 1
                retry = (now + timedelta(seconds=min(300, 10 * 2 ** min(attempts - 1, 5)))).isoformat(timespec='seconds')
                self.store.execute('UPDATE reminder_events SET lease_owner=NULL,lease_until=NULL,lease_until_utc=NULL,next_attempt_at=?,next_attempt_utc=?,attempts=?,last_error=? WHERE id=? AND lease_owner=?',
                                   (retry,utc_stamp(datetime.fromisoformat(retry)),attempts,str(error)[:1000],event['id'],self.owner))
                first_error = first_error or error
                continue
            identity=(event['id'],event['reminder_id'],event['scheduled_at'],event['token'])
            self._delivered.add(identity)
            self._acknowledge(identity)
            self._delivered.discard(identity)
        self.last = now
        if first_error is not None:
            raise first_error

    def _acknowledge(self, identity):
        self.store.execute("UPDATE reminder_events SET status='notified',lease_owner=NULL,lease_until=NULL,lease_until_utc=NULL WHERE id=? AND reminder_id=? AND scheduled_at=? AND token=? AND status='pending' AND lease_owner=?", (*identity,self.owner))

    def _enqueue(self, now):
        # The outbox and its scan checkpoint commit together. A crash cannot advance
        # the checkpoint past events that have not been persisted.
        with self.store.db:
            self.store.db.execute('BEGIN IMMEDIATE')
            rollback = False
            days, times = {}, {}
            for item in self.store.rows('SELECT reminder_id,weekday FROM reminder_days'):
                days.setdefault(item[0], []).append(item[1])
            for item in self.store.rows('SELECT reminder_id,time FROM reminder_times'):
                times.setdefault(item[0], []).append(item[1])
            for row in self.store.rows('SELECT r.*,n.title,n.body,n.deleted,n.archived FROM reminders r JOIN notes n ON n.id=r.note_id'):
                r = dict(row)
                r['days'], r['times'] = days.get(r['id'], []), times.get(r['id'], [])
                start = resolve_local(datetime.fromisoformat(r['checked_through'] or r['created_at']), r['timezone_id']).astimezone(zone(device_zone())).replace(tzinfo=None)
                if r.get('checked_through_utc'):
                    previous = datetime.fromisoformat(r['checked_through_utc']).replace(tzinfo=timezone.utc)
                    start = previous.astimezone(zone(device_zone())).replace(tzinfo=None)
                elif not r['checked_through'] and r.get('created_utc'):
                    created = datetime.fromisoformat(r['created_utc']).replace(tzinfo=timezone.utc)
                    start = created.astimezone(zone(device_zone())).replace(tzinfo=None)
                if utc_stamp(now) < utc_stamp(start):
                    # Local wall clocks may move backwards. Resume scanning from
                    # the new present; persisted occurrence identities prevent
                    # already delivered minutes from being enqueued twice.
                    start = now
                    rollback = True
                if not r['enabled'] or row['deleted'] or row['archived']:
                    self.store.execute("UPDATE reminder_events SET status='cancelled',lease_owner=NULL,lease_until=NULL,lease_until_utc=NULL WHERE reminder_id=? AND status='pending'", (r['id'],))
                else:
                    title = row['title'].strip() or 'Без названия'
                    body = plain_body(row['body']) or 'Откройте заметку, чтобы посмотреть подробности.'
                    for at in occurrences(r, start, now):
                        instant = resolve_local(at, device_zone(), at.fold)
                        origin = instant.astimezone(zone(r['timezone_id']))
                        stamp = origin.replace(tzinfo=None).isoformat(timespec='seconds')
                        self.store.execute('INSERT OR IGNORE INTO reminder_events(reminder_id,scheduled_at,status,revision,title,body,token,due_at,scheduled_utc,due_utc,timezone_id,utc_offset,fold) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                                           (r['id'],stamp,'pending',r['revision'],title,body,uuid4().hex,stamp,utc_stamp(instant),utc_stamp(instant),r['timezone_id'],int(origin.utcoffset().total_seconds()),origin.fold))
                self.store.execute('UPDATE reminders SET checked_through=?,checked_through_utc=? WHERE id=?', (now.isoformat(timespec='seconds'),utc_stamp(now),r['id']))
            if rollback:
                # A crashed worker's lease and delivery backoff must not become
                # hours long after a clock correction. Retain their normal
                # maximum windows, rather than immediately stealing a live lease.
                lease_limit = (now + timedelta(seconds=30)).isoformat(timespec='seconds')
                retry_limit = (now + timedelta(seconds=300)).isoformat(timespec='seconds')
                self.store.execute("UPDATE reminder_events SET lease_until=?,lease_until_utc=NULL WHERE status='pending' AND lease_until>?", (lease_limit,lease_limit))
                self.store.execute("UPDATE reminder_events SET next_attempt_at=?,next_attempt_utc=NULL WHERE status='pending' AND next_attempt_at>?", (retry_limit,retry_limit))

    def claim_next(self, now=None):
        """Atomically lease one due event; the caller performs I/O after commit."""
        now = now or datetime.now()
        stamp = now.isoformat(timespec='seconds')
        with self.store.db:
            self.store.db.execute('BEGIN IMMEDIATE')
            rows = self.store.rows('''SELECT e.*,r.note_id FROM reminder_events e
                JOIN reminders r ON r.id=e.reminder_id JOIN notes n ON n.id=r.note_id
                WHERE e.status='pending' AND e.revision=r.revision AND r.enabled=1
                    AND n.deleted=0 AND n.archived=0 AND ((e.due_utc IS NOT NULL AND e.due_utc<=?) OR (e.due_utc IS NULL AND COALESCE(e.due_at,e.scheduled_at)<=?))
                    AND ((e.lease_until_utc IS NOT NULL AND e.lease_until_utc<=?) OR (e.lease_until_utc IS NULL AND (e.lease_until IS NULL OR e.lease_until<=?)))
                    AND ((e.next_attempt_utc IS NOT NULL AND e.next_attempt_utc<=?) OR (e.next_attempt_utc IS NULL AND (e.next_attempt_at IS NULL OR e.next_attempt_at<=?)))
                ORDER BY COALESCE(e.due_utc,e.due_at,e.scheduled_at),e.id LIMIT 1''', (utc_stamp(now),stamp,utc_stamp(now),stamp,utc_stamp(now),stamp))
            if not rows:
                return None
            event = dict(rows[0])
            expires = (now + timedelta(seconds=30)).isoformat(timespec='seconds')
            self.store.db.execute('UPDATE reminder_events SET lease_owner=?,lease_until=?,lease_until_utc=? WHERE id=?', (self.owner,expires,utc_stamp(datetime.fromisoformat(expires)),event['id']))
            event.update(lease_owner=self.owner, lease_until=expires)
            return event

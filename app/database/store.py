import os
import sqlite3
import tempfile
from pathlib import Path
from datetime import datetime, time, timedelta
from html.parser import HTMLParser
from uuid import uuid4


class _PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('style', 'script', 'head'):
            self.hidden += 1
        if not self.hidden and tag in ('p', 'div', 'br', 'li', 'tr'):
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in ('style', 'script', 'head') and self.hidden:
            self.hidden -= 1
        elif not self.hidden and tag in ('p', 'div', 'li', 'tr'):
            self.parts.append('\n')

    def handle_data(self, value):
        if not self.hidden:
            self.parts.append(value)


def plain_body(html):
    parser = _PlainText()
    parser.feed(html or '')
    return '\n'.join(line.strip() for line in ''.join(parser.parts).splitlines() if line.strip())


class Store:
    @staticmethod
    def default_path():
        from app.utils.data_paths import database_path
        return database_path()

    def __init__(self, path=None):
        path = (Path(path) if path else self.default_path()).resolve()
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        try:
            self._initialize()
        except Exception:
            self.db.close()
            raise

    def _initialize(self):
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.execute('PRAGMA busy_timeout=5000')
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS folders(id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL);
        CREATE TABLE IF NOT EXISTS notes(id INTEGER PRIMARY KEY, title TEXT NOT NULL DEFAULT '', body TEXT NOT NULL DEFAULT '', folder_id INTEGER REFERENCES folders(id) ON DELETE SET NULL, favorite INTEGER NOT NULL DEFAULT 0, archived INTEGER NOT NULL DEFAULT 0, deleted INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS tags(id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL);
        CREATE TABLE IF NOT EXISTS note_tags(note_id INTEGER REFERENCES notes(id) ON DELETE CASCADE, tag_id INTEGER REFERENCES tags(id) ON DELETE CASCADE, PRIMARY KEY(note_id,tag_id));
        CREATE TABLE IF NOT EXISTS reminders(id INTEGER PRIMARY KEY, note_id INTEGER NOT NULL UNIQUE REFERENCES notes(id) ON DELETE CASCADE, mode TEXT NOT NULL CHECK(mode IN ('once','repeat')), once_at TEXT, enabled INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS reminder_days(reminder_id INTEGER REFERENCES reminders(id) ON DELETE CASCADE, weekday INTEGER CHECK(weekday BETWEEN 0 AND 6), PRIMARY KEY(reminder_id,weekday));
        CREATE TABLE IF NOT EXISTS reminder_times(reminder_id INTEGER REFERENCES reminders(id) ON DELETE CASCADE, time TEXT NOT NULL, PRIMARY KEY(reminder_id,time));
        CREATE TABLE IF NOT EXISTS reminder_events(id INTEGER PRIMARY KEY, reminder_id INTEGER NOT NULL REFERENCES reminders(id) ON DELETE CASCADE, scheduled_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', UNIQUE(reminder_id,scheduled_at));
        CREATE INDEX IF NOT EXISTS notes_updated ON notes(updated_at DESC);
        ''')
        self.db.commit()
        self._migrate_delivery()

    def _migrate_delivery(self):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            self._migrate_delivery_locked()

    def _migrate_delivery_locked(self):
        if 'icon_name' not in {row['name'] for row in self.rows('PRAGMA table_info(notes)')}:
            self.execute('ALTER TABLE notes ADD COLUMN icon_name TEXT')
        # Additive migrations preserve existing notes, schedules and delivery history.
        for table, columns in {
            'notes': {'created_at':'TEXT','workspace_id':'INTEGER NOT NULL DEFAULT 1'},
            'reminders': {'checked_through':'TEXT', 'revision':'INTEGER NOT NULL DEFAULT 1', 'timezone_id':'TEXT', 'created_utc':'TEXT', 'once_utc':'TEXT', 'checked_through_utc':'TEXT'},
            'reminder_events': {'revision':'INTEGER NOT NULL DEFAULT 1', 'title':'TEXT',
                                'body':'TEXT', 'token':'TEXT', 'due_at':'TEXT',
                                'lease_owner':'TEXT', 'lease_until':'TEXT',
                                'next_attempt_at':'TEXT', 'attempts':'INTEGER NOT NULL DEFAULT 0', 'last_error':'TEXT', 'lease_until_utc':'TEXT', 'next_attempt_utc':'TEXT', 'scheduled_utc':'TEXT', 'due_utc':'TEXT', 'timezone_id':'TEXT', 'utc_offset':'INTEGER', 'fold':'INTEGER NOT NULL DEFAULT 0'},
        }.items():
            existing = {row['name'] for row in self.rows(f'PRAGMA table_info({table})')}
            for name, definition in columns.items():
                if name not in existing:
                    self.execute(f'ALTER TABLE {table} ADD COLUMN {name} {definition}')
        self.execute('CREATE INDEX IF NOT EXISTS reminder_delivery ON reminder_events(status,due_at,lease_until)')
        self.db.execute('DROP TRIGGER IF EXISTS reminder_note_visibility')
        self.db.execute('''
            CREATE TRIGGER IF NOT EXISTS reminder_note_visibility AFTER UPDATE OF archived,deleted ON notes
            WHEN OLD.archived != NEW.archived OR OLD.deleted != NEW.deleted
            BEGIN
                UPDATE reminders SET checked_through=strftime('%Y-%m-%dT%H:%M:%S','now','localtime'),checked_through_utc=strftime('%Y-%m-%dT%H:%M:%S','now') WHERE note_id=NEW.id;
                UPDATE reminder_events SET status='cancelled',lease_owner=NULL,lease_until=NULL
                    WHERE reminder_id IN (SELECT id FROM reminders WHERE note_id=NEW.id) AND status='pending';
            END;
        ''')
        for event in self.rows('SELECT e.id,n.title,n.body FROM reminder_events e JOIN reminders r ON r.id=e.reminder_id JOIN notes n ON n.id=r.note_id WHERE e.token IS NULL'):
            self.db.execute('UPDATE reminder_events SET title=?,body=?,token=?,due_at=scheduled_at WHERE id=?',
                            (event['title'].strip() or 'Без названия', plain_body(event['body']) or 'Откройте заметку, чтобы посмотреть подробности.', uuid4().hex, event['id']))

        from app.utils.timezones import device_zone, utc_stamp, resolve_local
        name = device_zone()
        for row in self.rows('SELECT * FROM reminders WHERE timezone_id IS NULL'):
            self.db.execute('UPDATE reminders SET timezone_id=?,once_utc=?,created_utc=? WHERE id=?',
                            (name, utc_stamp(datetime.fromisoformat(row['once_at']), name) if row['once_at'] else None, utc_stamp(datetime.fromisoformat(row['created_at']),name), row['id']))
        for row in self.rows('SELECT e.*,r.timezone_id AS schedule_zone FROM reminder_events e JOIN reminders r ON r.id=e.reminder_id WHERE e.scheduled_utc IS NULL'):
            local = resolve_local(datetime.fromisoformat(row['scheduled_at']), row['schedule_zone'])
            self.db.execute('UPDATE reminder_events SET scheduled_utc=?,due_utc=?,timezone_id=?,utc_offset=?,fold=? WHERE id=?',
                            (utc_stamp(local), utc_stamp(datetime.fromisoformat(row['due_at'] or row['scheduled_at']), row['schedule_zone']), row['schedule_zone'], int(local.utcoffset().total_seconds()), local.fold, row['id']))
        self.db.execute('CREATE INDEX IF NOT EXISTS reminder_delivery_utc ON reminder_events(status,due_utc)')

    def set_note_icon(self, note_id, name):
        if name not in (None,'note','phone','laptop','plane','book','gift','team','calendar','star','check'):
            raise ValueError('Неизвестная иконка')
        result=self.execute('UPDATE notes SET icon_name=?,updated_at=? WHERE id=? AND deleted=0',
                            (name,datetime.now().isoformat(timespec='seconds'),note_id))
        if result.rowcount!=1:
            raise ValueError('Заметка недоступна')

    def rows(self, sql, args=()):
        return self.db.execute(sql, args).fetchall()

    def execute(self, sql, args=()):
        if self.db.in_transaction:
            return self.db.execute(sql, args)
        with self.db:
            return self.db.execute(sql, args)

    def notification_event(self, event_id):
        rows = self.rows('''SELECT e.*,r.note_id FROM reminder_events e
            JOIN reminders r ON r.id=e.reminder_id JOIN notes n ON n.id=r.note_id
            WHERE e.id=? AND e.revision=r.revision AND r.enabled=1
              AND n.deleted=0 AND n.archived=0 AND e.status!='cancelled' ''', (event_id,))
        return dict(rows[0]) if rows else None

    def handle_notification_action(self, event_id, token, action, now=None):
        if action == 'snooze10':
            action = 'snooze'
        if action not in ('open', 'done', 'snooze'):
            return None
        now = now or datetime.now()
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            event = self.notification_event(event_id)
            if not event or not token or event['token'] != token:
                return None
            if action == 'snooze' and event['status'] not in ('notified', 'pending'):
                return None
            result = {'action':action, 'note_id':event['note_id'], 'event_id':event_id, 'status':event['status'], 'due_at':event['due_at']}
            if action == 'done':
                self.db.execute("UPDATE reminder_events SET status='done',lease_owner=NULL,lease_until=NULL WHERE id=?", (event_id,))
                result['status'] = 'done'
            elif action == 'snooze':
                due = (now + timedelta(minutes=10)).isoformat(timespec='seconds')
                self.db.execute("UPDATE reminder_events SET status='pending',due_at=?,token=?,lease_owner=NULL,lease_until=NULL,next_attempt_at=NULL,attempts=0,last_error=NULL WHERE id=?", (due,uuid4().hex,event_id))
                from app.utils.timezones import utc_stamp
                self.db.execute('UPDATE reminder_events SET due_utc=?,lease_until_utc=NULL,next_attempt_utc=NULL WHERE id=?', (utc_stamp(now + timedelta(minutes=10)), event_id))
                result.update(status='pending', due_at=due)
            return result

    def create_note(self, folder=None, tag=None, favorite=False):
        with self.db:
            stamp=datetime.now().isoformat(timespec='seconds')
            note_id = self.db.execute('INSERT INTO notes(updated_at,created_at,folder_id,favorite) VALUES (?,?,?,?)', (stamp,stamp,folder,int(favorite))).lastrowid
            if tag is not None:
                self.db.execute('INSERT INTO note_tags(note_id,tag_id) VALUES (?,?)', (note_id,tag))
        return note_id

    def backup_to(self, path):
        destination = Path(path).resolve()
        if destination == self.path or (destination.exists() and os.path.samefile(destination,self.path)):
            raise ValueError('Выберите другой файл: это рабочая база заметок.')
        if self.db.in_transaction:
            raise ValueError('Дождитесь завершения сохранения заметок.')
        destination.parent.mkdir(parents=True,exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(prefix='.notes-backup-',suffix='.sqlite3',dir=destination.parent,delete=False) as handle:
                temporary = Path(handle.name)
            connection = sqlite3.connect(temporary)
            try:
                self.db.backup(connection)
            finally:
                connection.close()
            os.replace(temporary,destination)
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return destination

    @staticmethod
    def _collection_table(kind):
        tables = {'folder':'folders','tag':'tags'}
        if kind not in tables:
            raise ValueError('Неизвестный раздел.')
        return tables[kind]

    def rename_collection(self, kind, collection_id, name):
        table = self._collection_table(kind)
        name = name.strip()
        if not name:
            raise ValueError('Введите название.')
        with self.db:
            result = self.db.execute(f'UPDATE {table} SET name=? WHERE id=?',(name,collection_id))
            if result.rowcount != 1:
                raise ValueError('Папка или метка больше не существует.')

    def delete_collection(self, kind, collection_id):
        table = self._collection_table(kind)
        with self.db:
            result = self.db.execute(f'DELETE FROM {table} WHERE id=?',(collection_id,))
            if result.rowcount != 1:
                raise ValueError('Папка или метка больше не существует.')

    def save_note(self, note_id, title, body, folder, favorite, tags):
        names = sorted(set(t.strip() for t in tags.split(',') if t.strip()))
        with self.db:
            previous=self.db.execute('SELECT title,body,workspace_id FROM notes WHERE id=?',(note_id,)).fetchone()
            result = self.db.execute('UPDATE notes SET title=?,body=?,folder_id=?,favorite=?,updated_at=? WHERE id=?', (title,body,folder,int(favorite),datetime.now().isoformat(timespec='seconds'),note_id))
            if result.rowcount != 1:
                raise ValueError('Заметка больше не существует')
            if previous and self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='note_versions'").fetchone():
                import hashlib,json
                for version_title,version_body in [(previous['title'],previous['body']),(title,body)]:
                    digest=hashlib.sha256(json.dumps([version_title,version_body],ensure_ascii=False,separators=(',',':')).encode('utf-8')).hexdigest()
                    latest=self.db.execute('SELECT revision,content_hash,workspace_id FROM note_versions WHERE note_id=? ORDER BY revision DESC LIMIT 1',(note_id,)).fetchone()
                    if latest and latest['content_hash']==digest and latest['workspace_id']==previous['workspace_id']:continue
                    revision=latest['revision']+1 if latest else 1
                    self.db.execute('INSERT INTO note_versions(note_id,revision,title,body,content_hash,workspace_id,recorded_at) VALUES (?,?,?,?,?,?,?)',(note_id,revision,version_title,version_body,digest,previous['workspace_id'],datetime.now().isoformat(timespec='seconds')))
            self.db.execute('DELETE FROM note_tags WHERE note_id=?', (note_id,))
            for name in names:
                self.db.execute('INSERT OR IGNORE INTO tags(name) VALUES (?)', (name,))
                self.db.execute('INSERT INTO note_tags SELECT ?,id FROM tags WHERE name=?', (note_id,name))

    def reminder(self, note_id):
        rows = self.rows('SELECT * FROM reminders WHERE note_id=?', (note_id,))
        if not rows:
            return None
        r = dict(rows[0])
        r['days'] = [x[0] for x in self.rows('SELECT weekday FROM reminder_days WHERE reminder_id=?', (r['id'],))]
        r['times'] = [x[0] for x in self.rows('SELECT time FROM reminder_times WHERE reminder_id=? ORDER BY time', (r['id'],))]
        return r

    def save_reminder(self, note_id, mode, once_at, days, times):
        if mode not in (None,'once','repeat'):
            raise ValueError('Неизвестный тип напоминания')
        if mode=='once':
            value = datetime.fromisoformat(once_at)
            if value.tzinfo is not None:
                raise ValueError('Укажите местное время без часового пояса')
            once_at=value.isoformat(timespec='seconds')
            days,times=[],[]
        elif mode=='repeat':
            days=list(days)
            if not days or any(type(d) is not int or not 0<=d<=6 for d in days):
                raise ValueError('Укажите дни недели и хотя бы одно время')
            days=sorted(set(days))
            values=[time.fromisoformat(t) for t in times]
            if not values or any(t.tzinfo is not None or t.second or t.microsecond for t in values):
                raise ValueError('Укажите местное время в формате ЧЧ:ММ')
            times=sorted(set(t.strftime('%H:%M') for t in values))
            once_at=None
        with self.db:
            if mode is None:
                # Retain the schedule and its audit trail. A revision change also
                # invalidates actions in notifications delivered before disabling.
                self.db.execute('UPDATE reminders SET enabled=0,revision=revision+1 WHERE note_id=? AND enabled=1', (note_id,))
                self.db.execute("UPDATE reminder_events SET status='cancelled',lease_owner=NULL,lease_until=NULL WHERE reminder_id IN (SELECT id FROM reminders WHERE note_id=?) AND status IN ('pending','notified')", (note_id,))
                return
            existing=self.reminder(note_id)
            if existing and existing['enabled'] and (existing['mode'],existing['once_at'],sorted(existing['days']),sorted(existing['times']))==(mode,once_at,days,times):
                return
            if existing:
                rid=existing['id']
                self.db.execute('UPDATE reminders SET mode=?,once_at=?,enabled=1,created_at=?,checked_through=NULL,revision=revision+1 WHERE id=?',(mode,once_at,datetime.now().isoformat(timespec='seconds'),rid))
                self.db.execute('DELETE FROM reminder_days WHERE reminder_id=?',(rid,))
                self.db.execute('DELETE FROM reminder_times WHERE reminder_id=?',(rid,))
                self.db.execute("UPDATE reminder_events SET status='cancelled',lease_owner=NULL,lease_until=NULL WHERE reminder_id=? AND status!='done'",(rid,))
            else:
                rid = self.db.execute('INSERT INTO reminders(note_id,mode,once_at,created_at) VALUES (?,?,?,?)', (note_id,mode,once_at,datetime.now().isoformat(timespec='seconds'))).lastrowid
            from app.utils.timezones import device_zone, utc_stamp
            zone_name = existing['timezone_id'] if existing and existing['timezone_id'] else device_zone()
            self.db.execute('UPDATE reminders SET timezone_id=?,once_utc=?,created_utc=?,checked_through_utc=NULL WHERE id=?',
                            (zone_name, utc_stamp(datetime.fromisoformat(once_at), zone_name) if once_at else None, utc_stamp(datetime.now()), rid))
            self.db.executemany('INSERT INTO reminder_days VALUES (?,?)', [(rid,d) for d in set(days)])
            self.db.executemany('INSERT INTO reminder_times VALUES (?,?)', [(rid,t) for t in set(times)])

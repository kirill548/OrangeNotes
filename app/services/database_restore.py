"""Validated, staged restore; caller must pause every background writer first."""
import os
import sqlite3
import shutil
from contextlib import closing
import tempfile
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from app.database.store import Store

REQUIRED = {
    'notes': {'id','title','body','folder_id','favorite','archived','deleted','updated_at'},
    'folders': {'id','name'}, 'tags': {'id','name'},
    'note_tags': {'note_id','tag_id'},
    'reminders': {'id','note_id','mode','once_at','enabled','created_at'},
    'reminder_days': {'reminder_id','weekday'},
    'reminder_times': {'reminder_id','time'},
    'reminder_events': {'id','reminder_id','scheduled_at','status'},
}


def validate_backup(path):
    path = Path(path).resolve(strict=True)
    if not path.is_file():
        raise ValueError('Выберите файл резервной копии.')
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)) as db:
        db.execute('PRAGMA trusted_schema=OFF')
        if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise ValueError('Резервная копия повреждена.')
        if db.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError('В резервной копии нарушены связи записей.')
        for table, columns in REQUIRED.items():
            object_type = db.execute('SELECT type FROM sqlite_master WHERE name=?',(table,)).fetchone()
            if object_type != ('table',) or not columns <= {r[1] for r in db.execute(f'PRAGMA table_info({table})')}:
                raise ValueError('Это не поддерживаемая база Orange Notes: '+table)
        # Application backups do not contain executable database objects.
        objects=db.execute("SELECT name,sql FROM sqlite_master WHERE type IN ('trigger','view')").fetchall()
        expected="CREATE TRIGGER reminder_note_visibility AFTER UPDATE OF archived,deleted ON notes WHEN OLD.archived != NEW.archived OR OLD.deleted != NEW.deleted BEGIN UPDATE reminders SET checked_through=strftime('%Y-%m-%dT%H:%M:%S','now','localtime') WHERE note_id=NEW.id; UPDATE reminder_events SET status='cancelled',lease_owner=NULL,lease_until=NULL WHERE reminder_id IN (SELECT id FROM reminders WHERE note_id=NEW.id) AND status='pending'; END"
        expected_utc=expected.replace(" WHERE note_id=NEW.id;", ",checked_through_utc=strftime('%Y-%m-%dT%H:%M:%S','now') WHERE note_id=NEW.id;",1)
        canonical=lambda value: ''.join(value.split()).rstrip(';')
        if any(name!='reminder_note_visibility' or canonical(sql) not in {canonical(expected),canonical(expected_utc)} for name,sql in objects):
            raise ValueError('Копия содержит неподдерживаемые SQL-объекты.')
        return {'notes': db.execute('SELECT COUNT(*) FROM notes').fetchone()[0], 'path': path}


def restore_database(store, source):
    """Retain Store identity for UI references. Worker must already be stopped.

    A verified safety copy remains beside the database on success and failure.
    """
    source = Path(source).resolve(strict=True)
    target = store.path.resolve()
    if source == target or os.path.samefile(source,target):
        raise ValueError('Нельзя восстановить базу из самой себя.')
    validate_backup(source)
    descriptor, filename = tempfile.mkstemp(prefix='.notes-restore-', suffix='.sqlite3', dir=target.parent)
    os.close(descriptor)
    staged = Path(filename)
    safety = target.with_name('notes-before-restore-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid4().hex[:8]+'.sqlite3')
    closed = False
    replaced = False
    busy_timeout = store.db.execute('PRAGMA busy_timeout').fetchone()[0]
    try:
        with closing(sqlite3.connect(source.as_uri()+'?mode=ro', uri=True)) as incoming:
            with closing(sqlite3.connect(staged)) as destination:
                incoming.backup(destination)
        validate_backup(staged)
        # Test migrations on the staged copy, never on the user's current data.
        test = Store(staged)
        try:
            test.db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            test.db.execute('PRAGMA journal_mode=DELETE')
        finally:
            test.db.close()
        validate_backup(staged)
        if store.db.in_transaction:
            raise ValueError('Сначала завершите сохранение заметок.')
        checkpoint = store.db.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
        if checkpoint and checkpoint[0]:
            raise ValueError('База используется другим процессом. Закройте его и повторите.')
        store.backup_to(safety)
        validate_backup(safety)
        store.db.close()
        closed = True
        os.replace(staged,target)
        replaced = True
        reopened = Store(target)
        store.db = reopened.db
        store.db.execute(f'PRAGMA busy_timeout={int(busy_timeout)}')
        closed = False
        return safety
    except Exception:
        if closed:
            if replaced:
                for suffix in ('-wal','-shm'):
                    target.with_name(target.name+suffix).unlink(missing_ok=True)
                shutil.copyfile(safety,staged)
                os.replace(staged,target)
            reopened = Store(target)
            store.db = reopened.db
            store.db.execute(f'PRAGMA busy_timeout={int(busy_timeout)}')
        raise
    finally:
        staged.unlink(missing_ok=True)
        for suffix in ('-wal','-shm'):
            staged.with_name(staged.name+suffix).unlink(missing_ok=True)

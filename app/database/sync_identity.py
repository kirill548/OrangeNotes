"""Opt-in UUID identity snapshot; never called by legacy Store initialization.

Integer PKs and every legacy table remain authoritative locally. Copying an
already prepared database preserves its device ID: rotate it BEFORE pairing a
clone (device enrollment/rotation is intentionally outside this prototype).
"""
from pathlib import Path
from contextlib import closing
import os
import sqlite3
import tempfile
from uuid import UUID, uuid4

_ENTITIES = ('notes', 'folders', 'tags', 'reminders')
_UUID_SQL = "(lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' || substr(lower(hex(randomblob(2))),2) || '-' || substr('89ab',abs(random() % 4)+1,1) || substr(lower(hex(randomblob(2))),2) || '-' || lower(hex(randomblob(6))))"


def _initialize(db):
    if db.in_transaction:
        raise ValueError('Identity initialization requires an idle connection')
    required = {'notes': {'id', 'workspace_id'}, 'folders': {'id'},
                'tags': {'id'}, 'reminders': {'id', 'note_id'}}
    for table, columns in required.items():
        info = list(db.execute(f'PRAGMA table_info({table})'))
        if not columns.issubset({row[1] for row in info}) or not any(
                row[1] == 'id' and row[2].upper() == 'INTEGER' and row[5] == 1 for row in info):
            raise ValueError(f'Unsupported legacy schema: {table}')
    if db.execute("SELECT 1 FROM sqlite_master WHERE name GLOB 'sync_*' LIMIT 1").fetchone():
        _validate_metadata(db)
        return
    db.execute('BEGIN IMMEDIATE')
    try:
        db.execute('CREATE TABLE IF NOT EXISTS sync_device(singleton INTEGER PRIMARY KEY CHECK(singleton=1), device_uuid TEXT NOT NULL UNIQUE, schema_version INTEGER NOT NULL CHECK(schema_version=1))')
        db.execute('INSERT OR IGNORE INTO sync_device VALUES(1,?,1)', (str(uuid4()),))
        db.execute('CREATE TABLE IF NOT EXISTS sync_workspaces(local_id INTEGER PRIMARY KEY, workspace_uuid TEXT NOT NULL UNIQUE)')
        db.execute('CREATE TABLE IF NOT EXISTS sync_identities(entity TEXT NOT NULL, local_id INTEGER NOT NULL, entity_uuid TEXT PRIMARY KEY, tombstone INTEGER NOT NULL DEFAULT 0 CHECK(tombstone IN (0,1)))')
        db.execute('CREATE UNIQUE INDEX IF NOT EXISTS sync_live_identity ON sync_identities(entity,local_id) WHERE tombstone=0')
        for workspace in (1, 2):
            db.execute('INSERT OR IGNORE INTO sync_workspaces VALUES(?,?)', (workspace, str(uuid4())))
        db.execute(f'INSERT OR IGNORE INTO sync_workspaces SELECT DISTINCT workspace_id,{_UUID_SQL} FROM notes')
        for table in _ENTITIES:
            db.execute(f"INSERT INTO sync_identities(entity,local_id,entity_uuid) SELECT ?,id,{_UUID_SQL} FROM {table} WHERE NOT EXISTS(SELECT 1 FROM sync_identities i WHERE i.entity=? AND i.local_id={table}.id AND i.tombstone=0)", (table, table))
            db.execute(f"CREATE TRIGGER IF NOT EXISTS sync_{table}_insert AFTER INSERT ON {table} BEGIN INSERT INTO sync_identities(entity,local_id,entity_uuid) VALUES('{table}',new.id,{_UUID_SQL}); END")
            db.execute(f"CREATE TRIGGER IF NOT EXISTS sync_{table}_delete AFTER DELETE ON {table} BEGIN UPDATE sync_identities SET tombstone=1 WHERE entity='{table}' AND local_id=old.id AND tombstone=0; END")
            db.execute(f"CREATE TRIGGER IF NOT EXISTS sync_{table}_move AFTER UPDATE OF id ON {table} WHEN old.id != new.id BEGIN UPDATE sync_identities SET local_id=new.id WHERE entity='{table}' AND local_id=old.id AND tombstone=0; END")
        for action in ('INSERT', 'UPDATE OF workspace_id'):
            name = 'insert' if action == 'INSERT' else 'update'
            db.execute(f'CREATE TRIGGER IF NOT EXISTS sync_workspace_{name} AFTER {action} ON notes BEGIN INSERT OR IGNORE INTO sync_workspaces VALUES(new.workspace_id,{_UUID_SQL}); END')
        db.commit()
    except BaseException:
        db.rollback()
        raise


def _validate_metadata(db):
    """Never repair an interrupted/untrusted identity history in place.

    Missing lifetime triggers may already have aliased a reused local ID, so even
    seemingly reconstructible metadata must fail closed rather than be reseeded.
    """
    def objects(connection):
        return {name: (kind, sql.strip().rstrip(';') if isinstance(sql, str) else None)
                for name, kind, sql in connection.execute(
                    "SELECT name,type,sql FROM sqlite_master WHERE name GLOB 'sync_*'")}
    with closing(sqlite3.connect(':memory:')) as reference:
        reference.execute('CREATE TABLE notes(id INTEGER PRIMARY KEY, workspace_id INTEGER)')
        for table in ('folders', 'tags'):
            reference.execute(f'CREATE TABLE {table}(id INTEGER PRIMARY KEY)')
        reference.execute('CREATE TABLE reminders(id INTEGER PRIMARY KEY, note_id INTEGER)')
        _initialize(reference)
        # Phase-2 tables have their own exact, versioned ownership boundary.
        # Unrecognized sync objects still fail the full schema comparison.
        from app.database.p2p_store import SCHEMA
        phase2_names = {statement.split('(', 1)[0].split()[2] for statement in SCHEMA}
        if phase2_names & set(objects(db)):
            for statement in SCHEMA:
                reference.execute(statement)
        if objects(db) != objects(reference):
            raise ValueError('Incomplete or altered sync identity schema; explicit recovery required')

    seen = set()
    def uuid(value):
        try:
            valid = isinstance(value, str) and str(UUID(value)) == value and UUID(value).version == 4
        except (ValueError, AttributeError):
            valid = False
        if not valid or value in seen:
            raise ValueError('Invalid or duplicated sync UUID')
        seen.add(value)

    device = list(db.execute('SELECT singleton,device_uuid,schema_version FROM sync_device'))
    if len(device) != 1 or device[0][0] != 1 or type(device[0][2]) is not int or device[0][2] != 1:
        raise ValueError('Unsupported sync device metadata version')
    uuid(device[0][1])
    workspaces = set()
    for local_id, value in db.execute('SELECT local_id,workspace_uuid FROM sync_workspaces'):
        if type(local_id) is not int:
            raise ValueError('Invalid workspace identity')
        workspaces.add(local_id)
        uuid(value)
    required = {1, 2} | {row[0] for row in db.execute('SELECT DISTINCT workspace_id FROM notes')}
    if not required <= workspaces:
        raise ValueError('Missing workspace identity')
    actual = {table: {row[0] for row in db.execute(f'SELECT id FROM {table}')} for table in _ENTITIES}
    mapped = {table: set() for table in _ENTITIES}
    for entity, local_id, value, tombstone in db.execute('SELECT entity,local_id,entity_uuid,tombstone FROM sync_identities'):
        if entity not in mapped or type(local_id) is not int or type(tombstone) is not int or tombstone not in (0, 1):
            raise ValueError('Invalid sync identity row')
        uuid(value)
        if not tombstone:
            if local_id in mapped[entity]:
                raise ValueError('Duplicate live identity')
            mapped[entity].add(local_id)
    if actual != mapped:
        raise ValueError('Stale or missing live sync identities; explicit recovery required')


def _snapshot_copy(source, destination, transform):
    """Create a new compatible database with UUID identities, without editing source.

    SQLite backup includes committed WAL contents. Destination must not exist;
    publication uses a hard link so racing creators cannot be overwritten. Both
    temporary and final files live on the same filesystem, with private mode.
    An already migrated source is supported and retains stable UUIDs.
    """
    source, destination = Path(source).resolve(strict=True), Path(destination).absolute()
    if destination.resolve() == source or destination.exists() or destination.is_symlink():
        raise FileExistsError('Destination must be a new file, distinct from source')
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.sync-identity-', suffix='.db', dir=destination.parent)
    os.close(fd)
    temporary = Path(temporary)
    try:
        with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as original:
            copied = sqlite3.connect(temporary)
            try:
                original.backup(copied)
                copied.execute('PRAGMA journal_mode=DELETE')
                transform(copied)
                if copied.execute('PRAGMA integrity_check').fetchone()[0] != 'ok' or list(copied.execute('PRAGMA foreign_key_check')):
                    raise ValueError('Database integrity check failed')
            finally:
                copied.close()
        os.link(temporary, destination)
        return destination
    finally:
        temporary.unlink(missing_ok=True)
        for suffix in ('-wal', '-shm', '-journal'):
            Path(str(temporary) + suffix).unlink(missing_ok=True)


def prepare_identity_copy(source, destination):
    """Explicitly prepare a UUID-enabled copy; never modify source or overwrite."""
    return _snapshot_copy(source, destination, _initialize)


def export_legacy_copy(source, destination):
    """Export data for v0.1.5 restore, removing only this prototype's metadata.

    Legacy restore intentionally rejects UUID triggers. UUID identity/history is
    NOT retained by this compatibility export; reconnecting requires enrollment
    and a full snapshot, never resuming the former device's synchronization log.
    """
    def strip_metadata(db):
        if db.execute("SELECT 1 FROM sqlite_master WHERE name GLOB 'sync_*' LIMIT 1").fetchone():
            _validate_metadata(db)
        db.execute('BEGIN IMMEDIATE')
        try:
            trigger_names = [f'sync_{entity}_{operation}' for entity in _ENTITIES
                             for operation in ('insert', 'delete', 'move')]
            trigger_names += ['sync_workspace_insert', 'sync_workspace_update']
            for name in trigger_names:
                db.execute(f'DROP TRIGGER IF EXISTS {name}')
            from app.database.p2p_store import SCHEMA
            # Referencing ACK table must be removed before its outbox parent.
            phase2_tables = [statement.split('(', 1)[0].split()[2] for statement in SCHEMA]
            for name in reversed(phase2_tables):
                db.execute(f'DROP TABLE IF EXISTS {name}')
            for name in ('sync_identities', 'sync_workspaces', 'sync_device'):
                db.execute(f'DROP TABLE IF EXISTS {name}')
            db.commit()
        except BaseException:
            db.rollback()
            raise
        # Keep the production security allowlist intact. Unknown triggers still
        # fail validation and the staged snapshot is never installed.
        from app.services.database_restore import validate_backup
        filename = db.execute('PRAGMA database_list').fetchone()[2]
        validate_backup(filename)
    return _snapshot_copy(source, destination, strip_metadata)


def identity_for(db, entity, local_id):
    """Return active UUID; graph rows are deliberately not sync entities."""
    if entity not in _ENTITIES:
        raise ValueError('Unsupported entity')
    row = db.execute('SELECT entity_uuid FROM sync_identities WHERE entity=? AND local_id=? AND tombstone=0', (entity, local_id)).fetchone()
    return row[0] if row else None


def inspect_identities(db):
    return {'device_uuid': db.execute('SELECT device_uuid FROM sync_device WHERE singleton=1').fetchone()[0],
            'workspaces': dict(db.execute('SELECT local_id,workspace_uuid FROM sync_workspaces')),
            'entities': [dict(zip(('entity', 'local_id', 'uuid', 'tombstone'), row)) for row in db.execute('SELECT entity,local_id,entity_uuid,tombstone FROM sync_identities ORDER BY entity,local_id,entity_uuid')]}

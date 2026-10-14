import hashlib
import sqlite3
from uuid import UUID

import pytest
from app.database.store import Store
from app.database.sync_identity import prepare_identity_copy, identity_for, inspect_identities, _initialize
from app.services.knowledge_graph import KnowledgeGraph


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_store(path):
    store = Store(path)
    with store.db:
        store.db.executemany("INSERT INTO notes(id,title,body,workspace_id,updated_at) VALUES(?,?,?,?,?)", [(1,'План','Текст [[Работа]]',1,'2026-10-14'), (2,'Работа','<b>Текст</b>',1,'2026-10-14'), (3,'Личное','секрет',2,'2026-10-14')])
    store.save_reminder(1, 'once', '2026-10-15T12:00:00', [], [])
    KnowledgeGraph(path).refresh()
    return store


def test_backup_preserves_legacy_tables_wal_and_source(tmp_path):
    source, dest = tmp_path/'legacy.db', tmp_path/'copy.db'
    store = make_store(source)
    # Keep source open: newest data can exist only in WAL.
    before = digest(source)
    tables = [r[0] for r in store.db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    expected = {t: list(map(tuple, store.db.execute(f'SELECT * FROM "{t}"'))) for t in tables}
    prepare_identity_copy(source, dest)
    assert digest(source) == before
    with sqlite3.connect(dest) as copied:
        for t, rows in expected.items():
            assert list(copied.execute(f'SELECT * FROM "{t}"')) == rows
        assert copied.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert list(copied.execute('PRAGMA foreign_key_check')) == []
        assert identity_for(copied, 'notes', 1)
    # Unmodified legacy Store initialization, note saving and reminder reading.
    legacy = Store(dest)
    legacy.save_note(1, 'Изменено', 'Сохранено', None, False, '')
    assert legacy.db.execute('SELECT body FROM notes WHERE id=1').fetchone()[0] == 'Сохранено'
    assert legacy.db.execute('SELECT once_at FROM reminders WHERE note_id=1').fetchone()[0] == '2026-10-15T12:00:00'
    legacy.db.close()
    store.db.close()


def test_idempotence_uuidv4_and_id_reuse(tmp_path):
    source = tmp_path/'source.db'
    store = make_store(source)
    store.db.close()
    first = prepare_identity_copy(source, tmp_path/'first.db')
    with sqlite3.connect(first) as db:
        before = inspect_identities(db)
        _initialize(db)
        assert before == inspect_identities(db)
        old = identity_for(db, 'notes', 3)
        db.execute('DELETE FROM notes WHERE id=3')
        db.execute("INSERT INTO notes(id,title,body,workspace_id,updated_at) VALUES(3,'New','',9,'2026')")
        db.executemany("INSERT INTO notes(title,body,workspace_id,updated_at) VALUES(?,'',1,'2026')", [(str(i),) for i in range(1000)])
        db.commit()
        assert old != identity_for(db, 'notes', 3)
        assert db.execute('SELECT tombstone FROM sync_identities WHERE entity_uuid=?', (old,)).fetchone()[0] == 1
        data = inspect_identities(db)
        values = [r['uuid'] for r in data['entities']] + list(data['workspaces'].values()) + [data['device_uuid']]
        assert len(values) == len(set(values))
        assert all(UUID(value).version == 4 for value in values)
        assert data['workspaces'][1] != data['workspaces'][2]
        assert 9 in data['workspaces']
        with pytest.raises(ValueError):
            identity_for(db, 'note_links', 1)
    second = prepare_identity_copy(first, tmp_path/'second.db')
    with sqlite3.connect(first) as a, sqlite3.connect(second) as b:
        assert inspect_identities(a) == inspect_identities(b)


def test_independent_databases_never_match_workspace_ids(tmp_path):
    values = []
    for n in range(2):
        source = tmp_path/f'source{n}.db'
        store = make_store(source); store.db.close()
        dest = prepare_identity_copy(source, tmp_path/f'dest{n}.db')
        with sqlite3.connect(dest) as db:
            values.append(inspect_identities(db))
    assert values[0]['device_uuid'] != values[1]['device_uuid']
    assert values[0]['workspaces'][1] != values[1]['workspaces'][1]


def test_reject_overwrite_and_invalid_schema_without_residue(tmp_path):
    source = tmp_path/'bad.db'
    with sqlite3.connect(source) as db:
        db.execute('CREATE TABLE unrelated(value TEXT)')
    before = digest(source)
    with pytest.raises(ValueError):
        prepare_identity_copy(source, tmp_path/'failed.db')
    assert not (tmp_path/'failed.db').exists()
    assert not list(tmp_path.glob('.sync-identity-*'))
    assert digest(source) == before
    with pytest.raises(FileExistsError):
        prepare_identity_copy(source, source)
    existing = tmp_path/'existing.db'; existing.write_bytes(b'keep')
    with pytest.raises(FileExistsError):
        prepare_identity_copy(source, existing)
    assert existing.read_bytes() == b'keep'


def test_initialization_refuses_active_transaction(tmp_path):
    store = make_store(tmp_path/'source.db')
    store.db.execute("UPDATE notes SET title='uncommitted' WHERE id=1")
    with pytest.raises(ValueError):
        _initialize(store.db)
    assert store.db.in_transaction
    store.db.rollback(); store.db.close()


def test_exact_released_store_can_open_and_write(tmp_path):
    import subprocess
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(['git', '-c', f'safe.directory={root}', 'show', 'v0.1.5:app/database/store.py'], cwd=root, capture_output=True)
    if result.returncode:
        pytest.skip('Released git object unavailable in shallow checkout')
    namespace = {'__name__': 'released_store'}
    exec(compile(result.stdout.decode('utf-8-sig'), 'v015/store.py', 'exec'), namespace)
    source = tmp_path/'legacy.db'
    original = namespace['Store'](source)
    note_id = original.create_note()
    original.save_note(note_id, 'Прежняя версия', 'Текст', None, False, '')
    original.db.close()
    dest = prepare_identity_copy(source, tmp_path/'with-identities.db')
    legacy = namespace['Store'](dest)
    old_uuid = identity_for(legacy.db, 'notes', note_id)
    legacy.save_note(note_id, 'Новый текст', 'Сохранено', None, False, '')
    with legacy.db:
        legacy.db.execute('UPDATE notes SET id=42 WHERE id=?', (note_id,))
    assert identity_for(legacy.db, 'notes', 42) == old_uuid
    assert identity_for(legacy.db, 'notes', note_id) is None
    legacy.create_note()
    assert legacy.db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    legacy.db.close()

def test_partial_migration_failure_is_not_published(tmp_path):
    source = tmp_path/'collision.db'
    store = make_store(source)
    with store.db:
        store.db.execute('CREATE TABLE sync_identities(incompatible TEXT)')
    store.db.close()
    before = digest(source)
    with pytest.raises(ValueError):
        prepare_identity_copy(source, tmp_path/'rejected.db')
    assert digest(source) == before
    assert not (tmp_path/'rejected.db').exists()
    assert not list(tmp_path.glob('.sync-identity-*'))

def test_legacy_export_passes_exact_v015_restore_and_keeps_all_rows(tmp_path):
    import subprocess
    from pathlib import Path
    from app.database.sync_identity import export_legacy_copy
    root = Path(__file__).resolve().parents[1]
    released = {}
    for module in ('store', 'restore'):
        path = 'app/database/store.py' if module == 'store' else 'app/services/database_restore.py'
        result = subprocess.run(['git', '-c', f'safe.directory={root}', 'show', f'v0.1.5:{path}'], cwd=root, capture_output=True)
        if result.returncode:
            pytest.skip('Released git object unavailable in shallow checkout')
        namespace = {'__name__': f'released_{module}'}
        exec(compile(result.stdout.decode('utf-8-sig'), f'v015/{path}', 'exec'), namespace)
        released[module] = namespace
    released['restore']['Store'] = released['store']['Store']
    source = tmp_path/'original.db'
    store = make_store(source); store.db.close()
    migrated = prepare_identity_copy(source, tmp_path/'uuid.db')
    before = digest(migrated)
    with pytest.raises(ValueError, match='SQL'):
        released['restore']['validate_backup'](migrated)
    exported = export_legacy_copy(migrated, tmp_path/'export.db')
    assert digest(migrated) == before
    assert released['restore']['validate_backup'](exported)['notes'] == 3
    repeated = export_legacy_copy(exported, tmp_path/'export-again.db')
    with sqlite3.connect(source) as original, sqlite3.connect(repeated) as copied:
        tables = [r[0] for r in original.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        for table in tables:
            assert list(original.execute(f'SELECT * FROM "{table}"')) == list(copied.execute(f'SELECT * FROM "{table}"'))
        assert not copied.execute("SELECT name FROM sqlite_master WHERE name LIKE 'sync_%'").fetchall()
    destination = released['store']['Store'](tmp_path/'restored.db')
    safety = released['restore']['restore_database'](destination, exported)
    assert safety.is_file()
    assert destination.db.execute('SELECT count(*) FROM notes').fetchone()[0] == 3
    assert destination.db.execute('SELECT once_at FROM reminders WHERE note_id=1').fetchone()[0] == '2026-10-15T12:00:00'
    assert destination.db.execute('SELECT count(*) FROM note_links').fetchone()[0] > 0
    assert destination.db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    destination.db.close()
    with pytest.raises(FileExistsError):
        export_legacy_copy(migrated, exported)
    with pytest.raises(FileExistsError):
        export_legacy_copy(migrated, migrated)


def test_legacy_export_does_not_strip_unknown_sql_objects(tmp_path):
    from app.database.sync_identity import export_legacy_copy
    source = tmp_path/'unknown.db'
    store = make_store(source)
    with store.db:
        store.db.execute('CREATE TRIGGER unknown_trigger AFTER INSERT ON tags BEGIN SELECT 1; END')
    store.db.close()
    before = digest(source)
    with pytest.raises(ValueError, match='SQL'):
        export_legacy_copy(source, tmp_path/'not-published.db')
    assert digest(source) == before
    assert not (tmp_path/'not-published.db').exists()

@pytest.mark.parametrize('reuse', [False, True])
def test_incomplete_lifetime_triggers_are_never_repaired(tmp_path, reuse):
    source = tmp_path/'original.db'
    store = make_store(source); store.db.close()
    prepared = prepare_identity_copy(source, tmp_path/'prepared.db')
    with sqlite3.connect(prepared) as db:
        db.execute('DROP TRIGGER sync_notes_delete')
        db.execute('DELETE FROM notes WHERE id=3')
        if reuse:
            db.execute('DROP TRIGGER sync_notes_insert')
            db.execute("INSERT INTO notes(id,title,body,workspace_id,updated_at) VALUES(3,'Reused','',2,'2026')")
    before = digest(prepared)
    with pytest.raises(ValueError, match='sync identity schema'):
        prepare_identity_copy(prepared, tmp_path/'rejected.db')
    assert digest(prepared) == before
    assert not (tmp_path/'rejected.db').exists()


@pytest.mark.parametrize('damage', ['uuid', 'version', 'trigger', 'missing_mapping', 'stale_mapping'])
def test_corrupt_existing_identity_metadata_fails_closed(tmp_path, damage):
    source = tmp_path/'original.db'
    store = make_store(source); store.db.close()
    prepared = prepare_identity_copy(source, tmp_path/'prepared.db')
    with sqlite3.connect(prepared) as db:
        if damage == 'uuid':
            db.execute("UPDATE sync_device SET device_uuid='not-a-uuid'")
        elif damage == 'version':
            db.execute('PRAGMA ignore_check_constraints=ON')
            db.execute('UPDATE sync_device SET schema_version=2')
        elif damage == 'trigger':
            db.execute('DROP TRIGGER sync_notes_delete')
            db.execute('CREATE TRIGGER sync_notes_delete AFTER DELETE ON notes BEGIN SELECT 1; END')
        elif damage == 'missing_mapping':
            db.execute("DELETE FROM sync_identities WHERE entity='notes' AND local_id=1")
        else:
            db.execute("UPDATE sync_identities SET local_id=999 WHERE entity='notes' AND local_id=1")
    before = digest(prepared)
    with pytest.raises(ValueError):
        prepare_identity_copy(prepared, tmp_path/'rejected.db')
    assert digest(prepared) == before
    assert not (tmp_path/'rejected.db').exists()
    assert not list(tmp_path.glob('.sync-identity-*'))

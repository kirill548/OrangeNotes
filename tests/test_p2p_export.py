"""Phase-2 snapshots remain opt-in and legacy export keeps strict v0.1.5 guards."""
import hashlib
import sqlite3
from uuid import uuid4

import pytest

from app.database.store import Store
from app.database.sync_identity import prepare_identity_copy, export_legacy_copy, inspect_identities
from app.database.p2p_store import P2PStore
from app.services.database_restore import validate_backup


def uid():
    return str(uuid4())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


@pytest.fixture
def phase2(tmp_path):
    legacy = tmp_path / 'legacy.db'
    original = Store(legacy)
    original.db.close()
    path = prepare_identity_copy(legacy, tmp_path / 'phase2.db')
    with sqlite3.connect(path) as db:
        identity = inspect_identities(db)
    workspace = identity['workspaces'][1]
    peer = uid()
    store = P2PStore(path,workspace_id=workspace,local_workspace_id=1,
                     actor_id=identity['device_uuid'],authorized_actors=[peer])
    entity = uid()
    wire = store.mutate_note(entity,{'title':'Перенос','body':'[[Связь]] — сохранённый текст'})
    store.acknowledge(peer,{'actor_id':peer,'workspace_id':workspace,
                           'sha256':hashlib.sha256(wire.encode('utf-8')).hexdigest()})
    yield store,path,entity,peer
    store.close()


def contents(db):
    return list(db.iterdump())


def test_phase2_export_is_v015_compatible_without_modifying_source(phase2,tmp_path):
    store,path,entity,peer=phase2
    before=contents(store.db)
    bytes_before=digest(path)
    wal=path.with_name(path.name+'-wal')
    wal_before=digest(wal)
    output=export_legacy_copy(path,tmp_path/'restore-v015.db')
    assert validate_backup(output)['notes']==1
    with sqlite3.connect(output) as exported:
        assert exported.execute('SELECT title,body,deleted FROM notes').fetchone()==('Перенос','[[Связь]] — сохранённый текст',0)
        assert exported.execute("SELECT count(*) FROM sqlite_master WHERE name GLOB 'sync_*'").fetchone()[0]==0
        assert list(exported.execute('PRAGMA foreign_key_check'))==[]
    assert contents(store.db)==before
    assert digest(path)==bytes_before
    assert digest(wal)==wal_before
    assert store.db.execute('SELECT count(*) FROM sync_outbox_ack').fetchone()[0]==1
    assert store.get(entity)['title']=='Перенос'


def test_repeated_prepare_preserves_phase2_identity_and_durable_events(phase2,tmp_path):
    store,path,entity,peer=phase2
    before=contents(store.db)
    output=prepare_identity_copy(path,tmp_path/'second-prepared.db')
    with sqlite3.connect(output) as db:
        assert inspect_identities(db)==inspect_identities(store.db)
        for table in ('sync_events','sync_outbox','sync_outbox_ack','sync_inbox','sync_p2p_schema'):
            assert list(db.execute(f'SELECT * FROM {table}'))==list(store.db.execute(f'SELECT * FROM {table}'))
    reopened=P2PStore(output,workspace_id=store.workspace_id,local_workspace_id=1,
                      actor_id=store.actor_id,authorized_actors=[peer])
    try:
        assert reopened.get(entity)==store.get(entity)
        assert reopened.pending_outbox(peer)==[]
    finally:
        reopened.close()
    assert contents(store.db)==before
    # A copied enrollment deliberately keeps its device UUID. It is a backup,
    # not permission to operate two simultaneous replicas with the same actor.


@pytest.mark.parametrize('operation',[prepare_identity_copy,export_legacy_copy])
@pytest.mark.parametrize('damage',['partial','modified','unknown'])
def test_invalid_phase2_schema_rejected_without_published_output(phase2,tmp_path,operation,damage):
    store,path,_,_=phase2
    if damage=='partial':
        store.db.execute('DROP TABLE sync_inbox')
    elif damage=='modified':
        store.db.execute('ALTER TABLE sync_outbox ADD COLUMN unexpected TEXT')
    else:
        store.db.execute('CREATE TABLE sync_unrecognized(payload TEXT)')
    store.db.commit()
    before=contents(store.db)
    output=tmp_path/(operation.__name__+'-'+damage+'.db')
    with pytest.raises(ValueError,match='Incomplete or altered'):
        operation(path,output)
    assert not output.exists()
    assert not list(tmp_path.glob('.sync-identity-*'))
    assert contents(store.db)==before

"""Durability, scope and rollback tests use explicit disposable database copies."""
import hashlib
import json
import sqlite3
from uuid import uuid4

import pytest

from app.database.store import Store
from app.database.sync_identity import prepare_identity_copy, inspect_identities
from app.database.p2p_store import P2PStore
from app.services.p2p_sync import DeltaError, Replica


def uid():
    return str(uuid4())


def prepared(tmp_path, name):
    source = tmp_path / (name + '-legacy.db')
    legacy = Store(source)
    legacy.db.close()
    path = prepare_identity_copy(source, tmp_path / (name + '.db'))
    with sqlite3.connect(path) as db:
        actor = inspect_identities(db)['device_uuid']
    return path, actor


@pytest.fixture
def pair(tmp_path):
    workspace = uid()
    pa, aa = prepared(tmp_path, 'a')
    pb, ab = prepared(tmp_path, 'b')
    a = P2PStore(pa, workspace_id=workspace, local_workspace_id=1, actor_id=aa, authorized_actors=[ab])
    b = P2PStore(pb, workspace_id=workspace, local_workspace_id=2, actor_id=ab, authorized_actors=[aa])
    yield a, b, pa, pb
    a.close()
    b.close()


def test_atomic_roundtrip_replay_and_peer_ack(pair):
    a, b, _, _ = pair
    entity = uid()
    wire = a.mutate_note(entity, {'title':'Встреча', 'body':'Текст [[Связь]]'})
    assert a.db.execute('PRAGMA journal_mode').fetchone()[0] == 'wal'
    receipt = b.receive_delta(wire, a.actor_id)
    assert b.get(entity) == a.get(entity)
    assert b.db.execute('SELECT workspace_id FROM notes').fetchone()[0] == 2
    before = b.db.total_changes
    assert b.receive_delta(wire, a.actor_id) == receipt
    assert b.db.total_changes == before
    a.acknowledge(b.actor_id, receipt)
    assert a.pending_outbox(b.actor_id) == []
    assert a.db.execute('SELECT count(*) FROM sync_outbox').fetchone()[0] == 1
    assert list(b.db.execute('PRAGMA foreign_key_check')) == []


def test_restart_preserves_clock_outbox_tombstones_and_conflicts(pair):
    a, b, pa, pb = pair
    entity = uid()
    wa = a.mutate_note(entity, {'body':'A'})
    wb = b.mutate_note(entity, {'body':'B'})
    a.receive_delta(wb, b.actor_id)
    b.receive_delta(wa, a.actor_id)
    deleted = a.mutate_note(entity, {'lifecycle':'deleted'})
    b.receive_delta(deleted, a.actor_id)
    expected = a.get(entity), a.body_conflicts(entity)
    with_reopen = P2PStore(pa, workspace_id=a.workspace_id, local_workspace_id=1, actor_id=a.actor_id, authorized_actors=[b.actor_id])
    try:
        assert (with_reopen.get(entity), with_reopen.body_conflicts(entity)) == expected
        assert with_reopen.pending_outbox(b.actor_id) == [wa, deleted]
        next_wire = with_reopen.mutate_note(entity, {'title':'Не восстанавливать'})
        assert json.loads(next_wire)['clock'] > json.loads(deleted)['clock']
        assert with_reopen.get(entity)['lifecycle'] == 'deleted'
    finally:
        with_reopen.close()


@pytest.mark.parametrize('direction', ['outgoing', 'incoming'])
def test_injected_sql_failure_rolls_back_entire_transaction(pair, direction):
    a, b, _, _ = pair
    entity = uid()
    target = a if direction == 'outgoing' else b
    table = 'sync_outbox' if direction == 'outgoing' else 'sync_inbox'
    target.db.execute(f"CREATE TEMP TRIGGER fail_sync BEFORE INSERT ON {table} BEGIN SELECT RAISE(ABORT,'injected'); END")
    wire = None if direction == 'outgoing' else a.mutate_note(entity, {'body':'Remote'})
    with pytest.raises(sqlite3.IntegrityError):
        if direction == 'outgoing':
            a.mutate_note(entity, {'body':'Local'})
        else:
            b.receive_delta(wire, a.actor_id)
    assert target.db.execute('SELECT count(*) FROM notes').fetchone()[0] == 0
    assert target.db.execute('SELECT count(*) FROM sync_events').fetchone()[0] == 0
    assert target.db.execute(f'SELECT count(*) FROM {table}').fetchone()[0] == 0
    target.db.execute('DROP TRIGGER fail_sync')
    retried = a.mutate_note(entity, {'body':'Local'}) if direction == 'outgoing' else b.receive_delta(wire, a.actor_id)
    assert retried
    assert target.db.execute('SELECT clock FROM sync_events').fetchone()[0] == 1


def test_equivocation_and_untrusted_actor_are_atomic(pair):
    a, b, _, _ = pair
    entity = uid()
    wire = a.mutate_note(entity, {'body':'Trusted'})
    b.receive_delta(wire, a.actor_id)
    data = json.loads(wire)
    data['ops'][0]['fields']['body'] = 'Equivocation'
    with pytest.raises(DeltaError):
        b.receive_delta(json.dumps(data), a.actor_id)
    with pytest.raises(DeltaError):
        b.receive_delta(wire, uid())
    b.revoke_peer(a.actor_id)
    with pytest.raises(DeltaError):
        b.receive_delta(wire, a.actor_id)
    assert b.get(entity)['body'] == 'Trusted'
    assert b.db.execute('SELECT count(*) FROM sync_inbox').fetchone()[0] == 1


def test_ack_is_peer_specific_and_rejects_forgery(pair):
    a, b, _, _ = pair
    c = uid()
    a.grant_peer(c)
    wire = a.mutate_note(uid(), {'title':'Note'})
    receipt = b.receive_delta(wire, a.actor_id)
    a.acknowledge(b.actor_id, receipt)
    assert a.pending_outbox(c) == [wire]
    with pytest.raises(DeltaError):
        a.acknowledge(c, receipt)
    with pytest.raises(DeltaError):
        a.acknowledge(c, {**receipt,'actor_id':c,'sha256':'0'*64})
    a.revoke_peer(c)
    with pytest.raises(DeltaError):
        a.pending_outbox(c)


def test_foreign_workspace_note_uuid_cannot_be_overwritten(pair):
    a, b, _, _ = pair
    stamp = '2026-10-14T12:00:00'
    local = b.db.execute('INSERT INTO notes(title,workspace_id,updated_at,created_at) VALUES(?,?,?,?)', ('Private',1,stamp,stamp)).lastrowid
    entity = b.db.execute("SELECT entity_uuid FROM sync_identities WHERE entity='notes' AND local_id=?", (local,)).fetchone()[0]
    b.db.commit()
    wire = a.mutate_note(entity, {'title':'Attack'})
    with pytest.raises(DeltaError):
        b.receive_delta(wire, a.actor_id)
    assert b.db.execute('SELECT title FROM notes WHERE id=?',(local,)).fetchone()[0] == 'Private'
    assert b.db.execute('SELECT count(*) FROM sync_inbox').fetchone()[0] == 0


def test_existing_integer_ids_differ_across_devices(pair):
    a, b, _, _ = pair
    b.mutate_note(uid(), {'title':'Other'})
    entity = uid()
    b.receive_delta(a.mutate_note(entity, {'title':'Shared'}), a.actor_id)
    ids = [node.db.execute('SELECT local_id FROM sync_identities WHERE entity_uuid=?',(entity,)).fetchone()[0] for node in (a,b)]
    assert ids == [1,2]


def test_enrollment_mismatch_rejected(pair):
    a, b, pa, _ = pair
    with pytest.raises(DeltaError):
        P2PStore(pa, workspace_id=a.workspace_id, local_workspace_id=1, actor_id=uid())
    with pytest.raises(DeltaError):
        P2PStore(pa, workspace_id=uid(), local_workspace_id=1, actor_id=a.actor_id)


def test_scope_and_authenticated_wire_actor_rejected(pair):
    a, b, _, _ = pair
    foreign = Replica(uid(), a.actor_id).make_delta(uid(), {'title':'Foreign'})
    with pytest.raises(DeltaError):
        b.receive_delta(foreign, a.actor_id)
    c = uid()
    forged = Replica(a.workspace_id, c).make_delta(uid(), {'title':'Spoof'})
    with pytest.raises(DeltaError):
        b.receive_delta(forged, a.actor_id)
    assert b.db.execute('SELECT count(*) FROM notes').fetchone()[0] == 0

def test_legacy_baseline_seeds_deleted_state_and_initial_outbox(tmp_path):
    source = tmp_path / 'old.db'
    old = Store(source)
    stamp = '2026-10-14T00:00:00'
    old.db.execute('INSERT INTO notes(title,body,deleted,workspace_id,updated_at,created_at) VALUES(?,?,?,?,?,?)',('Deleted','Keep history',1,1,stamp,stamp))
    old.db.commit()
    old.db.close()
    path = prepare_identity_copy(source,tmp_path/'prepared.db')
    with sqlite3.connect(path) as db:
        identities = inspect_identities(db)
    workspace = identities['workspaces'][1]
    actor = identities['device_uuid']
    entity = identities['entities'][0]['uuid']
    peer = uid()
    local = P2PStore(path,workspace_id=workspace,local_workspace_id=1,actor_id=actor,authorized_actors=[peer])
    try:
        assert local.get(entity)['lifecycle'] == 'deleted'
        assert len(local.pending_outbox(peer)) == 1
        foreign = Replica(workspace,peer)
        foreign.make_delta(uid(), {'title':'advance clock'})
        local.receive_delta(foreign.make_delta(entity,{'body':'Updated remotely'}),peer)
        assert local.get(entity)['lifecycle'] == 'deleted'
        assert local.db.execute('SELECT deleted FROM notes').fetchone()[0] == 1
    finally:
        local.close()


@pytest.mark.parametrize('kind',['body','delete','new'])
def test_legacy_mutations_outside_sync_boundary_fail_closed(pair,kind):
    a,b,_,_ = pair
    entity=uid()
    a.mutate_note(entity,{'title':'Before','body':'Original'})
    if kind=='body':
        a.db.execute("UPDATE notes SET body='unsynchronized'")
    elif kind=='delete':
        a.db.execute('DELETE FROM notes')
    else:
        a.db.execute("INSERT INTO notes(updated_at,created_at) VALUES('now','now')")
    a.db.commit()
    events=a.db.execute('SELECT count(*) FROM sync_events').fetchone()[0]
    with pytest.raises(DeltaError,match='Out-of-band'):
        a.mutate_note(entity,{'title':'Overwrite'})
    assert a.db.execute('SELECT count(*) FROM sync_events').fetchone()[0] == events


def test_dirty_graph_receives_same_note_mutation(pair):
    a,b,_,_=pair
    entity=uid()
    b.receive_delta(a.mutate_note(entity,{'title':'Galaxy','body':'[[Neighbour]] Searchneedle'}),a.actor_id)
    # Production graph triggers, rather than manual parallel indexing, own
    # derived indexes. The durable note and dirty queue share the transaction.
    from app.services.knowledge_graph import KnowledgeGraph
    assert b.db.execute('SELECT count(*) FROM note_links_dirty').fetchone()[0] == 1
    graph=KnowledgeGraph(b.db.execute('PRAGMA database_list').fetchone()[2])
    graph.refresh()
    assert b.db.execute('SELECT link_text FROM note_links').fetchone()[0] == 'Neighbour'
    assert b.db.execute('SELECT title FROM notes').fetchone()[0]=='Galaxy'
    assert list(b.db.execute('PRAGMA foreign_key_check'))==[]

def test_failed_existing_note_update_retains_original_and_dirty_state(pair):
    a,b,_,_=pair
    entity=uid()
    a.mutate_note(entity,{'title':'Original','body':'Before'})
    a.db.execute('DELETE FROM note_links_dirty')
    a.db.execute('DELETE FROM note_links_title_dirty')
    a.db.commit()
    a.db.execute("CREATE TEMP TRIGGER fail_existing BEFORE INSERT ON sync_outbox BEGIN SELECT RAISE(ABORT,'injected'); END")
    with pytest.raises(sqlite3.IntegrityError):
        a.mutate_note(entity,{'title':'Changed','body':'After'})
    assert a.db.execute('SELECT title,body FROM notes').fetchone()==('Original','Before')
    assert a.db.execute('SELECT count(*) FROM note_links_dirty').fetchone()[0]==0
    assert a.get(entity)['body']=='Before'
    assert len(a.pending_outbox(b.actor_id))==1


def test_persisted_clock_column_tampering_fails_closed(pair):
    a,b,_,_=pair
    a.mutate_note(uid(),{'title':'Durable'})
    a.db.execute('UPDATE sync_events SET clock=clock+10')
    a.db.commit()
    with pytest.raises(DeltaError,match='Corrupt durable'):
        a.mutate_note(uid(),{'title':'Should not happen'})
    assert a.db.execute('SELECT count(*) FROM notes').fetchone()[0]==1

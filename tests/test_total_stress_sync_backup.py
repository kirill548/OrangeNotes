"""Adversarial synthetic backup inputs; no user files or network required."""
import sqlite3
from uuid import uuid4

import pytest

from app.database.store import Store
from app.services.database_restore import _canonical_sql, restore_database, validate_backup
from app.database.sync_identity import prepare_identity_copy, inspect_identities
from app.database.p2p_store import P2PStore


@pytest.fixture
def databases(tmp_path):
    live = Store(tmp_path / 'live.sqlite3')
    note = live.create_note()
    live.save_note(note, 'retain original', 'local data', None, False, '')
    candidate = Store(tmp_path / 'candidate.sqlite3')
    other = candidate.create_note()
    candidate.save_note(other, 'candidate', 'incoming data', None, False, '')
    candidate.db.close()
    yield live, tmp_path / 'candidate.sqlite3'
    live.db.close()


def test_trigger_literal_whitespace_cannot_bypass_allowlist(databases):
    live, path = databases
    with sqlite3.connect(path) as db:
        sql = db.execute("SELECT sql FROM sqlite_master WHERE name='reminder_note_visibility'").fetchone()[0]
        assert "'cancelled'" in sql
        db.execute('DROP TRIGGER reminder_note_visibility')
        db.execute(sql.replace("'cancelled'", "'can celled'"))
    with pytest.raises(ValueError, match='SQL'):
        restore_database(live, path)
    assert live.rows('SELECT title FROM notes')[0][0] == 'retain original'


@pytest.mark.parametrize('literal', ["'a b'", "'a'' b'", '"quoted name"', '[quoted name]', '`quoted name`'])
def test_sql_canonicalization_preserves_quoted_whitespace(literal):
    assert _canonical_sql(' SELECT  ' + literal + ' ;') == 'SELECT' + literal


@pytest.mark.parametrize('damage', ['blob', 'invalid-utf8', 'null-byte'])
def test_malformed_body_backup_cannot_replace_live_database(databases, damage):
    live, path = databases
    with sqlite3.connect(path) as db:
        if damage == 'blob':
            db.execute('UPDATE notes SET body=?', (b'\xff\xfe',))
        elif damage == 'invalid-utf8':
            db.execute("UPDATE notes SET body=CAST(X'FFFF' AS TEXT)")
        else:
            db.execute('UPDATE notes SET body=?', ('x\x00y',))
    with pytest.raises((ValueError, sqlite3.Error, TypeError)):
        restore_database(live, path)
    assert live.rows('SELECT title FROM notes')[0][0] == 'retain original'
    assert live.db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'


def test_valid_backup_restores_and_preserves_verified_safety_copy(databases):
    live, path = databases
    safety = restore_database(live, path)
    assert validate_backup(safety)['notes'] == 1
    with sqlite3.connect(safety) as db:
        assert db.execute('SELECT title FROM notes').fetchone()[0] == 'retain original'
    assert live.rows('SELECT title FROM notes')[0][0] == 'candidate'


def test_offline_reverse_retries_delete_and_peer_ack_are_convergent(tmp_path):
    workspace = str(uuid4())
    identities = []
    for name in ('a', 'b'):
        original = Store(tmp_path / (name + '-legacy.sqlite3'))
        original.db.close()
        prepared = prepare_identity_copy(original.path, tmp_path / (name + '-sync.sqlite3'))
        with sqlite3.connect(prepared) as db:
            actor = inspect_identities(db)['device_uuid']
        identities.append((prepared, actor))
    (pa, aa), (pb, ab) = identities
    a = P2PStore(pa, workspace_id=workspace, local_workspace_id=1, actor_id=aa, authorized_actors=[ab])
    b = P2PStore(pb, workspace_id=workspace, local_workspace_id=2, actor_id=ab, authorized_actors=[aa])
    try:
        entity = str(uuid4())
        wires = [a.mutate_note(entity, {'title': f'offline {i}', 'body': f'revision {i}'}) for i in range(12)]
        wires.append(a.mutate_note(entity, {'lifecycle': 'deleted'}))
        assert len(a.pending_outbox(ab)) == 13
        for wire in reversed(wires):
            receipt = b.receive_delta(wire, aa)
            assert b.receive_delta(wire, aa) == receipt
            a.acknowledge(ab, receipt)
            a.acknowledge(ab, receipt)
        assert a.get(entity) == b.get(entity)
        assert b.get(entity)['lifecycle'] == 'deleted'
        assert a.pending_outbox(ab) == []
        assert b.db.execute('PRAGMA foreign_key_check').fetchall() == []
        assert b.db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    finally:
        a.close()
        b.close()

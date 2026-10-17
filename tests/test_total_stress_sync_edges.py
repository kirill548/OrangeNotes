"""Independent bounded adversarial sync checks; disposable synthetic data only."""
import json
import random
import sqlite3
from uuid import uuid4

import pytest

from app.database.store import Store
from app.database.sync_identity import prepare_identity_copy, inspect_identities
from app.database.p2p_store import P2PStore
from app.services.p2p_sync import Replica, DeltaError, MAX_ENTITIES, MAX_OPS, MAX_HISTORY_BYTES


def uid():
    return str(uuid4())


def wire(workspace, actor, clock, ops):
    return json.dumps(dict(version=1, workspace_id=workspace, actor_id=actor,
                           clock=clock, ops=ops), ensure_ascii=False)


def test_exact_entity_capacity_in_batches_is_atomic_and_replayable():
    replica = Replica(uid(), uid())
    last = None
    for start in range(0, MAX_ENTITIES, MAX_OPS):
        last = wire(replica.workspace_id, replica.actor_id, replica.clock + 1,
                    [dict(entity_id=uid(), fields={'title': str(n)})
                     for n in range(start, min(start + MAX_OPS, MAX_ENTITIES))])
        assert replica.apply_delta(last)
    assert len(replica._entities) == MAX_ENTITIES
    before = replica.clock, len(replica._dots), replica._history_bytes
    with pytest.raises(DeltaError, match='capacity'):
        replica.make_delta(uid(), {'title': 'one too many'})
    assert before == (replica.clock, len(replica._dots), replica._history_bytes)
    assert replica.apply_delta(last) is False


def test_actual_eight_megabyte_ledger_limit_rejects_without_partial_state():
    replica = Replica(uid(), uid())
    entity = uid()
    accepted = 0
    while True:
        before = replica.clock, replica._history_bytes, replica.get(entity)
        try:
            replica.make_delta(entity, {'body': str(accepted) + 'x' * 120000})
        except DeltaError:
            assert before == (replica.clock, replica._history_bytes, replica.get(entity))
            break
        accepted += 1
        assert accepted < 100
    assert 60 < accepted < 80
    assert MAX_HISTORY_BYTES - replica._history_bytes < 121000
    assert replica._history_bytes <= MAX_HISTORY_BYTES
    assert len(replica.body_conflicts(entity)) <= 15


@pytest.mark.parametrize('seed', range(5))
def test_random_offline_reorder_duplicates_tombstone_convergence(seed):
    workspace, entity = uid(), uid()
    sources = [Replica(workspace, uid()) for _ in range(3)]
    events = []
    for index in range(30):
        source = sources[index % 3]
        fields = {'body': f'{index}: Кириллица 👩🏽‍💻 [[Ссылка]]'}
        if index == 29:
            fields['lifecycle'] = 'deleted'
        events.append(source.make_delta(entity, fields))
    expected = None
    for repetition in range(4):
        target = Replica(workspace, uid())
        delivered = events + events[::3]
        random.Random(seed * 10 + repetition).shuffle(delivered)
        for event in delivered:
            target.apply_delta(event)
        state = target.get(entity), target.body_conflicts(entity)
        if expected is None:
            expected = state
        assert state == expected
        assert target.get(entity)['lifecycle'] == 'deleted'
        assert len(target._dots) == 30


@pytest.fixture
def durable(tmp_path):
    source = tmp_path / 'legacy.db'
    legacy = Store(source)
    legacy.db.close()
    path = prepare_identity_copy(source, tmp_path / 'prepared.db')
    with sqlite3.connect(path) as db:
        actor = inspect_identities(db)['device_uuid']
    peer = uid()
    store = P2PStore(path, workspace_id=uid(), local_workspace_id=1,
                     actor_id=actor, authorized_actors=[peer])
    yield store, peer
    store.close()


@pytest.mark.parametrize('attack', ['unknown', 'workspace', 'impersonation', 'equivocation', 'revoked'])
def test_authenticated_boundary_rejects_without_ledger_or_note_mutation(durable, attack):
    store, peer = durable
    entity = uid()
    good = wire(store.workspace_id, peer, 1, [dict(entity_id=entity, fields={'title': 'original'})])
    store.receive_delta(good, peer)
    bad = json.loads(good)
    caller = peer
    if attack == 'unknown':
        caller = uid()
    elif attack == 'workspace':
        bad['workspace_id'] = uid()
    elif attack == 'impersonation':
        bad['actor_id'] = uid()
    elif attack == 'equivocation':
        bad['ops'][0]['fields']['title'] = 'forged'
    elif attack == 'revoked':
        store.revoke_peer(peer)
    before = tuple(store.db.execute('SELECT count(*) FROM ' + table).fetchone()[0]
                   for table in ('notes', 'sync_events', 'sync_inbox', 'sync_outbox'))
    with pytest.raises(DeltaError):
        store.receive_delta(json.dumps(bad), caller)
    after = tuple(store.db.execute('SELECT count(*) FROM ' + table).fetchone()[0]
                  for table in ('notes', 'sync_events', 'sync_inbox', 'sync_outbox'))
    assert after == before
    assert store.get(entity)['title'] == 'original'
    assert list(store.db.execute('PRAGMA foreign_key_check')) == []

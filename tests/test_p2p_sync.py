import itertools
import json
from uuid import uuid4

import pytest
from app.services.p2p_sync import Replica, DeltaError, MAX_CLOCK, MAX_BODY_VERSIONS


def uid():
    return str(uuid4())


@pytest.fixture
def pair():
    workspace = uid()
    return Replica(workspace, uid()), Replica(workspace, uid()), uid()


def changed(wire, **fields):
    data = json.loads(wire)
    data.update(fields)
    return json.dumps(data)


def test_roundtrip_and_idempotence(pair):
    a, b, note = pair
    wire = a.make_delta(note, {'title': 'Приём', 'body': 'Текст [[Связь]]'})
    assert b.apply_delta(wire)
    assert not b.apply_delta(wire)
    assert a.get(note) == b.get(note)


def test_reordering_converges_fields_and_body_history(pair):
    a, b, note = pair
    events = [a.make_delta(note, {'body': 'A', 'title': 'First'}),
              b.make_delta(note, {'body': 'B'}),
              a.make_delta(note, {'title': 'Second'}),
              b.make_delta(note, {'lifecycle': 'deleted'})]
    expected = None
    for order in itertools.permutations(events):
        replica = Replica(a.workspace_id, uid())
        for event in order:
            replica.apply_delta(event)
        state = (replica.get(note), replica.body_conflicts(note))
        expected = state if expected is None else expected
        assert state == expected


def test_tie_break_is_actor_uuid(pair):
    a, b, note = pair
    wa = a.make_delta(note, {'body': 'A'})
    wb = b.make_delta(note, {'body': 'B'})
    a.apply_delta(wb)
    b.apply_delta(wa)
    assert a.get(note)['body'] == ('A' if a.actor_id > b.actor_id else 'B')
    assert a.get(note) == b.get(note)
    assert len(a.body_conflicts(note)) == 1


def test_edit_does_not_resurrect_and_explicit_restore_does(pair):
    a, b, note = pair
    b.apply_delta(a.make_delta(note, {'lifecycle': 'deleted'}))
    a.apply_delta(b.make_delta(note, {'body': 'Later edit'}))
    assert a.get(note)['lifecycle'] == 'deleted'
    b.apply_delta(a.make_delta(note, {'lifecycle': 'active'}))
    assert b.get(note)['lifecycle'] == 'active'


def test_atomic_rejection_of_late_invalid_op(pair):
    a, b, note = pair
    data = json.loads(a.make_delta(note, {'title': 'Good'}))
    data['ops'].append({'entity_id': uid(), 'fields': {'note_links': 'bad'}})
    with pytest.raises(DeltaError):
        b.apply_delta(json.dumps(data))
    assert b.get(note) is None
    assert b.clock == 0


def test_equivocation_rejected(pair):
    a, b, note = pair
    wire = a.make_delta(note, {'title': 'A'})
    b.apply_delta(wire)
    data = json.loads(wire)
    data['ops'][0]['fields']['title'] = 'B'
    with pytest.raises(DeltaError, match='equivocation'):
        b.apply_delta(json.dumps(data))
    assert b.get(note)['title'] == 'A'


@pytest.mark.parametrize('change', [
    {'version': True}, {'version': 2}, {'clock': True}, {'clock': 0},
    {'clock': -1}, {'clock': MAX_CLOCK + 1}, {'clock': 1.5},
    {'actor_id': False}, {'actor_id': str(uuid4()).upper()},
    {'workspace_id': str(uuid4())}, {'ops': []}, {'ops': [{}]}
])
def test_bad_envelopes_do_not_mutate(pair, change):
    a, b, note = pair
    wire = a.make_delta(note, {'title': 'Valid'})
    with pytest.raises(DeltaError):
        b.apply_delta(changed(wire, **change))
    assert b.get(note) is None
    assert b.clock == 0


@pytest.mark.parametrize('fields', [
    {'title': True}, {'body': None}, {'unknown': 'x'}, {},
    {'lifecycle': 'restore'}, {'title': 'x' * 1025},
    {'body': 'СЏ' * 65537}
])
def test_invalid_fields(pair, fields):
    a, _, note = pair
    with pytest.raises(DeltaError):
        a.make_delta(note, fields)
    assert a.get(note) is None
    assert a.clock == 0


@pytest.mark.parametrize('wire', ['{"version":1,"version":1}', '[]', '{',
                                  '{"clock":NaN}', '[' * 2000, '\ud800'])
def test_malformed_json(wire, pair):
    with pytest.raises(DeltaError):
        pair[0].apply_delta(wire)


def test_unknown_envelope_key(pair):
    a, b, note = pair
    with pytest.raises(DeltaError):
        b.apply_delta(changed(a.make_delta(note, {'title': 'A'}), extra='x'))


def test_duplicate_entity_transaction_rejected(pair):
    a, b, note = pair
    data = json.loads(a.make_delta(note, {'title': 'A'}))
    data['ops'] *= 2
    with pytest.raises(DeltaError):
        b.apply_delta(json.dumps(data))


def test_bounded_conflict_history_converges(pair):
    a, b, note = pair
    events = [a.make_delta(note, {'body': str(index)}) for index in range(30)]
    for event in reversed(events):
        b.apply_delta(event)
    assert a.body_conflicts(note) == b.body_conflicts(note)
    assert len(b.body_conflicts(note)) == MAX_BODY_VERSIONS - 1
    assert b.get(note)['body'] == '29'


def test_query_returns_copy(pair):
    a, _, note = pair
    a.make_delta(note, {'title': 'Original'})
    a.get(note)['title'] = 'Modified'
    assert a.get(note)['title'] == 'Original'


def test_wire_byte_limit(pair):
    with pytest.raises(DeltaError):
        pair[0].apply_delta(' ' * 262145)


def test_multi_note_transaction(pair):
    a, b, note = pair
    other = uid()
    data = json.loads(a.make_delta(note, {'title': 'A'}))
    data['ops'].append({'entity_id': other, 'fields': {'body': 'B'}})
    assert b.apply_delta(json.dumps(data))
    assert b.get(note)['title'] == 'A'
    assert b.get(other)['body'] == 'B'


def test_no_database_or_network_side_effects(monkeypatch):
    import socket
    import sqlite3
    def forbidden(*args, **kwargs):
        raise AssertionError('Unexpected network/database access')
    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(sqlite3, 'connect', forbidden)
    a = Replica(uid(), uid())
    note = uid()
    a.make_delta(note, {'title': 'Offline'})
    assert a.get(note)['title'] == 'Offline'
    assert a.body_conflicts(note) == []


def test_three_actor_random_deliveries_converge(pair):
    import random
    a, b, note = pair
    c = Replica(a.workspace_id, uid())
    events = []
    for index in range(24):
        actor = [a, b, c][index % 3]
        events.append(actor.make_delta(note, {'body': str(index),
                                             'title': 'T' + str(index)}))
    states = []
    for seed in range(12):
        replica = Replica(a.workspace_id, uid())
        delivery = events + events[:8]
        random.Random(seed).shuffle(delivery)
        for wire in delivery:
            replica.apply_delta(wire)
        states.append((replica.get(note), replica.body_conflicts(note)))
    assert all(state == states[0] for state in states)


@pytest.mark.parametrize('limit', ['MAX_DOTS', 'MAX_ENTITIES', 'MAX_HISTORY_BYTES'])
def test_capacity_failure_is_atomic(pair, monkeypatch, limit):
    import app.services.p2p_sync as sync
    a, b, note = pair
    wire = a.make_delta(note, {'body': 'First'})
    b.apply_delta(wire)
    before = (b.get(note), b.body_conflicts(note), b.clock)
    monkeypatch.setattr(sync, limit, 1)
    data = json.loads(wire)
    data['clock'] = 2
    data['ops'][0]['entity_id'] = uid()
    with pytest.raises(DeltaError, match='capacity'):
        b.apply_delta(json.dumps(data))
    assert (b.get(note), b.body_conflicts(note), b.clock) == before
    assert not b.apply_delta(wire)


def test_operation_count_bound(pair):
    a, b, note = pair
    data = json.loads(a.make_delta(note, {'title': 'A'}))
    data['ops'] = [{'entity_id': uid(), 'fields': {'title': 'A'}} for _ in range(33)]
    with pytest.raises(DeltaError):
        b.apply_delta(json.dumps(data))
    assert b.clock == 0


def test_huge_integer_json_fails_as_domain_error(pair):
    with pytest.raises(DeltaError):
        pair[0].apply_delta('{"clock":' + '9' * 5000 + '}')

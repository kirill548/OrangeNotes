"""Bounded, in-memory sync experiment; no networking or database writes.

Whole-field Lamport LWW registers are not collaborative text editing. Callers
must authenticate actors and authorize workspace membership before applying data.
"""
from __future__ import annotations

import copy
import json
from uuid import UUID

MAX_BYTES = 262144
MAX_OPS = 32
MAX_CLOCK = 2**53 - 1
MAX_DOTS = 8192
MAX_ENTITIES = 4096
MAX_HISTORY_BYTES = 8 * 1024 * 1024
MAX_BODY_VERSIONS = 16
_FIELDS = {'title': 1024, 'body': 131072, 'lifecycle': 7}


class DeltaError(ValueError):
    """Malformed, out-of-scope or conflicting delta; state stays unchanged."""


def _uuid(value):
    if not isinstance(value, str):
        raise DeltaError('Expected canonical UUIDv4')
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError) as exc:
        raise DeltaError('Expected canonical UUIDv4') from exc
    if parsed.version != 4 or str(parsed) != value:
        raise DeltaError('Expected canonical UUIDv4')
    return value


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise DeltaError('Duplicate JSON field')
        result[key] = value
    return result


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


class Replica:
    """One authorized workspace; explicit lifecycle restore uses ``active``.

    Limits fail closed and require a future authenticated snapshot/compaction
    protocol. Conflict history retains the greatest 16 distinct body registers,
    not every edit. There is no automatic persistence or integration with SQLite.
    """

    def __init__(self, workspace_id: str, actor_id: str):
        self.workspace_id = _uuid(workspace_id)
        self.actor_id = _uuid(actor_id)
        self.clock = 0
        self._dots = {}
        self._history_bytes = 0
        self._entities = {}
        self._bodies = {}

    def make_delta(self, entity_id: str, fields: dict) -> str:
        """Create and locally apply a delta; return its canonical wire JSON."""
        envelope = {'version': 1, 'workspace_id': self.workspace_id,
                    'actor_id': self.actor_id, 'clock': self.clock + 1,
                    'ops': [{'entity_id': entity_id, 'fields': fields}]}
        try:
            wire = _canonical(envelope)
        except (ValueError, TypeError, RecursionError) as exc:
            raise DeltaError('Cannot encode delta') from exc
        self.apply_delta(wire)
        return wire

    def apply_delta(self, wire: str) -> bool:
        """Atomically validate/apply strict version-1 JSON; duplicates are no-op.

        The returned flag means a previously unseen delta was accepted, including
        older deltas which do not change the currently visible register values.
        """
        try:
            if not isinstance(wire, str) or len(wire.encode('utf-8')) > MAX_BYTES:
                raise DeltaError('Delta exceeds byte limit')
            data = json.loads(wire, object_pairs_hook=_object,
                              parse_constant=lambda _: (_ for _ in ()).throw(DeltaError('Non-finite JSON')))
            expected = {'version', 'workspace_id', 'actor_id', 'clock', 'ops'}
            if not isinstance(data, dict) or set(data) != expected:
                raise DeltaError('Invalid envelope fields')
            if type(data['version']) is not int or data['version'] != 1:
                raise DeltaError('Unsupported protocol version')
            if _uuid(data['workspace_id']) != self.workspace_id:
                raise DeltaError('Workspace mismatch')
            actor = _uuid(data['actor_id'])
            clock = data['clock']
            if type(clock) is not int or not 1 <= clock <= MAX_CLOCK:
                raise DeltaError('Invalid Lamport clock')
            ops = data['ops']
            if not isinstance(ops, list) or not 1 <= len(ops) <= MAX_OPS:
                raise DeltaError('Invalid operation count')
            seen = set()
            for op in ops:
                if not isinstance(op, dict) or set(op) != {'entity_id', 'fields'}:
                    raise DeltaError('Invalid operation fields')
                entity = _uuid(op['entity_id'])
                if entity in seen:
                    raise DeltaError('Repeated entity operation')
                seen.add(entity)
                fields = op['fields']
                if not isinstance(fields, dict) or not fields or not set(fields) <= set(_FIELDS):
                    raise DeltaError('Unknown or empty note fields')
                for key, value in fields.items():
                    if not isinstance(value, str) or len(value.encode('utf-8')) > _FIELDS[key]:
                        raise DeltaError('Invalid field value')
                    if key == 'lifecycle' and value not in {'active', 'deleted'}:
                        raise DeltaError('Invalid lifecycle')
            canonical = _canonical(data)
        except (UnicodeError, RecursionError, ValueError) as exc:
            raise DeltaError('Invalid JSON encoding') from exc
        dot = (actor, clock)
        if dot in self._dots:
            if self._dots[dot] != canonical:
                raise DeltaError('Actor clock equivocation')
            return False
        canonical_bytes = len(canonical.encode('utf-8'))
        if (len(self._dots) >= MAX_DOTS or len(set(self._entities) | seen) > MAX_ENTITIES
                or self._history_bytes + canonical_bytes > MAX_HISTORY_BYTES):
            raise DeltaError('Replica capacity reached; snapshot required')
        entities = copy.deepcopy(self._entities)
        bodies = copy.deepcopy(self._bodies)
        stamp = (clock, actor)
        for op in ops:
            entity = op['entity_id']
            registers = entities.setdefault(entity, {})
            for key, value in op['fields'].items():
                if key == 'body':
                    versions = bodies.setdefault(entity, {})
                    versions[stamp] = value
                    bodies[entity] = dict(sorted(versions.items(), reverse=True)[:MAX_BODY_VERSIONS])
                if key not in registers or stamp > registers[key][0]:
                    registers[key] = (stamp, value)
        self._entities = entities
        self._bodies = bodies
        self._dots[dot] = canonical
        self._history_bytes += canonical_bytes
        self.clock = max(self.clock, clock)
        return True

    def get(self, entity_id: str) -> dict | None:
        """Copy of visible fields, including deleted entities for conflict UI."""
        entity_id = _uuid(entity_id)
        if entity_id not in self._entities:
            return None
        result = {key: value for key, (_, value) in self._entities[entity_id].items()}
        result.setdefault('lifecycle', 'active')
        return result

    def body_conflicts(self, entity_id: str) -> list[dict]:
        """Bounded losing body candidates (may include sequential old edits)."""
        entity_id = _uuid(entity_id)
        versions = sorted(self._bodies.get(entity_id, {}).items(), reverse=True)
        return [{'clock': stamp[0], 'actor_id': stamp[1], 'body': value}
                for stamp, value in versions[1:]]

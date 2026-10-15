"""Generate/check Dart fixtures against the desktop CRDT implementation.

Run from the repository root. --check never rewrites expected results.
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.services.p2p_sync import Replica, DeltaError

WORKSPACE = '10000000-0000-4000-8000-000000000001'
A = '20000000-0000-4000-8000-000000000001'
B = '20000000-0000-4000-8000-000000000002'
RECEIVER = '20000000-0000-4000-8000-000000000003'
ENTITY = '30000000-0000-4000-8000-000000000001'
TARGET = ROOT / 'orange_notes_mobile/test/fixtures/protocol_v1.json'


def wire(actor, clock, fields):
    return json.dumps({'version': 1, 'workspace_id': WORKSPACE,
                      'actor_id': actor, 'clock': clock,
                      'ops': [{'entity_id': ENTITY, 'fields': fields}]},
                     ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def generate():
    base = wire(A, 1, {'title': 'Встреча 🐝', 'body': '<p>План [[Отчёт|на завтра]]</p>', 'lifecycle': 'active'})
    edit_a = wire(A, 2, {'body': 'Версия А'})
    edit_b = wire(B, 2, {'body': 'Версия Б'})
    delete = wire(A, 3, {'lifecycle': 'deleted'})
    late_body = wire(B, 4, {'body': 'Правка после удаления'})
    cases = [
        ('unicode_html_roundtrip', [base]),
        ('actor_tie_forward', [base, edit_a, edit_b]),
        ('actor_tie_reverse', [edit_b, base, edit_a]),
        ('deleted_body_does_not_restore', [base, delete, late_body]),
        ('explicit_restore', [base, delete, late_body, wire(B, 5, {'lifecycle': 'active'})]),
        ('duplicate_is_noop', [base, base]),
        ('equivocation_is_atomic', [base, wire(A, 1, {'title': 'Подмена'})]),
        ('unknown_field_is_atomic', [base, wire(B, 2, {'body': 'Нельзя применять', 'unknown': 'x'})]),
        ('foreign_workspace_is_atomic', [base, wire(B, 2, {'title': 'Чужая'}).replace(WORKSPACE, '10000000-0000-4000-8000-000000000002')]),
        ('duplicate_json_key_rejected', [base, base.replace('"clock":1', '"clock":1,"clock":2')]),
    ]
    output = []
    for name, wires in cases:
        replica = Replica(WORKSPACE, RECEIVER)
        accepted = []
        for value in wires:
            try:
                accepted.append(replica.apply_delta(value))
            except DeltaError:
                accepted.append('error')
        output.append({'name': name, 'wires': wires, 'accepted': accepted,
                       'expected': replica.get(ENTITY),
                       'body_conflicts': replica.body_conflicts(ENTITY)})
    return {'protocol_version': 1, 'workspace_id': WORKSPACE,
            'actors': {'a': A, 'b': B, 'receiver': RECEIVER},
            'entity_id': ENTITY, 'cases': output}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    expected = generate()
    if args.check:
        actual = json.loads(TARGET.read_text(encoding='utf-8'))
        if actual != expected:
            raise SystemExit('Mobile CRDT fixtures diverge from desktop implementation')
        print(f'Desktop/mobile protocol fixtures: {len(expected["cases"])} PASS')
    else:
        TARGET.parent.mkdir(parents=True, exist_ok=True)
        TARGET.write_text(json.dumps(expected, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(f'Generated {len(expected["cases"])} desktop protocol fixtures')


if __name__ == '__main__':
    main()

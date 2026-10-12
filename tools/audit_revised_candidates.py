"""Summarize existing live gate evidence; never synthesize model results."""
import argparse
import hashlib
import json
from pathlib import Path


def audit(folder):
    rows = []
    for key in ('coder', 'llama', 'phi'):
        path = folder / (key + '-quality.json')
        if not path.exists():
            rows.append({'candidate': key, 'status': 'not_run', 'approved': False})
            continue
        report = json.loads(path.read_text(encoding='utf-8'))
        cases = report.get('cases', [])
        names = [case.get('name') for case in cases]
        canonical = [case for case in cases if str(case.get('name')).startswith('canonical-refusal-')]
        holdout = [case for case in cases if str(case.get('name')).startswith('holdout-refusal-')]
        pipeline = [case for case in cases if case not in canonical and case not in holdout]
        integrity = (len(cases) == 21 and len(set(names)) == 21
                     and len(canonical) == 10 and len(holdout) == 5 and len(pipeline) == 6
                     and all(type(case.get('passed')) is bool for case in cases))
        passed = sum(case.get('passed') is True for case in cases)
        rows.append({'candidate': key, 'status': 'complete' if integrity else 'incomplete',
                     'approved_quality': integrity and passed == 21,
                     'complete_edition_approved': False,
                     'total': len(cases), 'passed': passed,
                     'canonical_passed': sum(case.get('passed') is True for case in canonical),
                     'holdout_passed': sum(case.get('passed') is True for case in holdout),
                     'pipeline_passed': sum(case.get('passed') is True for case in pipeline),
                     'prompt_sha256': report.get('system_prompt_sha256'),
                     'evidence_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                     'evidence': str(path.resolve())})
    return {'scope': 'Actual saved model Quality Gates only; performance and release manifests are separate gates.',
            'candidates': rows,
            'quality_candidates': [row['candidate'] for row in rows if row.get('approved_quality')],
            'complete_edition_approved': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder', type=Path, default=Path('work/v013-revised'))
    args = parser.parse_args()
    result = audit(args.folder)
    output = args.folder / 'quality-audit.json'
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))

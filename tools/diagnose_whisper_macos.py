"""Read-only native macOS build prerequisites; does not install or compile Whisper."""
import argparse
import json
import platform
from pathlib import Path
import shutil
import subprocess
import sys


def diagnose():
    result = {'platform': sys.platform, 'architecture': platform.machine(),
              'native_macos': sys.platform == 'darwin', 'ready_to_build': False,
              'checks': {}, 'compiled': False}
    if sys.platform != 'darwin':
        result['reason'] = 'Run this diagnostic on a real macOS host; platform mocks do not compile native binaries.'
        return result
    checks = result['checks']
    checks['architecture_supported'] = platform.machine().lower() in ('arm64', 'aarch64', 'x86_64', 'amd64')
    for name, command in (
        ('cmake', ['cmake', '--version']),
        ('compiler', ['xcrun', '--find', 'clang++']),
        ('sdk', ['xcrun', '--sdk', 'macosx', '--show-sdk-path']),
    ):
        if not shutil.which(command[0]):
            checks[name] = {'available': False, 'reason': 'Executable missing'}
            continue
        try:
            completed = subprocess.run(command, capture_output=True, text=True,
                                       timeout=10, check=False)
            checks[name] = {'available': completed.returncode == 0,
                            'detail': (completed.stdout or completed.stderr).strip()[:1000]}
        except (OSError, subprocess.SubprocessError) as exc:
            checks[name] = {'available': False, 'reason': type(exc).__name__}
    result['ready_to_build'] = checks['architecture_supported'] and all(
        checks[name]['available'] for name in ('cmake', 'compiler', 'sdk'))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    report = diagnose()
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + '\n', encoding='utf-8')
    print(rendered)
    return 0 if report['ready_to_build'] else 1


if __name__ == '__main__':
    raise SystemExit(main())

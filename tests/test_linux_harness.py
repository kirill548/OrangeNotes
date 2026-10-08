"""Execute the actual harness with failing disposable daemon/session adapters."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
GIT_BASH = Path('C:/Program Files/Git/bin/bash.exe')
BASH = str(GIT_BASH) if os.name == 'nt' and GIT_BASH.exists() else shutil.which('bash')


@unittest.skipUnless(BASH, 'Bash is required for native harness failure tests')
class LinuxHarnessTests(unittest.TestCase):
    def run_harness(self, session, daemon):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            scripts = {
                'dbus-run-session': session,
                'xvfb-run': 'shift; exec "$@"',
                'dunst': daemon,
                'gdbus': 'exit 1',
                'notify-send': 'exit 0',
                'sleep': 'exit 0',
            }
            for name, content in scripts.items():
                path = directory / name
                path.write_text('#!/usr/bin/env bash\n' + content + '\n', newline='\n')
                path.chmod(0o755)
            # Git Bash translates this Windows PATH when launching POSIX children.
            env = dict(os.environ, PATH=str(directory) + os.pathsep + os.environ['PATH'])
            result = subprocess.run([BASH, str(ROOT / 'tools/test_linux_notifications.sh')],
                                    env=env, capture_output=True, text=True, timeout=15)
            return result

    def test_missing_session_fails_closed(self):
        result = self.run_harness('shift; unset DBUS_SESSION_BUS_ADDRESS; exec "$@"', 'exit 1')
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn('D-Bus session was not created', result.stderr)

    def test_unavailable_daemon_fails_closed(self):
        result = self.run_harness('shift; export DBUS_SESSION_BUS_ADDRESS=disposable; exec "$@"', 'exit 1')
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertTrue('Dunst exited before readiness' in result.stderr or
                        'readiness timed out' in result.stderr, result.stderr)

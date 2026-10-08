"""Start a non-Windows frozen GUI with an isolated empty database.

This checks startup only; native notification permissions and OS login must be
tested on a real desktop before declaring that platform a supported release.
"""
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time


def main(executable):
    executable = Path(executable).resolve()
    if not executable.is_file():
        raise FileNotFoundError(executable)
    with tempfile.TemporaryDirectory(prefix='orange-notes-ci-') as temporary:
        database = Path(temporary) / 'smoke.sqlite3'
        env = os.environ.copy()
        env.pop('PYTHONPATH', None)
        env.pop('PYTHONHOME', None)
        with (Path(temporary) / 'startup.log').open('w+') as log:
            process = subprocess.Popen([str(executable), '--database', str(database)],
                                       cwd=temporary, env=env, stdout=log, stderr=log)
            try:
                deadline = time.monotonic() + 30
                while not database.exists() and process.poll() is None and time.monotonic() < deadline:
                    time.sleep(.2)
                time.sleep(3)
                if process.poll() is not None or not database.is_file():
                    log.seek(0)
                    raise RuntimeError('Frozen GUI failed to start: ' + log.read())
                with sqlite3.connect(database) as connection:
                    assert connection.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
                    assert connection.execute("SELECT name FROM sqlite_master WHERE name='notes'").fetchone()
                print('Frozen GUI remained running; isolated SQLite schema and integrity verified.')
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)


if __name__ == '__main__':
    main(sys.argv[1])

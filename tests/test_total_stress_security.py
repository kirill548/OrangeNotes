"""Bounded adversarial tests using disposable synthetic databases only."""
from pathlib import Path
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.database.store import Store, plain_body


@pytest.mark.parametrize('phase,expected', [('uncommitted', 1), ('committed', 201), ('writing', 1)])
def test_real_process_termination_is_transaction_atomic(tmp_path, phase, expected):
    path = tmp_path / 'synthetic-crash.sqlite3'
    script = Path(__file__).resolve().parents[1] / 'tools' / 'total_stress_crash.py'
    child = subprocess.Popen([sys.executable, str(script), '--database', str(path), '--phase', phase],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            ready = pool.submit(child.stdout.readline)
            try:
                assert ready.result(timeout=30).strip() == 'READY'
            finally:
                child.kill()
                child.wait(timeout=10)
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=10)
    recovered = Store(path)
    try:
        assert recovered.db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert recovered.db.execute('PRAGMA foreign_key_check').fetchall() == []
        assert recovered.db.execute('SELECT count(*) FROM crash_probe').fetchone()[0] == expected
        fresh = recovered.create_note()
        recovered.save_note(fresh, 'recovered', '<p>Still writable</p>', None, False, '')
        assert recovered.rows('SELECT title FROM notes WHERE id=?', (fresh,))[0]['title'] == 'recovered'
    finally:
        recovered.db.close()


@pytest.mark.parametrize('payload', ["'); DROP TABLE notes; --", '<script>alert(1)</script>',
                                     '<img src=x onerror="alert(1)">', '😀 кириллица \\ "'])
def test_note_payload_is_bound_as_data(tmp_path, payload):
    store = Store(tmp_path / 'synthetic.sqlite3')
    try:
        note = store.create_note()
        store.save_note(note, payload, payload, None, False, 'safe')
        saved = store.rows('SELECT title,body FROM notes WHERE id=?', (note,))[0]
        assert saved['title'] == payload and saved['body'] == payload
        assert store.db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    finally:
        store.db.close()


@pytest.mark.parametrize('invalid', ['bad\ud800', 'x\x00y', b'\xff\xfe', None, 42])
def test_invalid_unicode_does_not_partially_mutate_note(tmp_path, invalid):
    store = Store(tmp_path / 'synthetic.sqlite3')
    try:
        note = store.create_note()
        store.save_note(note, 'baseline', '<p>baseline</p>', None, False, 'tag')
        with pytest.raises(ValueError):
            store.save_note(note, 'changed', invalid, None, False, 'other')
        assert store.rows('SELECT title,body FROM notes WHERE id=?', (note,))[0]['title'] == 'baseline'
        assert store.rows('SELECT title,body FROM notes WHERE id=?', (note,))[0]['body'] == '<p>baseline</p>'
        assert not store.db.in_transaction
        assert store.db.execute('PRAGMA foreign_key_check').fetchall() == []
    finally:
        store.db.close()


def test_collection_table_sql_injection_rejected(tmp_path):
    store = Store(tmp_path / 'synthetic.sqlite3')
    try:
        with pytest.raises(ValueError):
            store.rename_collection('folders; DROP TABLE notes;--', 1, 'x')
        assert store.rows('SELECT count(*) AS n FROM notes')[0]['n'] == 0
    finally:
        store.db.close()


def test_plain_context_excludes_executable_markup():
    assert plain_body('<p>safe</p><script>alert(1)</script><style>bad</style><p>end</p>') == 'safe\nend'


def test_sqlite_plaintext_storage_is_explicitly_documented(tmp_path):
    """Evidence of storage properties, not an assertion of encryption."""
    path = tmp_path / 'synthetic.sqlite3'
    store = Store(path)
    note = store.create_note()
    marker = 'SYNTHETIC-NOT-A-REAL-SECRET-7913'
    store.save_note(note, marker, marker, None, False, '')
    store.db.close()
    assert path.read_bytes().startswith(b'SQLite format 3\x00')
    assert marker.encode() in path.read_bytes()

"""Persistence regressions; every database is disposable and isolated."""
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.database.store import Store


class PersistenceEdges(unittest.TestCase):
    def setUp(self):
        scratch = Path(__file__).resolve().parents[3] / 'work'
        scratch.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='persistence-tests-', dir=scratch)
        self.root = Path(self.temp.name)
        self.path = self.root / 'notes.sqlite3'
        self.stores = []

    def tearDown(self):
        for store in self.stores:
            store.db.close()
        self.temp.cleanup()

    def open_store(self, path=None):
        store = Store(path or self.path)
        self.stores.append(store)
        return store

    def populate(self, store):
        folder = store.execute('INSERT INTO folders(name) VALUES (?)', ('Работа 🧡',)).lastrowid
        note = store.create_note(folder, favorite=True)
        store.save_note(note, 'Привет 中文 é', '<p>Строка 🧡 &amp; Unicode</p>', folder, True, '重要, Финансы 🧡')
        store.set_note_icon(note, 'phone')
        store.save_reminder(note, 'repeat', None, [0, 2, 4], ['09:00', '18:30'])
        return note

    def snapshot(self, store):
        return {table: [tuple(row) for row in store.rows('SELECT * FROM ' + table + ' ORDER BY rowid')]
                for table in ('folders', 'notes', 'tags', 'note_tags', 'reminders', 'reminder_days', 'reminder_times', 'reminder_events')}

    def test_legacy_icon_migration_preserves_rows_and_is_idempotent(self):
        old = sqlite3.connect(self.path)
        old.execute("CREATE TABLE notes(id INTEGER PRIMARY KEY,title TEXT NOT NULL DEFAULT '',body TEXT NOT NULL DEFAULT '',folder_id INTEGER,favorite INTEGER NOT NULL DEFAULT 0,archived INTEGER NOT NULL DEFAULT 0,deleted INTEGER NOT NULL DEFAULT 0,updated_at TEXT NOT NULL)")
        old.execute('INSERT INTO notes VALUES (?,?,?,?,?,?,?,?)', (42, '旧 🧡', '<p>текст</p>', None, 1, 0, 0, '2026-10-02T12:00:00'))
        old.commit(); old.close()
        migrated = self.open_store()
        row = dict(migrated.rows('SELECT * FROM notes WHERE id=42')[0])
        self.assertEqual(row['title'], '旧 🧡')
        self.assertEqual(row['body'], '<p>текст</p>')
        self.assertEqual(row['favorite'], 1)
        self.assertIsNone(row['icon_name'])
        migrated.set_note_icon(42, 'book')
        expected = self.snapshot(migrated)
        migrated.db.close()
        again = self.open_store()
        self.assertEqual(self.snapshot(again), expected)
        self.assertEqual(sum(row['name'] == 'icon_name' for row in again.rows('PRAGMA table_info(notes)')), 1)

    def test_hundred_reopens_unicode_icon_tags_and_schedule(self):
        store = self.open_store()
        nid = self.populate(store)
        expected = self.snapshot(store)
        store.db.close()
        for iteration in range(100):
            reopened = Store(self.path)
            try:
                self.assertEqual(self.snapshot(reopened), expected, iteration)
                self.assertEqual(reopened.reminder(nid)['times'], ['09:00', '18:30'])
                self.assertEqual(reopened.rows('PRAGMA integrity_check')[0][0], 'ok')
            finally:
                reopened.db.close()

    def test_backup_reopens_complete_independent_snapshot(self):
        store = self.open_store()
        nid = self.populate(store)
        expected = self.snapshot(store)
        destination = self.root / 'export' / 'snapshot.sqlite3'
        self.assertEqual(store.backup_to(destination), destination.resolve())
        store.save_note(nid, 'Changed after backup', 'new', None, False, '')
        backup = self.open_store(destination)
        self.assertEqual(self.snapshot(backup), expected)
        self.assertFalse(backup.rows('PRAGMA foreign_key_check'))
        self.assertEqual(backup.rows('PRAGMA integrity_check')[0][0], 'ok')

    def test_backup_failure_keeps_previous_file_and_removes_temporary(self):
        store = self.open_store()
        self.populate(store)
        destination = self.root / 'existing.sqlite3'
        prior = b'previous user chosen backup'
        destination.write_bytes(prior)
        with patch('app.database.store.os.replace', side_effect=PermissionError('simulated locked destination')):
            with self.assertRaises(PermissionError):
                store.backup_to(destination)
        self.assertEqual(destination.read_bytes(), prior)
        self.assertFalse(list(self.root.glob('.notes-backup-*')))
        self.assertEqual(store.rows('PRAGMA integrity_check')[0][0], 'ok')
        with patch('app.database.store.sqlite3.connect', side_effect=sqlite3.OperationalError('simulated disk full')):
            with self.assertRaises(sqlite3.OperationalError):
                store.backup_to(destination)
        self.assertEqual(destination.read_bytes(), prior)
        self.assertFalse(list(self.root.glob('.notes-backup-*')))

    def test_backup_samefile_and_uncommitted_transaction_rejected(self):
        store = self.open_store()
        self.populate(store)
        before = self.snapshot(store)
        with self.assertRaises(ValueError):
            store.backup_to(self.root / '.' / 'notes.sqlite3')
        store.db.execute('BEGIN')
        try:
            store.db.execute("UPDATE notes SET title='not committed'")
            with self.assertRaises(ValueError):
                store.backup_to(self.root / 'blocked.sqlite3')
            self.assertFalse((self.root / 'blocked.sqlite3').exists())
        finally:
            store.db.rollback()
        self.assertEqual(self.snapshot(store), before)

    def test_readonly_writes_preserve_snapshot_and_recover(self):
        store = self.open_store()
        nid = self.populate(store)
        before = self.snapshot(store)
        store.db.execute('PRAGMA query_only=ON')
        for callback in (lambda: store.set_note_icon(nid, 'gift'),
                         lambda: store.save_note(nid, 'lost?', 'changed', None, False, ''),
                         lambda: store.save_reminder(nid, 'once', '2027-01-01T09:00:00', [], [])):
            with self.assertRaises(sqlite3.OperationalError):
                callback()
            self.assertFalse(store.db.in_transaction)
            self.assertEqual(self.snapshot(store), before)
        store.db.execute('PRAGMA query_only=OFF')
        store.set_note_icon(nid, 'gift')
        self.assertEqual(store.rows('SELECT icon_name FROM notes WHERE id=?', (nid,))[0][0], 'gift')

    def test_initialization_directory_permission_error_is_nondestructive(self):
        with patch('app.database.store.Path.mkdir', side_effect=PermissionError('simulated denied directory')):
            with self.assertRaises(PermissionError):
                Store(self.path)
        self.assertFalse(self.path.exists())

    def test_terminated_process_preserves_commit_and_rolls_back_open_transaction(self):
        store = self.open_store()
        nid = self.populate(store)
        before = self.snapshot(store)
        store.db.close()
        ready = self.root / 'ready'
        source = """
import sys,time
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from app.database.store import Store
store=Store(sys.argv[2])
store.save_note(int(sys.argv[3]),'committed 🧡','durable',None,True,'crash-test')
store.db.execute('BEGIN IMMEDIATE')
store.db.execute("UPDATE notes SET title='uncommitted lost update'")
store.db.execute("DELETE FROM reminder_times")
Path(sys.argv[4]).write_text('ready')
time.sleep(60)
"""
        child = subprocess.Popen([sys.executable, '-c', source, str(Path(__file__).resolve().parents[1]), str(self.path), str(nid), str(ready)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 10
            while not ready.exists() and child.poll() is None and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(ready.exists(), 'child failed before open transaction: ' + (child.stderr.read().decode(errors='replace') if child.poll() is not None else 'timeout'))
            child.terminate()
            child.communicate(timeout=10)
            self.assertNotEqual(child.returncode, 0)
        finally:
            if child.poll() is None:
                child.kill(); child.communicate(timeout=10)
        reopened = self.open_store()
        row = reopened.rows('SELECT * FROM notes WHERE id=?', (nid,))[0]
        self.assertEqual(row['title'], 'committed 🧡')
        self.assertEqual(row['body'], 'durable')
        self.assertEqual(row['icon_name'], 'phone')
        self.assertEqual(self.snapshot(reopened)['reminder_times'], before['reminder_times'])
        self.assertEqual(reopened.rows('PRAGMA integrity_check')[0][0], 'ok')
        self.assertFalse(reopened.rows('PRAGMA foreign_key_check'))
        backup = self.root / 'after-crash.sqlite3'
        reopened.backup_to(backup)
        self.assertEqual(self.snapshot(self.open_store(backup)), self.snapshot(reopened))


if __name__ == '__main__':
    unittest.main(verbosity=2)

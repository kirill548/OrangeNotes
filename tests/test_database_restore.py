import sqlite3
from contextlib import closing
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.database.store import Store
from app.services.database_restore import restore_database,validate_backup


class RestoreTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.current=Store(self.root/'current.sqlite3')
        self.old=self.current.create_note();self.current.save_note(self.old,'Current','keep',None,False,'')
        other=Store(self.root/'backup.sqlite3')
        note=other.create_note();other.save_note(note,'Backup','restored',None,False,'')
        other.db.close();self.backup=self.root/'backup.sqlite3'

    def tearDown(self):
        self.current.db.close();self.temp.cleanup()

    def test_restore_safety_copy_and_connection_identity(self):
        identity=id(self.current)
        safety=restore_database(self.current,self.backup)
        self.assertEqual(id(self.current),identity)
        self.assertEqual(self.current.rows('SELECT title FROM notes')[0][0],'Backup')
        with closing(sqlite3.connect(safety)) as db:self.assertEqual(db.execute('SELECT title FROM notes').fetchone()[0],'Current')
        self.assertEqual(self.current.db.execute('PRAGMA integrity_check').fetchone()[0],'ok')

    def test_invalid_and_same_file_preserve_current(self):
        bad=self.root/'bad.sqlite3';bad.write_bytes(b'broken')
        for file in (bad,self.current.path):
            with self.assertRaises((sqlite3.Error,ValueError)):restore_database(self.current,file)
        self.assertEqual(self.current.rows('SELECT title FROM notes')[0][0],'Current')

    def test_wrong_schema_rejected(self):
        wrong=self.root/'wrong.sqlite3'
        with closing(sqlite3.connect(wrong)) as db:db.execute('CREATE TABLE unrelated(id)')
        with self.assertRaises(ValueError):validate_backup(wrong)

    def test_replace_failure_reopens_original(self):
        with patch('app.services.database_restore.os.replace',side_effect=PermissionError('locked')):
            with self.assertRaises(PermissionError):restore_database(self.current,self.backup)
        self.assertEqual(self.current.rows('SELECT title FROM notes')[0][0],'Current')

    def test_failed_reopen_rolls_back(self):
        real=Store
        calls=[]
        def opening(path):
            calls.append(Path(path))
            if len(calls)==2:raise RuntimeError('migration failed')
            return real(path)
        with patch('app.services.database_restore.Store',side_effect=opening):
            with self.assertRaises(RuntimeError):restore_database(self.current,self.backup)
        self.assertEqual(self.current.rows('SELECT title FROM notes')[0][0],'Current')
        self.assertEqual(len(list(self.root.glob('notes-before-restore-*.sqlite3'))),1)

    def test_pending_transaction_refused(self):
        self.current.db.execute('BEGIN')
        with self.assertRaises(ValueError):restore_database(self.current,self.backup)
        self.current.db.rollback()

    def test_active_database_trigger_rejected(self):
        with closing(sqlite3.connect(self.backup)) as db:db.execute('CREATE TRIGGER strange AFTER INSERT ON notes BEGIN SELECT 1; END')
        with self.assertRaises(ValueError):validate_backup(self.backup)

    def test_foreign_key_corruption_refused(self):
        with closing(sqlite3.connect(self.backup)) as db:
            db.execute('UPDATE notes SET folder_id=999999');db.commit()
        with self.assertRaises(ValueError):restore_database(self.current,self.backup)
        self.assertEqual(self.current.rows('SELECT title FROM notes')[0][0],'Current')

    def test_open_reader_prevents_replacing_wal(self):
        self.current.db.execute('PRAGMA journal_mode=WAL')
        self.current.db.execute('PRAGMA busy_timeout=10')
        with closing(sqlite3.connect(self.current.path)) as reader:
            reader.execute('BEGIN');reader.execute('SELECT * FROM notes').fetchall()
            self.current.save_note(self.old,'New current','updated',None,False,'')
            with self.assertRaises(ValueError):restore_database(self.current,self.backup)
        self.assertEqual(self.current.rows('SELECT title FROM notes')[0][0],'New current')


"""Portable runtime contract, not evidence of execution on a different OS.

Run on each release interpreter: SQLite version alone does not establish FTS5
availability. The no-module branch is fault injection on the current engine.
"""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.database.store import Store
from app.services.memory_store import MemoryStore


def unicode61_available():
    with sqlite3.connect(':memory:') as db:
        try:
            db.execute('CREATE VIRTUAL TABLE probe USING fts5(text, tokenize="unicode61")')
        except sqlite3.OperationalError as error:
            if 'no such module: fts5' in str(error).lower():
                return False
            raise
        return True


class MemoryFTSPlatformTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'notes.db')
        self.addCleanup(self.store.db.close)

    def note(self, title, body, workspace=1):
        identity = self.store.create_note()
        self.store.save_note(identity, title, body, None, False, '')
        self.store.execute('UPDATE notes SET workspace_id=? WHERE id=?', (workspace, identity))
        return identity

    def memory(self, missing_module=False):
        connect = sqlite3.connect

        class MissingFTS(sqlite3.Connection):
            def execute(self, sql, parameters=()):
                if 'CREATE VIRTUAL TABLE' in sql:
                    raise sqlite3.OperationalError('no such module: fts5')
                return super().execute(sql, parameters)

        if missing_module:
            with patch('app.database.store.sqlite3.connect',
                       side_effect=lambda *args, **kwargs: connect(*args, factory=MissingFTS, **kwargs)):
                result = MemoryStore(self.store)
        else:
            result = MemoryStore(self.store)
        self.addCleanup(result.close)
        return result

    def test_real_runtime_version_and_required_sql_capabilities(self):
        # Exercise required syntax instead of pretending a patched version string
        # represents an older SQLite library.
        self.assertEqual(self.store.db.execute('SELECT sqlite_version()').fetchone()[0], sqlite3.sqlite_version)
        self.assertEqual(self.store.db.execute('SELECT (1,2) IN (SELECT 1,2)').fetchone()[0], 1)
        self.assertEqual(self.store.db.execute('PRAGMA journal_mode').fetchone()[0], 'wal')
        memory = self.memory()
        self.assertEqual(memory.fts_available, unicode61_available())
        self.assertEqual(memory.db.execute("SELECT orange_search_normalize(?)", ('ЁЛКИ Straße',)).fetchone()[0], 'елки strasse')

    def test_real_unicode61_cyrillic_case_and_diacritics(self):
        if not unicode61_available():
            self.skipTest('This actual SQLite build has no FTS5; fallback tests still run')
        with sqlite3.connect(':memory:') as db:
            db.execute('CREATE VIRTUAL TABLE probe USING fts5(text, tokenize="unicode61")')
            db.execute('INSERT INTO probe VALUES (?)', ('ЁЛКА Café A-102',))
            for query in ('"ёлка"', '"cafe"', '"a" AND "102"'):
                self.assertEqual(db.execute('SELECT count(*) FROM probe WHERE probe MATCH ?', (query,)).fetchone()[0], 1)

    def test_missing_module_fallback_unicode_scope_and_literal_identifiers(self):
        identity = self.note('Ёлки Straße', 'Реклама автомобиля A-102')
        self.note('Ёлки Straße', 'Реклама автомобиля A-102', workspace=2)
        memory = self.memory(missing_module=True)
        self.assertFalse(memory.fts_available)
        statements = []
        memory.db.set_trace_callback(statements.append)
        for query in ('ЕЛКИ', 'STRASSE', 'рекламы', 'A-102'):
            self.assertEqual([row['id'] for row in memory.search(query)], [identity], query)
        self.assertEqual(memory.search('A-103'), [])
        self.assertTrue(any(' LIKE ' in sql for sql in statements))
        self.assertFalse(any(' MATCH ' in sql for sql in statements))

    def test_real_fts_and_injected_missing_module_have_identical_results(self):
        indexed = self.memory()
        if not indexed.fts_available:
            self.skipTest('Actual engine has no FTS5; cannot compare indexed branch')
        identity = self.note('Ёлки Straße', 'Реклама автомобиля A-102; café; 100%_')
        self.note('Other', 'Unrelated control')
        fallback = self.memory(missing_module=True)
        for query in ('елки', 'strasse', 'рекламы', 'машина', 'A-102', 'A102', 'A-103',
                      '" OR *', '100%_', 'café', 'несуществующий'):
            with self.subTest(query=query, sqlite=sqlite3.sqlite_version):
                expected = indexed.search(query, fuzzy_identifiers=True)
                self.assertEqual(fallback.search(query, fuzzy_identifiers=True), expected)
        self.store.save_note(identity, 'Changed', 'Совершенно новый текст B-203', None, False, '')
        self.assertEqual(fallback.search('A-102'), [])
        self.assertEqual([row['id'] for row in fallback.search('B-203')], [identity])
        restored = self.memory()
        self.assertEqual(restored.search('B-203'), fallback.search('B-203'))
        self.assertEqual(restored.search('A-102'), [])


if __name__ == '__main__':
    unittest.main()

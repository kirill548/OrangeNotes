"""FTS migration/candidate contract; fallback has identical scoped results."""
import tempfile
import sqlite3
from unittest.mock import patch
import unittest
from pathlib import Path
from app.database.store import Store
from app.services.memory_store import MemoryStore


class MemoryFTSTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.store=Store(Path(self.temp.name)/'notes.db')
        self.memory=MemoryStore(self.store)
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.store.db.close)
        self.addCleanup(lambda: self.memory.close())

    def note(self,title,body):
        identity=self.store.create_note()
        self.store.save_note(identity,title,body,None,False,'')
        return identity

    def test_literal_fts_equivalence_and_normalization(self):
        self.note('Автомобили','Реклама ёлки A-102')
        self.note('Другой','Аренда студии')
        for query in ['автомобиль','рекламы','елки','A-102','A102','машина','несуществующий']:
            indexed=self.memory.search(query,fuzzy_identifiers=True)
            self.memory.fts_available=False
            try:
                scanned=self.memory.search(query,fuzzy_identifiers=True)
            finally:
                self.memory.fts_available=True
            self.assertEqual(indexed,scanned,query)

    def test_incremental_edit_and_unchanged_no_fts_writes(self):
        identity=self.note('Тест','Первый текст')
        self.memory.sync()
        statements=[]
        self.memory.db.set_trace_callback(statements.append)
        self.memory.sync()
        self.memory.db.set_trace_callback(None)
        self.assertFalse(any('INSERT INTO memory_literal_fts' in item for item in statements))
        self.store.save_note(identity,'Тест','Второй текст',None,False,'')
        self.assertEqual(self.memory.search('Первый'),[])
        self.assertEqual(self.memory.search('Второй')[0]['id'],identity)

    def test_migration_backfills_existing_chunks_without_new_revision(self):
        identity=self.note('Миграция','Содержимое')
        before=self.memory.search('Содержимое')[0]['revision']
        with self.memory.db:
            self.memory.db.execute('DROP TABLE memory_literal_fts')
        self.memory.close()
        self.memory=MemoryStore(self.store)
        self.assertEqual(self.memory.search('Содержимое')[0]['revision'],before)
        self.assertTrue(self.memory.store.rows('SELECT 1 FROM memory_literal_fts WHERE note_id=?',(identity,)))

    def test_scope_and_move_remove_old_candidates(self):
        identity=self.note('Секрет','Личная информация')
        self.memory.sync()
        self.memory.move_note(identity,2)
        self.assertEqual(self.memory.search('Секрет'),[])
        self.assertEqual(self.memory.search('Секрет',workspace_id=2)[0]['id'],identity)

    def test_candidate_sql_uses_match_and_exact_id_never_guessed(self):
        self.note('Номер','A-102')
        statements=[]
        self.memory.db.set_trace_callback(statements.append)
        self.assertEqual(self.memory.search('A-103'),[])
        self.memory.db.set_trace_callback(None)
        self.assertTrue(any('memory_literal_fts MATCH' in item for item in statements))

    def unavailable(self, message='no such module: fts5'):
        original=sqlite3.connect
        class NoFTS(sqlite3.Connection):
            def execute(self, sql, parameters=()):
                if 'CREATE VIRTUAL TABLE' in sql:
                    raise sqlite3.OperationalError(message)
                return super().execute(sql, parameters)
        with patch('app.database.store.sqlite3.connect',side_effect=lambda *a,**kw:original(*a,factory=NoFTS,**kw)):
            return MemoryStore(self.store)

    def test_missing_module_uses_unicode_like_and_fuzzy_fallback(self):
        identity=self.note('Ёлки Straße','Реклама автомобиля A-102')
        expected={q:self.memory.search(q,fuzzy_identifiers=True) for q in ['елки','strasse','рекламы','A102','A-103','машина']}
        fallback=self.unavailable();self.addCleanup(fallback.close)
        self.assertFalse(fallback.fts_available)
        statements=[];fallback.db.set_trace_callback(statements.append)
        for query,result in expected.items():
            self.assertEqual(fallback.search(query,fuzzy_identifiers=True),result,query)
        self.assertTrue(any(' LIKE ' in sql for sql in statements))
        self.assertFalse(any(' MATCH ' in sql for sql in statements))

    def test_fallback_edits_rebuild_index_when_fts_returns(self):
        identity=self.note('Планы','Первоначальный текст')
        self.memory.sync()
        fallback=self.unavailable()
        try:
            self.store.save_note(identity,'Планы','Новый текст',None,False,'')
            self.assertEqual(fallback.search('Новый')[0]['id'],identity)
        finally:
            fallback.close()
        restored=MemoryStore(self.store);self.addCleanup(restored.close)
        self.assertEqual(restored.search('Первоначальный'),[])
        self.assertEqual(restored.search('Новый')[0]['id'],identity)
        self.assertFalse(restored.store.rows("SELECT 1 FROM memory_search_state WHERE key='fts_dirty'"))

    def test_physical_delete_removes_both_fts_indexes(self):
        identity=self.note('Удаление','Содержимое')
        self.memory.sync()
        self.store.execute('DELETE FROM notes WHERE id=?',(identity,))
        self.assertEqual(self.memory.search('Содержимое'),[])
        for table in ('memory_fts','memory_literal_fts'):
            self.assertFalse(self.memory.store.rows('SELECT 1 FROM '+table+' WHERE note_id=?',(identity,)))

    def test_unrelated_operational_errors_are_not_hidden(self):
        with self.assertRaisesRegex(sqlite3.OperationalError,'database is locked'):
            self.unavailable('database is locked')

    def test_phrase_across_chunk_boundary_and_explicit_tokenizer(self):
        identity=self.note('Фраза','x '*245+'аренда студии завтра')
        self.assertEqual(self.memory.search('аренда студии')[0]['id'],identity)
        ddl=self.memory.store.rows("SELECT sql FROM sqlite_master WHERE name='memory_literal_fts'")[0]['sql']
        self.assertIn('unicode61',ddl)

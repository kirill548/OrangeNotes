"""FTS migration/candidate contract; fallback has identical scoped results."""
import tempfile
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

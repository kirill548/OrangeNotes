import tempfile
import unittest
from pathlib import Path

from app.database.store import Store
from app.services.memory_store import MemoryStore


class UserSynonymTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'notes.sqlite3')
        self.memory = MemoryStore(self.store)
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.store.db.close)
        self.addCleanup(lambda: self.memory.close())

    def note(self, text, workspace=1):
        identity = self.store.create_note()
        self.store.save_note(identity, '', text, None, False, '')
        if workspace != 1:
            self.memory.move_note(identity, workspace)
        return identity

    def test_persistence_and_removal(self):
        self.memory.set_synonyms('работа', ['совещание', 'митинг'])
        self.memory.close()
        self.memory = MemoryStore(self.store)
        self.assertEqual(self.memory.list_synonyms()[0]['terms'], ['митинг', 'совещание'])
        self.assertTrue(self.memory.delete_synonyms('работа'))
        self.assertEqual(self.memory.list_synonyms(), [])

    def test_alias_fts_and_no_fts_results_identical(self):
        identity = self.note('Спринт начнется завтра')
        self.memory.set_synonyms('план', ['итерация', 'спринт'])
        statements = []
        self.memory.db.set_trace_callback(statements.append)
        indexed = self.memory.search('итерация')
        self.memory.db.set_trace_callback(None)
        self.assertEqual([row['id'] for row in indexed], [identity])
        self.assertEqual(indexed[0]['search_stage'], 'fallback')
        self.assertIn('спринт', indexed[0]['synonym_matches'])
        self.assertTrue(any('MATCH' in sql and 'спринт' in sql for sql in statements))
        self.memory.fts_available = False
        self.assertEqual(indexed, self.memory.search('итерация'))

    def test_literal_priority(self):
        exact = self.note('Итерация')
        self.note('Спринт')
        self.memory.set_synonyms('план', ['итерация', 'спринт'])
        self.assertEqual([row['id'] for row in self.memory.search('итерация')], [exact])

    def test_alias_fts_stems_match_normalized_index(self):
        identity = self.note('Автомобили готовы')
        self.memory.set_synonyms('транспорт', ['тачка', 'автомобили'])
        self.memory.sync()
        # memory_literal_fts indexes stems; unlike raw memory_fts, it stores
        # «автомобил» for «автомобили». Expansion uses that normalization.
        rows = self.memory.db.execute('SELECT note_id FROM memory_literal_fts WHERE memory_literal_fts MATCH ?',
                                      ('"автомобил"',)).fetchall()
        self.assertEqual([row[0] for row in rows], [identity])
        self.assertEqual(self.memory.search('тачка')[0]['id'], identity)

    def test_workspace_and_visibility(self):
        personal = self.note('Спринт')
        work = self.note('Спринт', workspace=2)
        self.memory.set_synonyms('план', ['итерация', 'спринт'])
        self.assertEqual([row['id'] for row in self.memory.search('итерация')], [personal])
        self.assertEqual(self.memory.list_synonyms(2), [])
        self.assertEqual(self.memory.search('итерация', workspace_id=2), [])
        self.memory.set_synonyms('план', ['итерация', 'спринт'], workspace_id=2)
        self.assertEqual([row['id'] for row in self.memory.search('итерация', workspace_id=2)], [work])

    def test_ids_and_injection_rejected(self):
        for term in ['A-102', '123456', 'token', 'password', 'митинг" OR *', '../../file']:
            with self.subTest(term=term), self.assertRaises(ValueError):
                self.memory.set_synonyms('план', ['спринт', term])
        self.note('Спринт A-102')
        self.memory.set_synonyms('план', ['итерация', 'спринт'])
        self.assertEqual(self.memory.search('итерация A-103'), [])

    def test_no_recursive_expansion_and_limits(self):
        self.memory.set_synonyms('первая', ['альфа', 'бета'])
        self.memory.set_synonyms('вторая', ['бета', 'гамма'])
        self.assertEqual(self.memory.synonyms.expand('альфа', 1), ['альфа', 'бета'])
        self.assertEqual(self.memory.synonyms.expand('суперальфа', 1), [])
        self.assertEqual(self.memory.synonyms.expand('пароль альфа', 1), [])
        with self.assertRaises(ValueError):
            self.memory.set_synonyms('пустая', ['слово', 'слово'])
        with self.assertRaises(ValueError):
            self.memory.set_synonyms('пустая', ['слово'] * 13)

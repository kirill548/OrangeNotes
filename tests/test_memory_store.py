import sqlite3
import math
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from app.database.store import Store
from app.services.memory_store import MemoryStore


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.path=Path(self.directory.name)/'notes.sqlite3'
        self.store=Store(self.path)
        self.memory=MemoryStore(self.store)

    def tearDown(self):
        self.memory.close()
        self.store.db.close()
        self.directory.cleanup()

    def note(self,title,body,space=1):
        note=self.store.create_note()
        self.store.save_note(note,title,body,None,False,'')
        if space!=1: self.memory.move_note(note,space)
        return note

    def test_legacy_metadata_unknown_and_own_connection(self):
        note=self.store.execute("INSERT INTO notes(title,body,updated_at) VALUES('Старая заметка','<p>Текст</p>','2026-10-01T00:00:00')").lastrowid
        self.assertIsNone(self.store.rows('SELECT created_at FROM notes WHERE id=?',(note,))[0][0])
        self.assertEqual([row['kind'] for row in self.memory.list_workspaces()],['personal','work'])
        self.memory.close(); self.memory=MemoryStore(self.path)
        self.assertEqual(len(self.store.rows('SELECT * FROM notes')),1)

    def test_workspace_permission_filters_search_resolve_and_move(self):
        private=self.note('Контакт Лена','<p>Секретный телефон 12345678</p>')
        work=self.note('Контакт Лена','<p>Рабочий телефон 87654321</p>',2)
        rows=self.memory.search('Лена')
        self.assertEqual([row['id'] for row in rows],[private])
        work_rows=self.memory.search('Лена',workspace_id=2)
        self.assertEqual([row['id'] for row in work_rows],[work])
        self.assertIsNone(self.memory.resolve(work,work_rows[0]['revision'],1))
        self.memory.move_note(private,2)
        self.assertEqual(self.memory.search('12345678'),[])
        self.assertIsNone(self.memory.resolve(private,rows[0]['revision'],1))
        self.assertEqual(self.memory.search('12345678',workspace_id=2)[0]['id'],private)
        for space in [None,True,99]:
            with self.assertRaises(ValueError): self.memory.search('Лена',workspace_id=space)

    def test_snapshot_revision_stale_resolve_and_restart(self):
        note=self.note('План','<p>Первый текст</p>')
        first=self.memory.search('Первый')[0]
        self.assertGreaterEqual(first['revision'],1)
        count=len(self.memory.store.rows('SELECT * FROM note_versions'))
        self.assertEqual(self.memory.sync()['changed'],0)
        self.store.save_note(note,'План','<p>Второй текст</p>',None,False,'')
        self.assertIsNone(self.memory.resolve(note,first['revision']))
        second=self.memory.search('Второй')[0]
        self.assertEqual(second['revision'],first['revision']+1)
        self.assertEqual(self.memory.search('Первый'),[])
        self.assertEqual(len(self.memory.store.rows('SELECT * FROM note_versions')),count+1)
        self.memory.close(); self.memory=MemoryStore(self.path)
        self.assertEqual(self.memory.resolve(note,second['revision'])['body'],'Второй текст')

    def test_archive_trash_and_hard_delete(self):
        note=self.note('Гараж','<p>Гараж рядом с домом</p>')
        revision=self.memory.search('Гараж')[0]['revision']
        self.store.execute('UPDATE notes SET archived=1 WHERE id=?',(note,))
        self.assertEqual(self.memory.search('Гараж')[0]['state'],'archive')
        self.assertEqual(self.memory.search('Гараж',include_archive=False),[])
        self.store.execute('UPDATE notes SET deleted=1 WHERE id=?',(note,))
        self.assertEqual(self.memory.search('Гараж'),[])
        self.assertIsNone(self.memory.resolve(note,revision))
        self.assertEqual(self.memory.resolve(note,revision,include_trash=True)['state'],'trash')
        self.assertIsNone(self.memory.resolve(note,revision,include_trash=True,include_archive=False))
        self.assertEqual(self.memory.search('Гараж',include_trash=True)[0]['state'],'trash')
        self.store.execute('DELETE FROM notes WHERE id=?',(note,))
        self.assertEqual(self.memory.search('Гараж',include_trash=True),[])
        self.assertFalse(self.memory.store.rows('SELECT * FROM note_versions'))
        self.assertFalse(self.memory.store.rows('SELECT * FROM memory_chunks'))

    def test_phone_fuzzy_name_synonym_and_ambiguous_contacts(self):
        garage=self.note('Аренда бокса','<p>Снять гараж: Сергей +7 (900) 123-45-67</p>')
        first=self.note('Александр Иванов','<p>Мастерская мебели, телефон 555-11-22</p>')
        second=self.note('Александр Петров','<p>Маркетинг студии, телефон 555-33-44</p>')
        self.assertEqual(self.memory.search('79001234567')[0]['id'],garage)
        self.assertEqual({row['id'] for row in self.memory.search('Алексадр')},{first,second})
        self.assertEqual(self.memory.search('парковка для автомобиля')[0]['id'],garage)
        self.assertEqual(self.memory.search('продвижение')[0]['id'],second)
        self.assertTrue(all(row['retrieval']=='keyword_fallback' for row in self.memory.search('Александр')))

    def test_fuzzy_identifier_is_opt_in_and_exact_always_wins(self):
        near=self.note('Заказ','ABCD12345678')
        self.assertEqual(self.memory.search('ABCD12345679'),[])
        rows=self.memory.search('ABCD12345679',fuzzy_identifiers=True)
        self.assertEqual(rows[0]['id'],near)
        self.assertEqual(rows[0]['retrieval'],'fuzzy_identifier')
        self.assertEqual(rows[0]['identifier_matches'][0]['distance'],1)
        exact=self.note('Точный','ABCD12345679')
        self.assertEqual([r['id'] for r in self.memory.search('ABCD12345679',fuzzy_identifiers=True)],[exact])
        self.assertEqual(self.memory.search('ABCD99999999',fuzzy_identifiers=True),[])

    def test_fuzzy_numeric_identifier_respects_scope_and_trash(self):
        private=self.note('Личный','12345678')
        self.note('Рабочий','12345679',2)
        self.assertEqual([r['id'] for r in self.memory.search('12345677',fuzzy_identifiers=True)],[private])
        self.store.execute('UPDATE notes SET deleted=1 WHERE id=?',(private,))
        self.assertEqual(self.memory.search('12345677',fuzzy_identifiers=True),[])
        self.assertEqual(self.memory.search('12345677',include_trash=True,fuzzy_identifiers=True)[0]['id'],private)

    def test_chunks_offsets_and_no_cross_note_text(self):
        body='А'*500+' Проверка '+'Б'*600
        note=self.note('Длинная','<p>'+body+'</p>')
        self.memory.sync()
        chunks=self.memory.store.rows('SELECT * FROM memory_chunks WHERE note_id=? ORDER BY position',(note,))
        self.assertEqual([row['position'] for row in chunks],[0,400,800])
        self.assertTrue(all(body[row['position']:row['position']+500]==row['chunk'] for row in chunks))
        self.assertEqual(''.join(row['chunk'][:400] for row in chunks),body)
        self.assertTrue(all(len(row['chunk'])<=500 for row in chunks))

    def test_boundary_phrase_and_neighbor_excerpt(self):
        phrase='Согласовать договор с Владом'
        body='а'*490+' '+phrase+' '+'б'*700
        note=self.note('Договор',body)
        result=self.memory.search('Согласовать договор')[0]
        self.assertEqual(result['id'],note)
        self.assertIn(phrase,result['chunk'])
        self.assertEqual(result['chunk'],result['body'][result['offset']:result['offset']+len(result['chunk'])])
        self.assertLessEqual(len(result['chunk']),1300)

    def test_unchanged_sync_does_not_rewrite_chunks(self):
        first=self.note('Первый','текст '*300)
        second=self.note('Второй','другой '*300)
        self.memory.sync()
        before=[tuple(row) for row in self.memory.store.rows('SELECT rowid,* FROM memory_chunks ORDER BY note_id,position')]
        with patch('app.services.memory_store.plain_body',side_effect=AssertionError('unchanged body rebuilt')):
            self.assertEqual(self.memory.sync()['changed'],0)
        self.assertEqual(before,[tuple(row) for row in self.memory.store.rows('SELECT rowid,* FROM memory_chunks ORDER BY note_id,position')])
        self.store.execute('UPDATE notes SET body=? WHERE id=?',('новый текст',first))
        self.assertEqual(self.memory.sync()['changed'],1)
        self.assertEqual([row for row in before if row[1]==second],[tuple(row) for row in self.memory.store.rows('SELECT rowid,* FROM memory_chunks WHERE note_id=? ORDER BY position',(second,))])

    def test_chunk_query_does_not_duplicate_full_body(self):
        self.note('Большая заметка','контекст '*20000)
        statements=[]
        self.memory.db.set_trace_callback(statements.append)
        try:
            self.memory.search('контекст')
        finally:
            self.memory.db.set_trace_callback(None)
        chunk_selects=[sql for sql in statements if 'JOIN memory_chunks c' in sql]
        self.assertEqual(len(chunk_selects),1)
        self.assertNotIn('n.body',chunk_selects[0])

    def test_search_returns_full_body_once_per_note(self):
        first=self.note('Первый','контекст '*20000)
        second=self.note('Второй','контекст '*20000)
        reads=[]
        original=self.memory.store.rows
        def track(sql,parameters=()):
            rows=original(sql,parameters)
            reads.extend(row['id'] for row in rows if 'body' in row.keys())
            return rows
        with patch.object(self.memory.store,'rows',side_effect=track):
            self.assertEqual({row['id'] for row in self.memory.search('контекст')},{first,second})
        self.assertEqual(sorted(reads),sorted([first,second]))

    def test_raw_edit_during_embedding_discards_old_revision(self):
        note=self.note('Исходная','старый контекст')
        timestamp=self.store.rows('SELECT updated_at FROM notes WHERE id=?',(note,))[0][0]
        def embed(texts):
            if any('Исходная' in text for text in texts):
                self.store.execute('UPDATE notes SET body=? WHERE id=?',('новое содержимое',note))
            return [[1.0,0.0] for text in texts]
        self.assertEqual(self.memory.search('unmatched semantic phrase',embedding_provider=embed),[])
        self.assertEqual(self.store.rows('SELECT updated_at FROM notes WHERE id=?',(note,))[0][0],timestamp)
        self.assertFalse(self.memory.store.rows('SELECT * FROM memory_embeddings'))

    def test_old_index_migrates_once_without_new_revision(self):
        note=self.note('Старая','текст '*300)
        self.memory.sync()
        revision=self.memory.store.rows('SELECT max(revision) FROM note_versions')[0][0]
        self.memory.store.execute('DELETE FROM memory_index_state')
        self.memory.store.execute('DELETE FROM memory_chunks WHERE note_id=?',(note,))
        self.assertEqual(self.memory.sync()['changed'],0)
        self.assertEqual(self.memory.store.rows('SELECT max(revision) FROM note_versions')[0][0],revision)
        self.assertEqual(self.memory.store.rows('SELECT position FROM memory_chunks WHERE note_id=? ORDER BY position',(note,))[1][0],400)
        with patch('app.services.memory_store.plain_body',side_effect=AssertionError('second rebuild')):
            self.memory.sync()

    def test_sync_failure_rolls_back_versions_and_chunks(self):
        first=self.note('А','<p>Первый</p>'); second=self.note('Б','<p>Второй</p>')
        baseline=[dict(row) for row in self.memory.store.rows('SELECT * FROM note_versions ORDER BY id')]
        self.store.execute("UPDATE notes SET body='<p>Новая несинхронизированная версия</p>' WHERE id IN (?,?)",(first,second))
        calls=[]
        def fail(body):
            calls.append(body)
            if len(calls)==2: raise RuntimeError('interrupted indexing')
            return body
        with patch('app.services.memory_store.plain_body',fail):
            with self.assertRaises(RuntimeError): self.memory.sync()
        self.assertEqual([dict(row) for row in self.memory.store.rows('SELECT * FROM note_versions ORDER BY id')],baseline)
        self.assertFalse(self.memory.store.rows('SELECT * FROM memory_chunks'))
        self.assertEqual(self.memory.sync()['changed'],2)

    def test_instance_created_in_worker_uses_own_connection(self):
        note=self.note('План','<p>Проверка</p>')
        result=[]; failures=[]
        def worker():
            memory=None
            try:
                memory=MemoryStore(self.path)
                result.extend(memory.search('Проверка'))
            except Exception as error: failures.append(error)
            finally:
                if memory: memory.close()
        task=threading.Thread(target=worker); task.start(); task.join(10)
        self.assertFalse(task.is_alive()); self.assertFalse(failures)
        self.assertEqual(result[0]['id'],note)

    def test_semantic_pipeline_cache_is_scoped_and_versioned(self):
        private=self.note('Гараж','<p>Место для машины</p>')
        self.note('Секрет фирмы','<p>Рабочее содержимое</p>',2)
        calls=[]
        def embed(texts):
            calls.append(list(texts))
            return [[1.0,0.0] for text in texts]
        rows=self.memory.search('железный конь',embedding_provider=embed,embedding_model='test-model-v1')
        self.assertEqual(rows[0]['id'],private)
        self.assertEqual(rows[0]['retrieval'],'hybrid')
        self.assertFalse(any('Рабочее' in text or 'Секрет фирмы' in text for batch in calls for text in batch))
        self.assertEqual(max(map(len,calls)),1)
        calls.clear()
        self.memory.search('железный конь',embedding_provider=embed,embedding_model='test-model-v1')
        self.assertEqual(calls,[['железный конь']])
        self.memory.search('железный конь',embedding_provider=embed,embedding_model='test-model-v2')
        self.assertEqual(len(self.memory.store.rows('SELECT * FROM memory_embeddings')),2)

    def test_move_during_embedding_discards_former_scope_result(self):
        note=self.note('Личное','<p>Секрет</p>')
        def embed(texts):
            if any('Личное' in text for text in texts): self.store.execute('UPDATE notes SET workspace_id=2 WHERE id=?',(note,))
            return [[1.0,0.0] for text in texts]
        self.assertEqual(self.memory.search('тайна',embedding_provider=embed),[])
        self.assertFalse(self.memory.store.rows('SELECT * FROM memory_embeddings'))

    def test_invalid_embeddings_raise_without_fake_semantic_claim(self):
        self.note('План','<p>Текст</p>')
        for embed in [lambda texts:[],lambda texts:[[float('nan')]],lambda texts:[[0.0,0.0]]]:
            with self.assertRaises(ValueError): self.memory.search('неизвестная ассоциация',embedding_provider=embed)
        self.assertEqual(self.memory.search('Текст',embedding_provider=False)[0]['retrieval'],'keyword_fallback')

    def test_qwen_specific_threshold_accepts_point268_rejects_point162(self):
        related=self.note('Связанный объект','<p>Полезный контекст</p>')
        self.note('Другая запись','<p>Посторонний контекст</p>')
        # Concept-only query; opaque identifiers now intentionally require exact evidence.
        query='семантическая ассоциация без общего слова'
        def embed(texts):
            vectors=[]
            for text in texts:
                if text==query: vectors.append([1.0,0.0])
                else:
                    similarity=0.268 if 'Связанный объект' in text else 0.162
                    vectors.append([similarity,math.sqrt(1-similarity*similarity)])
            return vectors
        results=self.memory.search(query,embedding_provider=embed,embedding_model='qwen3-embedding:0.6b')
        self.assertEqual([row['id'] for row in results],[related])
        self.assertAlmostEqual(results[0]['dense_similarity'],0.268)
        self.assertEqual(self.memory.search(query,embedding_provider=embed,embedding_model='different-model'),[])


if __name__=='__main__':
    unittest.main()

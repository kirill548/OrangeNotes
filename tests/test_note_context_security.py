import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.database.store import Store
from app.services.memory_store import MemoryStore


class NoteContextSecurityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name)/'notes.sqlite3')
        self.memory = MemoryStore(self.store)

    def tearDown(self):
        self.memory.close()
        self.store.db.close()
        self.temp.cleanup()

    def note(self, title, body, workspace=1):
        identity = self.store.create_note()
        self.store.save_note(identity, title, body, None, False, '')
        if workspace != 1:
            self.memory.move_note(identity, workspace)
        return identity

    def test_picker_scoped_metadata_and_literal_wildcards(self):
        own = self.note('100% done', 'private body')
        self.note('100% done', 'work secret', 2)
        self.note('100x done', 'other')
        rows = self.memory.list_context_notes('100%')
        self.assertEqual([row['id'] for row in rows], [own])
        self.assertNotIn('body', rows[0])

    def test_foreign_selection_fails_before_any_snapshot_writes(self):
        own = self.note('Own', 'allowed')
        foreign = self.note('Foreign', 'SECRET', 2)
        before = [tuple(row) for row in self.store.rows('SELECT * FROM note_versions')]
        with self.assertRaises(ValueError):
            self.memory.attachment_sources([own, foreign])
        self.assertEqual([tuple(row) for row in self.store.rows('SELECT * FROM note_versions')], before)
        self.assertEqual(self.store.rows('SELECT * FROM memory_chunks'), [])

    def test_invalid_ids_and_duplicates(self):
        for ids in ([True], [0], [-1], ['1'], [1, 1], list(range(1, 10)), '1'):
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                self.memory.attachment_sources(ids)
        self.assertEqual(self.memory.attachment_sources([]), [])

    def test_archive_trash_flags_are_enforced(self):
        identity = self.note('Archived', 'visible only explicitly')
        self.store.execute('UPDATE notes SET archived=1 WHERE id=?', (identity,))
        self.assertEqual(self.memory.list_context_notes(), [])
        with self.assertRaises(ValueError):
            self.memory.attachment_sources([identity])
        self.assertEqual(self.memory.attachment_sources([identity], include_archive=True)[0]['state'], 'archive')
        self.store.execute('UPDATE notes SET deleted=1 WHERE id=?', (identity,))
        with self.assertRaises(ValueError):
            self.memory.attachment_sources([identity], include_archive=True)
        self.assertEqual(self.memory.attachment_sources([identity], include_archive=True, include_trash=True)[0]['state'], 'trash')

    def test_long_note_relevant_fragment_and_stale_revision(self):
        identity = self.note('Plan', 'padding '*16000 + 'Secret meeting Tuesday at 18:00.')
        source = self.memory.attachment_sources([identity], query='meeting Tuesday')[0]
        self.assertGreater(source['offset'], 100000)
        self.assertLessEqual(len(source['chunk']), 500)
        self.assertIn('meeting Tuesday', source['chunk'])
        self.assertEqual(source['body'][source['offset']:source['offset']+len(source['chunk'])], source['chunk'])
        self.store.save_note(identity, 'Plan', 'Changed body', None, False, '')
        self.assertIsNone(self.memory.resolve(identity, source['revision']))
        refreshed = self.memory.attachment_sources([identity])[0]
        self.assertGreater(refreshed['revision'], source['revision'])

    def test_moved_after_selection_and_physical_delete_fail_closed(self):
        identity = self.note('Own', 'source')
        source = self.memory.attachment_sources([identity])[0]
        self.memory.move_note(identity, 2)
        with self.assertRaises(ValueError):
            self.memory.attachment_sources([identity])
        self.assertIsNone(self.memory.resolve(identity, source['revision']))
        self.store.execute('DELETE FROM notes WHERE id=?', (identity,))
        with self.assertRaises(ValueError):
            self.memory.attachment_sources([identity], workspace_id=2)

    def test_attachment_never_indexes_other_notes(self):
        identity = self.note('Pinned', 'attached')
        other = self.note('Unpinned', 'not requested')
        foreign = self.note('Work', 'PRIVATE', 2)
        before = [tuple(row) for row in self.store.rows('SELECT * FROM note_versions WHERE note_id!=?', (identity,))]
        self.memory.attachment_sources([identity])
        self.assertEqual([tuple(row) for row in self.store.rows('SELECT * FROM note_versions WHERE note_id!=?', (identity,))], before)
        self.assertEqual([row['note_id'] for row in self.store.rows('SELECT DISTINCT note_id FROM memory_chunks')], [identity])
        self.assertNotEqual(identity, other)
        self.assertNotEqual(identity, foreign)

    def test_mixed_deleted_selection_does_not_index_valid_pin(self):
        own = self.note('Allowed', 'Current context')
        deleted = self.note('Deleted', 'Must not be included')
        self.store.execute('UPDATE notes SET deleted=1 WHERE id=?', (deleted,))
        before = [tuple(row) for row in self.store.rows('SELECT * FROM note_versions')]
        with self.assertRaises(ValueError):
            self.memory.attachment_sources([own, deleted])
        self.assertEqual([tuple(row) for row in self.store.rows('SELECT * FROM note_versions')], before)
        self.assertEqual(self.store.rows('SELECT * FROM memory_index_state'), [])

    def test_archived_between_sync_and_resolution_fails_closed(self):
        identity = self.note('Selected', 'Sensitive snapshot')
        original_sync = self.memory.sync

        def archive_after_sync(*args, **kwargs):
            result = original_sync(*args, **kwargs)
            self.store.execute('UPDATE notes SET archived=1 WHERE id=?', (identity,))
            return result

        with patch.object(self.memory, 'sync', side_effect=archive_after_sync):
            with self.assertRaises(ValueError):
                self.memory.attachment_sources([identity])


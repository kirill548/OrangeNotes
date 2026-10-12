"""Context picker and request snapshots, with no model or network."""
from pathlib import Path
import tempfile
import threading
import unittest

from PySide6.QtWidgets import QApplication
from app.database.store import Store
from app.services.memory_store import MemoryStore
from app.widgets.note_context_pins import NoteContextPins
from app.ui.companion import CompanionDialog, _RequestThread


class NoteContextUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name)/'notes.sqlite3')
        memory = MemoryStore(self.store.path)
        self.workspaces = memory.list_workspaces(); memory.close()
        self.personal = self.workspaces[0]['id']; self.work = self.workspaces[1]['id']
        self.note = self.make_note('План', self.personal)
        self.other = self.make_note('Секрет работы', self.work)
        self.pins = NoteContextPins(self.store)
        self.pins.set_scope(self.personal)
        self.dialogs = []

    def tearDown(self):
        for dialog in self.dialogs: dialog.close(); dialog.deleteLater()
        self.pins.deleteLater(); self.app.processEvents()
        self.store.db.close(); self.temp.cleanup()

    def make_note(self, title, workspace, archived=0, deleted=0):
        note = self.store.create_note()
        self.store.execute('UPDATE notes SET title=?,workspace_id=?,archived=?,deleted=? WHERE id=?',
                           (title, workspace, archived, deleted, note))
        return note

    def select(self, note): self.pins.candidates.setCurrentIndex(self.pins.candidates.findData(note))

    def test_add_remove_and_no_duplicates(self):
        self.select(self.note); self.assertTrue(self.pins.add_current())
        self.assertEqual(self.pins.note_ids, (self.note,))
        self.assertEqual(self.pins.candidates.findData(self.note), -1)
        self.assertTrue(self.pins.remove(self.note)); self.assertEqual(self.pins.note_ids, ())
        self.assertFalse(self.pins.remove(self.note))

    def test_workspace_change_clears_pins_and_filters_candidates(self):
        self.assertEqual(self.pins.candidates.findData(self.other), -1)
        self.select(self.note); self.pins.add_current(); self.pins.set_scope(self.work)
        self.assertEqual(self.pins.note_ids, ())
        self.assertEqual(self.pins.candidates.findData(self.note), -1)
        self.assertGreaterEqual(self.pins.candidates.findData(self.other), 0)

    def test_policy_drops_archived_and_trash_pins(self):
        archived = self.make_note('Архив', self.personal, archived=1)
        trash = self.make_note('Корзина', self.personal, deleted=1)
        self.pins.set_scope(self.personal, True, True)
        self.select(archived); self.pins.add_current(); self.select(trash); self.pins.add_current()
        self.pins.set_scope(self.personal, False, False)
        self.assertEqual(self.pins.note_ids, ())
        self.assertEqual(self.pins.candidates.findData(archived), -1)
        self.assertEqual(self.pins.candidates.findData(trash), -1)

    def test_title_search_and_empty_title(self):
        unnamed = self.make_note('', self.personal)
        self.pins.refresh(); index = self.pins.candidates.findData(unnamed)
        self.assertIn('Без названия', self.pins.candidates.itemText(index))
        self.pins.search.setText('пЛаН'); self.pins.refresh()
        self.assertEqual(self.pins.candidates.count(), 1)
        self.assertEqual(self.pins.candidates.currentData(), self.note)

    def test_eight_pin_limit(self):
        ids = [self.note] + [self.make_note(str(i), self.personal) for i in range(8)]
        for note in ids[:8]: self.pins.refresh(); self.select(note); self.assertTrue(self.pins.add_current())
        self.select(ids[8]); self.assertFalse(self.pins.add_current()); self.assertFalse(self.pins.add_button.isEnabled())

    def test_moved_note_cannot_be_added_from_stale_picker(self):
        self.select(self.note)
        self.store.execute('UPDATE notes SET workspace_id=? WHERE id=?', (self.work,self.note))
        self.assertFalse(self.pins.add_current()); self.assertEqual(self.pins.note_ids, ())

    def test_refresh_updates_pinned_title_and_removes_moved_note(self):
        self.select(self.note); self.pins.add_current()
        changes = []
        self.pins.changed.connect(lambda: changes.append(self.pins.note_ids))
        self.store.execute('UPDATE notes SET title=? WHERE id=?', ('Новый план', self.note))
        self.pins.refresh()
        self.assertEqual(self.pins.pin_row.itemAt(0).widget().text(), 'Новый план ×')
        self.store.execute('UPDATE notes SET workspace_id=? WHERE id=?', (self.work, self.note))
        self.pins.refresh()
        self.assertEqual(self.pins.note_ids, ())
        self.assertEqual(changes, [(self.note,), ()])
        self.assertIn('Убраны недоступные', self.pins.hint.text())
        self.assertFalse(self.pins.add_button.isEnabled())

    def test_empty_search_and_limit_explain_unavailable_add(self):
        self.pins.search.setText('Несуществующая заметка'); self.pins.refresh()
        self.assertFalse(self.pins.candidates.isEnabled())
        self.assertFalse(self.pins.add_button.isEnabled())
        self.assertIn('Нет подходящих', self.pins.candidates.placeholderText())
        self.assertIn('Измените запрос', self.pins.hint.text())
        self.pins.search.clear(); self.pins.refresh()
        ids = [self.note] + [self.make_note(str(i), self.personal) for i in range(7)]
        for note in ids:
            self.pins.refresh(); self.select(note); self.pins.add_current()
        self.assertIn('Уберите одну', self.pins.hint.text())

    def test_candidate_tooltip_preserves_full_long_title(self):
        from PySide6.QtCore import Qt
        title = 'Длинный заголовок ' * 12
        note = self.make_note(title, self.personal); self.pins.refresh()
        index = self.pins.candidates.findData(note)
        self.assertEqual(self.pins.candidates.itemData(index, Qt.ToolTipRole), title.strip())
        self.assertLess(len(self.pins.candidates.itemText(index)), len(title))

    def test_request_snapshots_ids_and_legacy_engine_compatibility(self):
        for supports in (True, False):
            calls = []
            class Engine:
                supports_note_context = supports
                def ask(self, query, workspace_id, **kwargs): calls.append(kwargs); return {'text':'ok','sources':[]}
            ids = [self.note]
            thread = _RequestThread(1,lambda path:Engine(),self.store.path,'query',self.personal,'memory',[],
                                    {'provider':'search'},True,False,threading.Event(),attached_note_ids=ids)
            ids.clear(); thread.run()
            if supports: self.assertEqual(calls[0]['attached_note_ids'], (self.note,))
            else: self.assertNotIn('attached_note_ids', calls[0])

    def test_busy_request_locks_pins_and_uses_snapshot(self):
        from PySide6.QtTest import QTest
        release = threading.Event(); calls = []
        class Engine:
            supports_note_context = True
            def ask(self, query, workspace_id, **kwargs):
                calls.append(kwargs); release.wait(2)
                return {'text':'ok','sources':[],'engine_label':'Поиск'}
        dialog = CompanionDialog(self.store,engine_factory=lambda path:Engine()); self.dialogs.append(dialog)
        dialog.config['provider']='search'
        dialog.context_pins.candidates.setCurrentIndex(dialog.context_pins.candidates.findData(self.note))
        dialog.context_pins.add_current()
        self.assertTrue(dialog._start('query'))
        try:
            self.assertFalse(dialog.context_pins.isEnabled())
            self.assertFalse(dialog.include_archive.isEnabled())
            self.assertFalse(dialog.include_trash.isEnabled())
        finally:
            release.set()
            for _ in range(300):
                self.app.processEvents(); QTest.qWait(5)
                if not dialog.busy: break
        self.assertFalse(dialog.busy)
        self.assertTrue(dialog.context_pins.isEnabled())
        self.assertEqual(calls[0]['attached_note_ids'], (self.note,))

    def test_new_conversation_clears_context(self):
        dialog = CompanionDialog(self.store,engine_factory=lambda path:None); self.dialogs.append(dialog)
        dialog.context_pins.candidates.setCurrentIndex(dialog.context_pins.candidates.findData(self.note))
        self.assertTrue(dialog.context_pins.add_current())
        self.assertTrue(dialog.new_conversation()); self.assertEqual(dialog.context_pins.note_ids, ())

    def test_reopening_dialog_refreshes_new_note_candidates(self):
        dialog = CompanionDialog(self.store,engine_factory=lambda path:None); self.dialogs.append(dialog)
        note = self.make_note('Новая запись', self.personal)
        self.assertEqual(dialog.context_pins.candidates.findData(note), -1)
        dialog.show(); self.app.processEvents()
        self.assertGreaterEqual(dialog.context_pins.candidates.findData(note), 0)


if __name__ == '__main__': unittest.main()

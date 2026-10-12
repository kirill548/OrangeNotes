import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PySide6.QtWidgets import QApplication,QMainWindow
from app.database.store import Store
from app.ui.companion import CompanionDialog

class InteractiveHelperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=Store(Path(self.temp.name)/'notes.sqlite3')
        self.parent=QMainWindow()
        with patch('app.services.local_runtime.resource_advisory',return_value={'warning':None}):
            self.dialog=CompanionDialog(self.store,self.parent,engine_factory=lambda path:None)
    def tearDown(self):
        self.dialog.deleteLater();self.parent.deleteLater();self.app.processEvents();self.store.db.close();self.temp.cleanup()
    def test_actions_whitelist_does_not_render_generated_labels_or_urls(self):
        self.dialog._show_help_actions([{'action':'synonyms','label':'<script>bad</script>'},{'action':'https://evil.test'}, {'action':'synonyms'}])
        self.assertEqual([b.text() for b in self.dialog.help_action_buttons],['Настроить синонимы'])
    def test_trusted_action_opens_local_settings(self):
        with patch.object(self.dialog,'show_synonyms') as opened:
            self.assertTrue(self.dialog._activate_help_action('synonyms'));opened.assert_called_once()
        self.assertFalse(self.dialog._activate_help_action('delete_all'))
    def test_clear_conversation_removes_navigation_actions(self):
        self.dialog._show_help_actions([{'action':'attach_notes'}]);self.dialog._clear_draft()
        self.assertEqual(self.dialog.help_action_buttons,[])
    def test_draft_is_confirmation_only_and_recurrence_is_visible(self):
        before=self.store.db.execute('SELECT COUNT(*) FROM notes').fetchone()[0]
        self.dialog._show_draft({'workspace_id':self.dialog.workspace_id,'title':'Отчет','body':'Проверить отчет','once_at':'2099-10-12T10:00:00','rrule':'FREQ=WEEKLY;BYDAY=MO'})
        self.assertIn('подтверждения',self.dialog.draft_heading.text())
        self.assertEqual('По понедельникам',self.dialog.draft_recurrence.text())
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM notes').fetchone()[0],before)

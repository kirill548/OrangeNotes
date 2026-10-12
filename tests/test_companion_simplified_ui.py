"""Progressive disclosure keeps chat focused without dropping settings."""
import unittest
from tests import test_companion_ui as fixtures


class SimplifiedCompanionTests(unittest.TestCase):
    setUpClass=classmethod(fixtures.CompanionUITests.setUpClass.__func__)
    setUp=fixtures.CompanionUITests.setUp
    tearDown=fixtures.CompanionUITests.tearDown
    wait=fixtures.CompanionUITests.wait
    factory=fixtures.CompanionUITests.factory
    dialog=fixtures.CompanionUITests.dialog
    def test_advanced_and_context_controls_are_initially_collapsed(self):
        dialog=self.dialog()
        self.assertTrue(dialog.advanced_panel.isHidden())
        self.assertTrue(dialog.context_pins.isHidden())
        self.assertTrue(dialog.mode_hint.isHidden())
        self.assertTrue(dialog.scope_hint.isHidden())
        self.assertTrue(dialog.settings_button.isHidden())
        dialog.search_options_button.click()
        self.assertFalse(dialog.advanced_panel.isHidden())
        dialog.attach_notes_button.click()
        self.assertFalse(dialog.context_pins.isHidden())

    def test_ready_banner_moves_diagnostics_to_tooltip(self):
        dialog=self.dialog()
        dialog.config['provider']='ollama'
        dialog._connection_changed({'model_ready':True,'embedding_ready':True,'latency_ms':18})
        self.assertEqual(dialog.connection_banner.text(),'ИИ готов')
        self.assertIn('qwen3:4b',dialog.connection_banner.toolTip())
        self.assertNotIn('NDJSON',dialog.connection_banner.toolTip())
        self.assertNotIn('NDJSON',dialog.connection_banner.text())

    def test_missing_model_is_plain_search_without_fake_ready(self):
        dialog=self.dialog()
        dialog._connection_changed({'model_ready':False})
        self.assertEqual(dialog.connection_banner.text(),'Поиск без ИИ')

    def test_help_attach_action_reveals_picker(self):
        dialog=self.dialog()
        self.assertTrue(dialog.context_pins.isHidden())
        dialog._activate_help_action('attach_notes')
        self.assertFalse(dialog.context_pins.isHidden())

    def test_draft_uses_scrollable_panel_without_resizing_window(self):
        dialog=self.dialog();dialog.show();self.app.processEvents()
        before=dialog.size()
        dialog._show_draft({'workspace_id':dialog.workspace_id,'title':'Встреча','body':'Текст','once_at':'2026-10-15T12:00:00'})
        self.app.processEvents()
        self.assertFalse(dialog.draft_area.isHidden())
        self.assertTrue(dialog.draft_area.widgetResizable())
        self.assertGreaterEqual(dialog.draft_area.height(),190)
        self.assertTrue(dialog.context_pins.isHidden())
        dialog._clear_draft()
        self.assertTrue(dialog.draft_area.isHidden())

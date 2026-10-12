"""Companion v0.1.4 keeps one input and draft confirmation always reachable."""
import unittest
from tests import test_companion_ui as fixtures
from app.ui.companion import CompanionDialog


class CompanionRefactoringTests(unittest.TestCase):
    setUpClass=classmethod(fixtures.CompanionUITests.setUpClass.__func__)
    setUp=fixtures.CompanionUITests.setUp
    tearDown=fixtures.CompanionUITests.tearDown
    wait=fixtures.CompanionUITests.wait
    factory=fixtures.CompanionUITests.factory
    dialog=fixtures.CompanionUITests.dialog

    def test_modes_and_quick_actions_stay_hidden_when_options_open(self):
        dialog=self.dialog();dialog.show();self.app.processEvents()
        dialog.search_options_button.click();self.app.processEvents()
        self.assertTrue(dialog.mode.isHidden())
        self.assertTrue(dialog.quick_actions.isHidden())
        self.assertTrue(dialog.input.isVisible())
        self.assertTrue(dialog.send_button.isVisible())

    def test_automatic_routing(self):
        for query,mode in [('Найди мои заметки про отчет','memory'),
                           ('Проверь план: что упущено?','audit'),
                           ('Привет, как дела?','discuss'),
                           ('Напомни завтра в 15:00 купить молоко','discuss')]:
            with self.subTest(query=query):self.assertEqual(CompanionDialog._automatic_mode(query),mode)

    def test_draft_confirmation_is_outside_scrolling_fields(self):
        dialog=self.dialog();dialog.resize(520,480);dialog.show()
        dialog._show_draft({'workspace_id':dialog.workspace_id,'title':'Встреча','body':'Описание '*300,'once_at':'2026-10-15T12:00:00'})
        self.app.processEvents()
        self.assertEqual(dialog.draft_create.text(),'Сохранить в заметки')
        self.assertFalse(dialog.draft_panel.isAncestorOf(dialog.draft_create))
        self.assertTrue(dialog.draft_actions.isAncestorOf(dialog.draft_create))
        for fraction in [0,1]:
            bar=dialog.draft_area.verticalScrollBar();bar.setValue(bar.maximum()*fraction)
            self.app.processEvents()
            self.assertTrue(dialog.draft_create.isVisible())
            position=dialog.draft_create.mapTo(dialog,dialog.draft_create.rect().bottomRight())
            self.assertTrue(dialog.rect().contains(position))
        dialog._clear_draft();self.assertTrue(dialog.draft_actions.isHidden())

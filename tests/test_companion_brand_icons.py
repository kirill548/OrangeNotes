"""Branding must not turn untrusted model output into rich text."""
import unittest
from tests import test_companion_ui as fixtures
from app.ui.companion import BrandedChatEdit
from app.widgets.companion_mascot import companion_icon
from app.utils.app_icon import application_icon


class CompanionBrandIconsTests(unittest.TestCase):
    setUpClass=classmethod(fixtures.CompanionUITests.setUpClass.__func__)
    setUp=fixtures.CompanionUITests.setUp
    tearDown=fixtures.CompanionUITests.tearDown
    wait=fixtures.CompanionUITests.wait
    factory=fixtures.CompanionUITests.factory
    dialog=fixtures.CompanionUITests.dialog

    def test_dialog_and_task_card_have_brand_assets(self):
        dialog=self.dialog()
        self.assertFalse(dialog.windowIcon().isNull())
        self.assertIsInstance(dialog.chat,BrandedChatEdit)
        self.assertFalse(dialog.chat.avatar_icon.pixmap(20,20).isNull())
        self.assertFalse(dialog.draft_brand.pixmap().isNull())

    def test_welcome_message_has_bee_avatar(self):
        dialog=self.dialog()
        self.assertTrue(dialog.chat.toPlainText().startswith('Помощник\nПривет!'))
        self.assertEqual(len(list(dialog.chat.assistant_avatar_positions())),1)
        dialog.chat.clear()
        dialog._welcome()
        self.assertEqual(len(list(dialog.chat.assistant_avatar_positions())),1)

    def test_avatar_render_preserves_plain_text_and_rollback(self):
        dialog=self.dialog()
        dialog.chat.resize(640,300)
        baseline='Вы\nПривет'
        dialog.chat.setPlainText(baseline)
        dialog.chat.appendPlainText('\nПомощник\n<img src="https://invalid.test/leak">')
        self.assertIn('<img src=',dialog.chat.toPlainText())
        self.assertEqual(len(list(dialog.chat.assistant_avatar_positions())),1)
        self.assertFalse(dialog.chat.grab().isNull())
        dialog.chat.setPlainText(baseline)
        self.assertEqual(dialog.chat.toPlainText(),baseline)
        self.assertEqual(list(dialog.chat.assistant_avatar_positions()),[])

    def test_assistant_uses_bee_while_window_retains_application_brand(self):
        dialog=self.dialog()
        avatar=dialog.chat.avatar_icon.pixmap(20,20).toImage()
        bee=companion_icon().pixmap(20,20).toImage()
        brand=application_icon().pixmap(20,20).toImage()
        self.assertFalse(avatar.isNull())
        self.assertEqual(avatar,bee)
        self.assertNotEqual(avatar,brand)
        self.assertEqual(dialog.windowIcon().pixmap(20,20).toImage(),brand)

    def test_help_navigation_buttons_use_brand(self):
        dialog=self.dialog()
        dialog._show_help_actions([{'action':'attach_notes'}])
        self.assertEqual(len(dialog.help_action_buttons),1)
        self.assertFalse(dialog.help_action_buttons[0].icon().isNull())

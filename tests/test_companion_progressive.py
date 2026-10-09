"""Presentation streaming exposes only already grounded response packets."""
import unittest
from PySide6.QtTest import QTest
import test_companion_ui as helpers


class ProgressiveRenderingTests(unittest.TestCase):
    setUpClass=classmethod(helpers.CompanionUITests.setUpClass.__func__)
    setUp=helpers.CompanionUITests.setUp
    tearDown=helpers.CompanionUITests.tearDown
    wait=helpers.CompanionUITests.wait
    factory=helpers.CompanionUITests.factory
    dialog=helpers.CompanionUITests.dialog
    source=helpers.CompanionUITests.source

    def test_validated_text_progresses_without_committing_partial_history(self):
        dialog=self.dialog()
        text='Проверенный ответ. '*100
        dialog._completed(dialog._request_id,{'text':text,'sources':[]},None,'ask')
        self.wait(lambda:'Проверенный' in dialog.chat.toPlainText())
        self.assertNotIn(text,dialog.chat.toPlainText())
        self.assertEqual(dialog._history,[])
        self.wait(lambda:not dialog._pending_render)
        self.assertIn(text,dialog.chat.toPlainText())
        self.assertEqual(dialog._history[-1]['content'],text)

    def test_revision_change_rolls_back_visible_validated_prefix(self):
        source=self.source();dialog=self.dialog()
        response={'text':'VERIFIED-PREFIX '*100,'sources':[source]}
        dialog._completed(dialog._request_id,response,None,'ask')
        self.wait(lambda:'VERIFIED-PREFIX' in dialog.chat.toPlainText())
        self.store.save_note(source['note_id'],'changed','replacement',None,False,'')
        self.wait(lambda:not dialog._pending_render)
        self.assertNotIn('VERIFIED-PREFIX',dialog.chat.toPlainText())
        self.assertEqual(dialog._history,[])
        self.assertEqual(dialog.source_buttons,[])

    def test_cancel_invalidates_animation_and_rolls_back_partial_text(self):
        dialog=self.dialog()
        dialog._completed(dialog._request_id,{'text':'CANCEL-PREFIX '*100,'sources':[]},None,'ask')
        self.wait(lambda:'CANCEL-PREFIX' in dialog.chat.toPlainText())
        dialog.cancel()
        self.wait(lambda:not dialog._pending_render)
        self.assertNotIn('CANCEL-PREFIX',dialog.chat.toPlainText())
        self.assertEqual(dialog._history,[])

    def test_space_change_never_restores_previous_space_prefix(self):
        dialog=self.dialog()
        dialog._completed(dialog._request_id,{'text':'PERSONAL-PREFIX '*100,'sources':[]},None,'ask')
        self.wait(lambda:'PERSONAL-PREFIX' in dialog.chat.toPlainText())
        dialog.set_workspace(self.workspaces[1]['id'])
        self.wait(lambda:not dialog._pending_render)
        self.assertNotIn('PERSONAL-PREFIX',dialog.chat.toPlainText())
        self.assertEqual(dialog._history,[])
        self.assertEqual(dialog.workspace_id,self.workspaces[1]['id'])


if __name__=='__main__':unittest.main()

"""Presentation streaming exposes only already grounded response packets."""
import unittest
import threading
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


    def test_only_segment_packets_are_shown_and_reset_retracts_preview(self):
        dialog=self.dialog();baseline=dialog.chat.toPlainText()
        dialog._request_progress(dialog._request_id,{'kind':'raw','text':'UNSAFE'})
        self.assertEqual(dialog.chat.toPlainText(),baseline)
        dialog._request_progress(dialog._request_id,{'kind':'segment','text':'VERIFIED','sources':[]})
        self.assertIn('VERIFIED',dialog.chat.toPlainText())
        self.assertEqual(dialog._history,[])
        dialog._request_progress(dialog._request_id,{'kind':'reset'})
        self.assertEqual(dialog.chat.toPlainText(),baseline)

    def test_intermediate_segment_rechecks_source_revision(self):
        source=self.source();dialog=self.dialog()
        self.store.save_note(source['note_id'],'changed','replacement',None,False,'')
        dialog._request_progress(dialog._request_id,{'kind':'segment','text':'STALE-FACT','sources':[source]})
        self.assertNotIn('STALE-FACT',dialog.chat.toPlainText())
        self.assertEqual(dialog._history,[])

    def test_verified_segments_do_not_pull_reader_from_earlier_messages(self):
        dialog=self.dialog();dialog.chat.setPlainText('Earlier message\n'*300)
        bar=dialog.chat.verticalScrollBar();bar.setValue(0)
        dialog._request_progress(dialog._request_id,{'kind':'segment','text':'NEW-VERIFIED','sources':[]})
        QTest.qWait(150)
        self.assertEqual(bar.value(),0)
        self.assertIn('NEW-VERIFIED',dialog.chat.toPlainText())

    def test_worker_publishes_verified_segment_before_final_packet_and_cancel_retracts(self):
        release=threading.Event()
        class StreamingEngine:
            supports_stream_segments=True
            def ask(self,query,workspace_id,*,on_segment,cancel_event,**kwargs):
                on_segment({'kind':'segment','text':'INTERMEDIATE-VERIFIED','sources':[]})
                while not release.wait(.01):
                    if cancel_event.is_set():raise RuntimeError('Cancelled')
                return {'text':'INTERMEDIATE-VERIFIED final','sources':[]}
        dialog=self.dialog(factory=lambda path:StreamingEngine())
        dialog.config['provider']='search'
        dialog.input.setPlainText('Question');self.assertTrue(dialog.send())
        self.wait(lambda:'INTERMEDIATE-VERIFIED' in dialog.chat.toPlainText())
        self.assertTrue(dialog.busy);self.assertEqual(dialog._history,[])
        self.assertTrue(dialog._typing_timer.isActive())
        dialog.cancel();self.wait(lambda:not dialog.busy)
        self.assertNotIn('INTERMEDIATE-VERIFIED',dialog.chat.toPlainText())
        self.assertEqual(dialog._history,[])
        self.assertFalse(dialog._typing_timer.isActive())

    def test_typing_indicator_timer_stops_after_error(self):
        dialog=self.dialog();dialog._set_typing(True)
        self.assertTrue(dialog._typing_timer.isActive())
        dialog._render_completed(dialog._request_id,None,'network error','ask')
        self.assertFalse(dialog._typing_timer.isActive())
        self.assertTrue(dialog.typing_indicator.isHidden())


if __name__=='__main__':unittest.main()

"""Qt companion tests use fake engines, temporary notes and no model/network calls."""
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import Qt,QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication,QMainWindow,QPushButton
from app.database.store import Store
from app.services.memory_store import MemoryStore
from app.ui.companion import CompanionDialog,DEFAULT_CONFIG,_ACTIVE_DIALOGS,validate_config


class CompanionUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        telemetry=patch('app.services.local_runtime.resource_advisory',return_value={'warning':None})
        telemetry.start()
        self.addCleanup(telemetry.stop)
        scratch=Path(__file__).resolve().parents[3]/'work';scratch.mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(prefix='companion-ui-',dir=scratch)
        self.store=Store(Path(self.temp.name)/'notes.sqlite3')
        self.parent=QMainWindow();self.parent.resize(1120,760);self.parent.show()
        self.dialogs=[];self.factories=[];self.calls=[];self.thread_ids=[]
        self.main_thread=threading.get_ident()
        self.response={'text':'Результат поиска, без выдуманной памяти.','sources':[],'engine_label':'Поиск без модели'}
        self.started=threading.Event();self.release=threading.Event();self.slow=False;self.error=None
        self.memory=MemoryStore(self.store.path)
        self.workspaces=self.memory.list_workspaces()

    def tearDown(self):
        self.release.set()
        for dialog in self.dialogs:
            dialog.request_close()
            self.wait(lambda:not dialog.busy)
            dialog.deleteLater()
        self.parent.close();self.parent.deleteLater();self.app.processEvents()
        self.memory.close();self.store.db.close();self.temp.cleanup()

    def wait(self,predicate,timeout=3):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            self.app.processEvents()
            if predicate():return
            QTest.qWait(5)
        self.fail('Asynchronous request did not complete in time')

    def factory(self,path):
        self.factories.append(Path(path));self.thread_ids.append(threading.get_ident())
        suite=self
        class Engine:
            def ask(self,query,workspace_id,**kwargs):
                suite.calls.append((query,workspace_id,kwargs));suite.started.set()
                if suite.slow:suite.release.wait(2)
                if suite.error:raise suite.error
                return dict(suite.response)
            def status(self,config):
                suite.calls.append(('status',config));return {'available':True,'model_ready':True,'models':['qwen3:1.7b'],'error':None}
        return Engine()

    def dialog(self,factory=None):
        dialog=CompanionDialog(self.store,self.parent,engine_factory=factory or self.factory)
        self.dialogs.append(dialog);dialog.show();self.app.processEvents();return dialog

    def send(self,dialog,text='Найди контакт'):
        dialog.input.setPlainText(text);self.assertTrue(dialog.send());self.wait(lambda:not dialog.busy)

    def source(self):
        note=self.store.create_note();self.store.save_note(note,'Контакт','<p>Телефон 123 🧡</p>',None,False,'')
        self.memory.sync(self.workspaces[0]['id'])
        row=self.memory.search('Телефон',workspace_id=self.workspaces[0]['id'])[0]
        return {'note_id':note,'title':row['title'],'revision':row['revision'],'excerpt':row['chunk'],'workspace_id':row['workspace_id']}

    def test_first_use_is_truthful_nonmodal_no_launch_or_typing_requests(self):
        geometry=self.parent.geometry();dialog=self.dialog()
        self.assertFalse(dialog.isModal());self.assertEqual(self.parent.geometry(),geometry)
        self.assertIn('без модели',dialog.chat.toPlainText())
        self.assertIn('Личное',dialog.workspace.itemText(0))
        self.assertFalse(dialog.include_trash.isChecked());self.assertTrue(dialog.include_archive.isChecked())
        dialog.input.setPlainText('some typing');self.app.processEvents()
        self.assertEqual(self.factories,[]);self.assertEqual(self.calls,[])
        self.assertFalse(dialog.settings_path.exists())
        dialog.input.clear();self.assertFalse(dialog.send());self.assertEqual(self.calls,[])

    def test_deadline_message_discards_late_model_result(self):
        self.slow=True
        dialog=self.dialog();dialog.input.setPlainText('Обсудим план')
        self.assertTrue(dialog.send());self.wait(self.started.is_set)
        dialog._deadline_expired();self.release.set();self.wait(lambda:not dialog.busy)
        self.assertIn('30 секунд',dialog.chat.toPlainText())
        self.assertEqual(dialog._history,[])
        self.assertNotIn(self.response['text'],dialog.chat.toPlainText())
        self.assertFalse(dialog._deadline_timer.isActive())

    def test_first_user_guidance_modes_and_fresh_conversation(self):
        dialog=self.dialog()
        self.assertIn('Создай заметку',dialog.chat.toPlainText())
        self.assertEqual(dialog.mode.currentData(),'discuss')
        self.assertIn('Личные и рабочие',dialog.scope_hint.text())
        dialog.mode.setCurrentIndex(dialog.mode.findData('audit'))
        self.assertIn('не проверка всей базы',dialog.mode_hint.text())
        dialog.mode.setCurrentIndex(dialog.mode.findData('memory'))
        self.send(dialog,'Найди поездку')
        self.assertTrue(dialog._history)
        QTest.mouseClick(dialog.new_chat_button,Qt.LeftButton)
        self.assertEqual(dialog._history,[])
        self.assertEqual(dialog.source_buttons,[])
        self.assertIn('Привет!',dialog.chat.toPlainText())
        self.assertEqual(len(self.calls),1)

    def test_draft_requires_explicit_create_and_scope_switch_discards(self):
        from uuid import uuid4
        dialog=self.dialog();draft={'draft_id':uuid4().hex,'kind':'create_note','title':'Черновик','body':'Проверить почту','once_at':None,'workspace_id':dialog.workspace_id}
        count=self.store.db.execute('select count(*) from notes').fetchone()[0]
        dialog._show_draft(draft);self.assertTrue(dialog.draft_panel.isVisible())
        self.assertEqual(self.store.db.execute('select count(*) from notes').fetchone()[0],count)
        dialog.draft_title.setText('Изменённый заголовок');created=[];dialog.note_created.connect(created.append)
        self.assertTrue(dialog._commit_draft());self.assertEqual(len(created),1)
        self.assertEqual(self.store.db.execute('select title from notes where id=?',(created[0],)).fetchone()[0],'Изменённый заголовок')
        self.assertFalse(dialog._commit_draft());self.assertEqual(len(created),1)
        dialog._show_draft({**draft,'draft_id':uuid4().hex});dialog._scope_changed();self.assertIsNone(dialog._action_draft)

    def test_mini_pet_click_keyboard_drag_and_resize_bounds(self):
        from app.widgets.companion_mascot import MiniCompanion
        from PySide6.QtCore import QPoint
        from PySide6.QtWidgets import QSplitter,QWidget
        splitter=QSplitter();splitter.addWidget(QWidget());splitter.addWidget(QWidget());self.parent.setCentralWidget(splitter)
        pet=MiniCompanion(self.parent);self.app.processEvents();self.assertEqual(splitter.count(),2)
        activated=[];pet.activated.connect(lambda:activated.append(True))
        QTest.mouseClick(pet,Qt.LeftButton,pos=QPoint(70,80));self.assertEqual(len(activated),1)
        QTest.keyClick(pet,Qt.Key_Return);self.assertEqual(len(activated),2)
        QTest.mousePress(pet,Qt.LeftButton,pos=QPoint(70,80));QTest.mouseMove(pet,QPoint(5,10));QTest.mouseRelease(pet,Qt.LeftButton,pos=QPoint(5,10));self.assertEqual(len(activated),2)
        self.parent.resize(500,400);self.app.processEvents();self.assertGreaterEqual(pet.x(),0);self.assertGreaterEqual(pet.y(),0)
        self.assertLessEqual(pet.geometry().right(),self.parent.width());self.assertLessEqual(pet.geometry().bottom(),self.parent.height())
        QTest.mouseClick(pet.dismiss,Qt.LeftButton);self.assertFalse(pet.isVisible())

    def test_mini_pet_pending_callbacks_die_with_widget(self):
        from app.widgets.companion_mascot import MiniCompanion
        import shiboken6
        pet=MiniCompanion(self.parent);pet._animate()
        self.assertTrue(pet._position_timer.isActive());self.assertTrue(pet._blink_timer.isActive())
        shiboken6.delete(pet)
        self.app.processEvents();QTest.qWait(180)
        self.assertFalse(shiboken6.isValid(pet))

    def test_companion_launcher_is_visible_descriptive_and_clickable(self):
        from app.widgets.companion_mascot import CompanionLauncher
        launcher=CompanionLauncher(self.parent);launcher.show();clicked=[]
        launcher.clicked.connect(lambda:clicked.append(True))
        self.assertFalse(launcher.icon().isNull())
        self.assertIn('Чат по заметкам',launcher.text())
        self.assertIn('ИИ-помощника',launcher.accessibleName())
        self.assertLessEqual(launcher.sizeHint().width(),196)
        QTest.mouseClick(launcher,Qt.LeftButton)
        self.assertEqual(clicked,[True]);launcher.deleteLater()

    def test_request_uses_independent_thread_and_annotations(self):
        dialog=self.dialog();self.send(dialog)
        self.assertNotEqual(self.thread_ids[0],self.main_thread)
        self.assertEqual(self.factories[0],self.store.path)
        self.assertIn('Результат поиска',dialog.chat.toPlainText())
        self.assertEqual(dialog._history[0]['workspace_id'],dialog.workspace_id)
        self.assertEqual(dialog._history[1]['sources'],[])
        self.send(dialog,'Уточни')
        history=self.calls[-1][2]['history']
        self.assertEqual(len(history),2)
        self.assertTrue(all(turn['workspace_id']==dialog.workspace_id for turn in history))
        self.assertTrue(dialog.send_button.isEnabled());self.assertFalse(dialog.cancel_button.isEnabled())

    def test_source_click_revalidates_and_rejects_changed_note(self):
        source=self.source();self.response['sources']=[source]
        dialog=self.dialog();opened=[];dialog.open_note.connect(opened.append)
        self.send(dialog);self.assertEqual(len(dialog.source_buttons),1)
        QTest.mouseClick(dialog.source_buttons[0],Qt.LeftButton);self.assertEqual(opened,[source['note_id']])
        self.store.save_note(source['note_id'],'Changed','new',None,False,'')
        QTest.mouseClick(dialog.source_buttons[0],Qt.LeftButton)
        self.assertEqual(opened,[source['note_id']]);self.assertIn('изменился',dialog.status.text())

    def test_scope_change_drops_late_private_answer_history_and_sources(self):
        self.slow=True;self.response={'text':'PRIVATE-SENTINEL','sources':[],'engine_label':'fake'}
        dialog=self.dialog();dialog.input.setPlainText('личный вопрос');dialog.send()
        self.wait(lambda:self.started.is_set())
        previous_event=dialog._cancel_event
        dialog.set_workspace(self.workspaces[1]['id'])
        self.assertTrue(previous_event.is_set());self.assertEqual(dialog._history,[])
        self.release.set();self.wait(lambda:not dialog.busy)
        self.assertNotIn('PRIVATE-SENTINEL',dialog.chat.toPlainText());self.assertEqual(dialog.source_buttons,[])
        self.slow=False;self.response={'text':'Рабочий ответ','sources':[],'engine_label':'fake'}
        self.send(dialog,'рабочий вопрос')
        self.assertEqual(self.calls[-1][2]['history'],[])
        self.assertEqual(self.calls[-1][1],self.workspaces[1]['id'])

    def test_ui_stays_responsive_cancel_ignores_late_result(self):
        self.slow=True;self.response['text']='LATE-ANSWER'
        dialog=self.dialog();beats=[];timer=QTimer();timer.setInterval(10);timer.timeout.connect(lambda:beats.append(1));timer.start()
        dialog.input.setPlainText('question');dialog.send();self.wait(lambda:self.started.is_set())
        self.wait(lambda:len(beats)>=3,timeout=1);self.assertGreaterEqual(len(beats),3)
        self.assertFalse(dialog.send_button.isEnabled());self.assertFalse(dialog.send())
        dialog.cancel();self.assertTrue(dialog._cancel_event.is_set())
        self.release.set();self.wait(lambda:not dialog.busy);timer.stop()
        self.assertNotIn('LATE-ANSWER',dialog.chat.toPlainText());self.assertEqual(dialog._history,[])

    def test_close_retains_thread_until_finished_and_emits_shutdown_ready(self):
        self.slow=True;dialog=self.dialog();ready=[];dialog.shutdown_ready.connect(lambda:ready.append(True))
        dialog.input.setPlainText('question');dialog.send();self.wait(lambda:self.started.is_set())
        thread=dialog._thread;dialog.request_close()
        self.assertTrue(dialog.busy);self.assertFalse(dialog.isVisible());self.assertIn(dialog,_ACTIVE_DIALOGS)
        self.assertTrue(thread.isRunning());self.assertTrue(dialog._cancel_event.is_set())
        self.release.set();self.wait(lambda:not dialog.busy)
        self.assertEqual(ready,[True]);self.assertNotIn(dialog,_ACTIVE_DIALOGS)

    def test_errors_do_not_add_false_history_or_expose_exception_details(self):
        self.error=OSError('PRIVATE-ERROR-SENTINEL');dialog=self.dialog();self.send(dialog)
        self.assertEqual(dialog._history,[]);self.assertEqual(dialog.source_buttons,[])
        self.assertNotIn('PRIVATE-ERROR-SENTINEL',dialog.chat.toPlainText()+dialog.status.text())
        self.assertIn('не выполнен',dialog.chat.toPlainText())

    def test_settings_local_only_atomic_no_secret_fields_and_explicit_probe(self):
        dialog=self.dialog();self.assertEqual(dialog.config['model'],DEFAULT_CONFIG['model'])
        for url in ['https://127.0.0.1:11434','http://example.com','http://localhost.evil','http://user:password@localhost:11434','http://localhost:11434/?q=1']:
            with self.assertRaises(ValueError):validate_config({**DEFAULT_CONFIG,'base_url':url})
        dialog._save_config({**DEFAULT_CONFIG,'provider':'search'})
        saved=json.loads(dialog.settings_path.read_text(encoding='utf8'))
        self.assertEqual(saved['provider'],'search');self.assertNotIn('api_key',saved)
        self.assertFalse(list(dialog.settings_path.parent.glob('.ai-settings-*')))
        before=dialog.settings_path.read_bytes()
        with patch('app.ui.companion.os.replace',side_effect=PermissionError('locked')):
            with self.assertRaises(PermissionError):dialog._save_config(DEFAULT_CONFIG)
        self.assertEqual(dialog.settings_path.read_bytes(),before)
        dialog.show_settings();self.assertEqual(self.calls,[])
        buttons=dialog._settings_dialog.findChildren(QPushButton)
        check=next(button for button in buttons if button.text()=='Проверить ИИ-комплект')
        QTest.mouseClick(check,Qt.LeftButton);self.wait(lambda:not dialog.busy)
        self.assertEqual(self.calls[-1][0],'status');self.assertIn('готов',dialog.status.text())
        dialog._settings_dialog.close()

    def test_cross_workspace_source_is_rejected_before_display(self):
        dialog=self.dialog();self.response={'text':'FORBIDDEN-RESPONSE','sources':[{'note_id':999,'title':'PRIVATE-TITLE','revision':'x','workspace_id':self.workspaces[1]['id']}],'engine_label':'fake'}
        self.send(dialog)
        self.assertNotIn('FORBIDDEN-RESPONSE',dialog.chat.toPlainText())
        self.assertEqual(dialog.source_buttons,[]);self.assertEqual(dialog._history,[])

    def test_actual_offline_engine_returns_evidence_without_runtime_calls(self):
        from app.services.companion import CompanionEngine
        source=self.source()
        dialog=self.dialog(factory=CompanionEngine)
        dialog.config={**DEFAULT_CONFIG,'provider':'search'}
        with patch('app.services.companion.ensure_runtime',side_effect=AssertionError('Offline search must not start model service')):
            self.send(dialog,'Телефон')
        self.assertIn('без генерации',dialog.status.text())
        self.assertIn('Телефон 123',dialog.chat.toPlainText())
        self.assertEqual(len(dialog.source_buttons),1)
        self.assertEqual(dialog._history[-1]['sources'][0]['note_id'],source['note_id'])

    def test_source_click_obeys_current_archive_and_trash_opt_in(self):
        source=self.source();self.response['sources']=[source]
        dialog=self.dialog();opened=[];dialog.open_note.connect(opened.append)
        self.send(dialog)
        self.store.execute('UPDATE notes SET archived=1 WHERE id=?',(source['note_id'],))
        dialog.include_archive.setChecked(False)
        QTest.mouseClick(dialog.source_buttons[0],Qt.LeftButton)
        self.assertEqual(opened,[])
        dialog.include_archive.setChecked(True)
        QTest.mouseClick(dialog.source_buttons[0],Qt.LeftButton)
        self.assertEqual(opened,[source['note_id']])
        self.store.execute('UPDATE notes SET deleted=1,archived=0 WHERE id=?',(source['note_id'],))
        QTest.mouseClick(dialog.source_buttons[0],Qt.LeftButton)
        self.assertEqual(opened,[source['note_id']])
        dialog.include_trash.setChecked(True)
        QTest.mouseClick(dialog.source_buttons[0],Qt.LeftButton)
        self.assertEqual(opened,[source['note_id'],source['note_id']])

    def test_first_setup_explains_optional_pack_and_exact_missing_models(self):
        dialog=self.dialog();dialog.show_settings()
        self.assertEqual(self.factories,[])
        from PySide6.QtWidgets import QLabel
        labels=' '.join(label.text() for label in dialog._settings_dialog.findChildren(QLabel))
        self.assertIn('готовый комплект',labels)
        self.assertIn('runtime',labels)
        self.assertIn('ничего не отправляет в облако',labels)
        self.assertIn('ещё не проверен',labels)
        missing=dialog._setup_message({'available':True,'model_ready':False,'models':[]},DEFAULT_CONFIG)
        self.assertIn(DEFAULT_CONFIG['model'],missing)
        self.assertIn(DEFAULT_CONFIG['embedding_model'],missing)
        partial=dialog._setup_message({'available':True,'model_ready':True,'models':[DEFAULT_CONFIG['model']]},DEFAULT_CONFIG)
        self.assertIn('Разговор готов',partial)
        self.assertIn(DEFAULT_CONFIG['embedding_model'],partial)
        ready=dialog._setup_message({'available':True,'model_ready':True,'models':[DEFAULT_CONFIG['model'],DEFAULT_CONFIG['embedding_model']]},DEFAULT_CONFIG)
        self.assertIn('комплект готов',ready)
        unavailable=dialog._setup_message({'available':False,'models':[]},DEFAULT_CONFIG)
        self.assertIn('Поиск по заметкам продолжает работать',unavailable)


if __name__=='__main__':unittest.main(verbosity=2)

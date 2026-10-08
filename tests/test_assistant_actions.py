import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from app.database.store import Store, plain_body
from app.services.assistant_actions import intent_response, commit_action_draft
from app.services.companion import CompanionEngine
from unittest.mock import patch
import json


class AssistantActionsTests(unittest.TestCase):
    now = datetime(2026, 10, 4, 12)

    def test_greeting_never_calls_model_or_database(self):
        engine=CompanionEngine('not-a-real-database',client_factory=lambda **kw:self.fail('Model invoked'))
        response=engine.ask('Привет!')
        self.assertIn('Привет',response['text'])
        self.assertEqual(response['sources'],[])

    def test_empty_discussion_uses_no_source_schema(self):
        class Client:
            modes=[]
            def probe(self):return {'available':True,'model_ready':True,'models':[]}
            def chat(self,messages,**kwargs):
                self.modes.append(kwargs['json_mode'])
                if len(self.modes)==1:return json.dumps({'segments':[{'kind':'fact','text':'Ошибка','source_ids':[1],'quotes':[]}]})
                return json.dumps({'segments':[{'kind':'suggestion','text':'Начните с одной задачи.','source_ids':[],'quotes':[]}]})
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'notes.db')
            store.db.close()
            client=Client()
            with patch('app.services.companion.ensure_runtime',return_value=True):
                answer=CompanionEngine(Path(folder)/'notes.db',client_factory=lambda **kw:client).ask('Помоги составить план',mode='discuss')
            self.assertEqual(client.modes,['dialogue','dialogue'])
            self.assertEqual(answer['status'],'answered')

    def test_intro_only_list_is_rejected(self):
        from app.services.local_ai import LocalAIError
        with self.assertRaises(LocalAIError):
            CompanionEngine._validate_dialogue_completeness('Предложение: Вот список:', 'Составь список вещей')
        with self.assertRaises(LocalAIError):
            CompanionEngine._validate_dialogue_completeness('Предложение: Вот что стоит взять.', 'Составь список вещей')
        CompanionEngine._validate_dialogue_completeness('Предложение: Документы\nОдежда\nЗарядка', 'Составь список вещей')
        CompanionEngine._validate_dialogue_completeness('Какая поездка планируется?', 'Составь список вещей')

    def test_date_and_text(self):
        result=intent_response('Создай заметку позвонить врачу завтра в 10:00',1,now=self.now)
        self.assertEqual(result['action_draft']['once_at'],'2026-10-05T10:00:00')
        self.assertEqual(result['action_draft']['body'],'позвонить врачу')

    def test_clarification_and_scope(self):
        history=[{'role':'user','workspace_id':1,'content':'Создай заметку позвонить врачу завтра'},
                 {'role':'assistant','workspace_id':1,'content':'Уточните время напоминания.'}]
        self.assertIn('action_draft',intent_response('в 10:00',1,history,now=self.now))
        self.assertIsNone(intent_response('в 10:00',2,history,now=self.now))

    def test_invalid_time_and_date_can_be_corrected(self):
        for request, reply, correction, expected in [
            ('Создай заметку врач завтра в 25:00','Уточните время от 00:00 до 23:59.','в 10:00','2026-10-05T10:00:00'),
            ('Создай заметку врач 31.02.2027 в 10:00','Уточните дату: такой даты нет.','15.12.2027','2027-12-15T10:00:00'),
            ('Создай заметку врач сегодня в 10:00','Уточните будущую дату и время: указанное время уже прошло.','завтра в 11:00','2026-10-05T11:00:00')]:
            history=[{'role':'user','workspace_id':1,'content':request},{'role':'assistant','workspace_id':1,'content':reply}]
            result=intent_response(correction,1,history,now=self.now)
            self.assertEqual(result['action_draft']['once_at'],expected)
            self.assertIsNone(intent_response(correction,2,history,now=self.now))

    def test_app_workflow_help_never_needs_model(self):
        result=intent_response('Как мне начать вести заметки, чтобы не терять задачи?',1,now=self.now)
        self.assertIn('Ctrl+N',result['text'])
        self.assertIn('Настроить расписание',result['text'])
        self.assertNotIn('action_draft',result)

    def test_ambiguous_invalid_past(self):
        for query in ['Создай заметку тест сегодня в 10:00','Создай заметку тест завтра в 25:00',
                      'Создай заметку тест 31.02.2027 в 10:00','Создай заметку тест 15.12 в 10:00',
                      'Создай заметку тест в пятницу в 10:00','Создай заметку тест ежедневно в 10:00']:
            result=intent_response(query,1,now=self.now)
            self.assertNotIn('action_draft',result,query)
            self.assertIn('Уточните',result['text'])

    def test_explicit_only(self):
        self.assertIsNone(intent_response('Я создал заметку про поездку',1,now=self.now))
        self.assertIsNone(intent_response('Найди заметку купить хлеб',1,now=self.now))

    def test_atomic_idempotent_and_html_safe(self):
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'notes.db')
            draft=intent_response('Создай заметку <script> завтра в 10:00',2,now=self.now)['action_draft']
            self.assertEqual(store.db.execute('SELECT count(*) FROM notes').fetchone()[0],0)
            first=commit_action_draft(store,draft,self.now)
            self.assertEqual(first,commit_action_draft(store,draft,self.now))
            self.assertEqual(store.db.execute('SELECT count(*) FROM notes').fetchone()[0],1)
            note=store.db.execute('SELECT * FROM notes').fetchone()
            self.assertEqual(note['workspace_id'],2)
            self.assertEqual(plain_body(note['body']),'<script>')
            self.assertIsNotNone(store.reminder(first))
            store.db.close()

    def test_transaction_rolls_back_on_reminder_error(self):
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'notes.db')
            store.db.execute("CREATE TRIGGER deny_reminder BEFORE INSERT ON reminders BEGIN SELECT RAISE(ABORT,'test failure'); END")
            store.db.commit()
            draft=intent_response('Создай заметку врач завтра в 10:00',1,now=self.now)['action_draft']
            with self.assertRaises(Exception):commit_action_draft(store,draft,self.now)
            self.assertEqual(store.db.execute('SELECT count(*) FROM notes').fetchone()[0],0)
            store.db.close()


if __name__=='__main__':unittest.main()

"""Independent newcomer and adversarial assistant tests; synthetic databases only."""
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock
from app.services.companion import CompanionEngine
from app.database.store import Store,plain_body
from app.services.memory_store import MemoryStore
from app.services.assistant_actions import intent_response,commit_action_draft

class FirstUserActionsTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.store=Store(Path(self.tmp.name)/'notes.sqlite3')
  memory=MemoryStore(self.store.path);memory.close();self.now=datetime(2027,1,2,12,0)
 def tearDown(self):self.store.db.close();self.tmp.cleanup()
 def count(self):return self.store.db.execute('SELECT COUNT(*) FROM notes').fetchone()[0]
 def test_greeting_and_gibberish_do_not_claim_missing_notes_or_write(self):
  for query in ('Привет!','спасибо','фыв','как пользоваться'):
   response=intent_response(query,1,now=self.now)
   self.assertIsNotNone(response);self.assertNotIn('action_draft',response)
   self.assertNotIn('не нашёл',response['text']);self.assertEqual(self.count(),0)
 def test_engine_greeting_does_not_start_model_or_create_records(self):
  factory=Mock(side_effect=AssertionError('Greeting should not start model'))
  engine=CompanionEngine(self.store.path,client_factory=factory)
  result=engine.ask('Привет!',config={'provider':'search'})
  self.assertIn('Привет',result['text']);self.assertEqual(result['sources'],[]);factory.assert_not_called();self.assertEqual(self.count(),0)
 def test_missing_invalid_past_and_multiple_time_require_clarification(self):
  for query in ('Создай заметку купить хлеб завтра','Создай напоминание купить хлеб в 18:30','Создай заметку купить хлеб 31.02.2027 в 18:30','Создай заметку купить хлеб сегодня в 10:00','Создай заметку купить хлеб завтра в 25:00','Создай заметку купить хлеб завтра в 10:00 и 18:30','Создай заметку купить хлеб 15.12 в 18:30'):
   with self.subTest(query=query):
    response=intent_response(query,1,now=self.now);self.assertNotIn('action_draft',response);self.assertIn('Уточните',response['text']);self.assertEqual(self.count(),0)
 def test_conflicting_dates_do_not_silently_pick_first(self):
  for query in ('Создай заметку купить хлеб 15.12.2027 и 16.12.2027 в 18:30','Создай заметку купить хлеб завтра 15.12.2027 в 18:30'):
   with self.subTest(query=query):
    response=intent_response(query,1,now=self.now);self.assertNotIn('action_draft',response);self.assertIn('Уточните',response['text']);self.assertEqual(self.count(),0)
 def test_preview_and_duplicate_confirmation_are_safe(self):
  response=intent_response('Создай заметку купить хлеб завтра в 18:30',2,now=self.now)
  self.assertEqual(self.count(),0);draft=response['action_draft'];self.assertEqual(draft['once_at'],'2027-01-03T18:30:00')
  first=commit_action_draft(self.store,draft,now=self.now);second=commit_action_draft(self.store,dict(draft),now=self.now)
  self.assertEqual(first,second);self.assertEqual(self.count(),1)
  row=self.store.db.execute('SELECT * FROM notes WHERE id=?',(first,)).fetchone();self.assertEqual(row['workspace_id'],2);self.assertEqual(plain_body(row['body']),'купить хлеб')
  self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM reminders').fetchone()[0],1)
 def test_personal_pending_request_cannot_resume_in_work(self):
  history=[{'workspace_id':1,'role':'user','content':'Создай заметку PRIVATE_CANARY завтра'}, {'workspace_id':1,'role':'assistant','content':'Уточните время'}]
  self.assertIsNone(intent_response('в 18:30',2,history=history,now=self.now));self.assertEqual(self.count(),0)
 def test_escaped_html_and_invalid_workspace(self):
  draft=intent_response('Создай заметку <script>alert(1)</script>',1,now=self.now)['action_draft']
  note=commit_action_draft(self.store,draft,now=self.now)
  html=self.store.db.execute('SELECT body FROM notes WHERE id=?',(note,)).fetchone()[0];self.assertNotIn('<script>',html);self.assertIn('&lt;script&gt;',html)
  draft=dict(draft,draft_id='a'*32,workspace_id=True)
  with self.assertRaises(ValueError):commit_action_draft(self.store,draft,now=self.now)
  self.assertEqual(self.count(),1)
 def test_commit_rechecks_expired_reminder_and_writes_nothing(self):
  draft=intent_response('Создай заметку купить хлеб завтра в 18:30',1,now=self.now)['action_draft']
  with self.assertRaises(ValueError):commit_action_draft(self.store,draft,now=datetime(2027,1,4))
  self.assertEqual(self.count(),0);self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM reminders').fetchone()[0],0)

if __name__=='__main__':unittest.main()

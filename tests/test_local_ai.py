"""Local API contract tests against a loopback fixture server; no model/network download."""
import sys,unittest,json,threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.services.local_ai import OllamaClient,LocalAIError,LocalAICancelled

class LocalAIContract(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.requests=[];cls.redirect=False;cls.remote=False;cls.empty=False
  class Handler(BaseHTTPRequestHandler):
   def log_message(self,*args):pass
   def do_GET(self):self.reply({'models':[{'name':'qwen3:4b'}]})
   def do_POST(self):
    data=json.loads(self.rfile.read(int(self.headers['Content-Length'])));cls.requests.append((self.path,data))
    if cls.redirect:
     self.send_response(302);self.send_header('Location','http://example.com/remote');self.end_headers();return
    if self.path=='/api/show':self.reply({'remote_host':'https://remote.example'} if cls.remote else {})
    elif self.path=='/api/chat':self.reply({'message':{'content':'' if cls.empty else 'Ответ 東京'}})
    elif self.path=='/api/embed':self.reply({'embeddings':[[0.1,0.2] for x in data['input']]})
   def reply(self,data):
    self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(json.dumps(data).encode())
  cls.server=ThreadingHTTPServer(('127.0.0.1',0),Handler);cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
 @classmethod
 def tearDownClass(cls):cls.server.shutdown();cls.server.server_close();cls.thread.join()
 def setUp(self):self.__class__.redirect=False;self.__class__.remote=False;self.__class__.empty=False;self.requests.clear();self.client=OllamaClient('http://127.0.0.1:'+str(self.server.server_port))
 def test_rejects_nonlocal_urls_and_cloud_models(self):
  for url in ['https://127.0.0.1:11434','http://example.com','http://127.0.0.1.evil.test','http://user:pw@127.0.0.1','http://127.0.0.1/api','http://127.0.0.1?key=secret','file:///tmp/model']:
   with self.subTest(url=url),self.assertRaises(LocalAIError):OllamaClient(url)
  with self.assertRaises(LocalAIError):OllamaClient(model='qwen3:cloud')
 def test_probe_chat_json_and_embeddings(self):
  self.assertTrue(self.client.probe()['model_ready']);self.assertEqual(self.client.chat([{'role':'user','content':'Привет'}],json_mode=True),'Ответ 東京')
  payload=self.requests[-1][1];self.assertFalse(payload['stream']);self.assertFalse(payload['think']);self.assertEqual(payload['format']['type'],'object');self.assertEqual(payload['format']['properties']['segments']['items']['required'],['kind','text','source_ids','quotes']);self.assertFalse(payload['format']['additionalProperties']);self.assertEqual(self.client.embed(['A','B']),[[0.1,0.2],[0.1,0.2]])
 def test_redirect_and_remote_model_fail_closed(self):
  self.__class__.redirect=True
  with self.assertRaises(LocalAIError):self.client.chat([{'role':'user','content':'never remote'}])
  self.__class__.redirect=False;self.__class__.remote=True
  with self.assertRaises(LocalAIError):self.client.chat([{'role':'user','content':'never remote'}])
  self.assertFalse(any(path=='/api/chat' for path,data in self.requests))
 def test_general_dialogue_schema_cannot_invent_sources(self):
  self.client.chat([{'role':'user','content':'Помоги составить список'}],json_mode='dialogue')
  properties=self.requests[-1][1]['format']['properties']['segments']['items']['properties']
  self.assertEqual(properties['kind']['enum'],['suggestion','question'])
  self.assertEqual(properties['source_ids']['maxItems'],0)
  self.assertEqual(properties['quotes']['maxItems'],0)
  self.client.chat([{'role':'user','content':'Найди запись'}],json_mode=True)
  evidence=self.requests[-1][1]['format']['properties']['segments']['items']['properties']
  self.assertIn('fact',evidence['kind']['enum'])
  self.assertNotIn('maxItems',evidence['source_ids'])
 def test_cancel_and_empty_answer(self):
  cancel=threading.Event();cancel.set()
  with self.assertRaises(LocalAICancelled):self.client.chat([{'role':'user','content':'x'}],cancel)
  self.assertEqual(self.requests,[]);self.__class__.empty=True
  with self.assertRaises(LocalAIError):self.client.chat([{'role':'user','content':'x'}])
 def test_dialogue_list_requires_multiple_segments_without_mutating_other_requests(self):
  self.client.chat([{'role':'user','content':'Составь список вещей'}],json_mode='dialogue')
  self.assertEqual(self.requests[-1][1]['format']['properties']['segments']['minItems'],3)
  self.client.chat([{'role':'user','content':'Обсудим идею'}],json_mode='dialogue')
  self.assertEqual(self.requests[-1][1]['format']['properties']['segments']['minItems'],1)
 def test_truncated_output_is_not_accepted_as_finished_answer(self):
  from unittest.mock import patch
  with patch.object(self.client,'_ensure_installed_local'),patch.object(self.client,'_request',return_value={'message':{'content':'unfinished'},'done_reason':'length'}):
   with self.assertRaisesRegex(LocalAIError,'оборвался'):self.client.chat([{'role':'user','content':'x'}])


class RuntimeStartupContract(unittest.TestCase):
 def test_reuses_loading_process_instead_of_duplicate(self):
  from unittest.mock import Mock,patch
  from app.services import local_runtime
  client=Mock();client.base_url='http://127.0.0.1:11434';client.probe.side_effect=[{'available':False},{'available':False},{'available':True}]
  process=Mock();process.poll.return_value=None
  with patch.object(local_runtime,'_process',process),patch.object(Path,'is_file',return_value=True),patch.object(local_runtime.subprocess,'Popen') as spawn:
   self.assertTrue(local_runtime.ensure_runtime(client));spawn.assert_not_called()
 def test_existing_endpoint_is_left_untouched(self):
  from unittest.mock import Mock,patch
  from app.services import local_runtime
  client=Mock();client.probe.return_value={'available':True}
  with patch.object(local_runtime.subprocess,'Popen') as spawn:self.assertTrue(local_runtime.ensure_runtime(client));spawn.assert_not_called()

if __name__=='__main__':unittest.main()

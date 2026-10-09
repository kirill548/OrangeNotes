import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from app.services.local_ai import OllamaClient, LocalAIError, LocalAICancelled
from app.services.ollama_stream import NDJSONAnswer, MAX_LINE, MAX_RESPONSE


class NDJSONContracts(unittest.TestCase):
    def test_fragmented_utf8_and_final_marker(self):
        parser=NDJSONAnswer()
        raw=(json.dumps({'message':{'content':'Привет'}},ensure_ascii=False)+'\n'+json.dumps({'done':True})+'\n').encode()
        for byte in raw:parser.feed(bytes([byte]))
        self.assertEqual(parser.finish()['message']['content'],'Привет')

    def test_unfinished_and_trailing_packets_fail(self):
        parser=NDJSONAnswer();parser.feed(b'{"message":{"content":"partial"}}\n')
        with self.assertRaises(LocalAIError):parser.finish()
        parser=NDJSONAnswer()
        with self.assertRaises(LocalAIError):parser.feed(b'{"done":true}\n{"done":true}\n')

    def test_line_limit_and_global_limit(self):
        with self.assertRaises(LocalAIError):NDJSONAnswer().feed(b'x'*(MAX_LINE+1))
        parser=NDJSONAnswer();parser.size=MAX_RESPONSE
        with self.assertRaises(LocalAIError):parser.feed(b'x')

    def test_single_deadline_covers_preflight_and_stream_and_restores_client(self):
        from unittest.mock import patch
        client=OllamaClient()
        budgets=[]
        def preflight(model):budgets.append(client.deadline)
        def stream(payload,cancel):
            budgets.append(client.deadline)
            return {'message':{'content':'bounded'},'done_reason':'stop'}
        start=time.monotonic()
        with patch.object(client,'_ensure_installed_local',side_effect=preflight),patch.object(client,'_stream_request',side_effect=stream):
            self.assertEqual(client.chat_stream([{'role':'user','content':'x'}]),'bounded')
        self.assertEqual(budgets[0],budgets[1])
        self.assertLessEqual(budgets[0]-start,30.01)
        self.assertIsNone(client.deadline)
        client.deadline=start-1
        with self.assertRaises(LocalAIError):client.chat_stream([{'role':'user','content':'x'}])
        self.assertEqual(client.deadline,start-1)

    def test_bad_types_error_and_malformed_json(self):
        for line in (b'[]\n',b'{bad}\n',b'{"done":"true"}\n',b'{"error":"OOM"}\n'):
            with self.subTest(line=line),self.assertRaises(LocalAIError):NDJSONAnswer().feed(line)


class QtStreamContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app=QApplication.instance() or QApplication([])
        cls.slow=False
        cls.mode="normal"
        cls.chat_started=threading.Event()
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                data=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                if self.path=='/api/chat' and cls.mode=='redirect':
                    self.send_response(302);self.send_header('Location','http://example.com');self.end_headers();return
                self.send_response(200);self.end_headers()
                if self.path=='/api/show':self.wfile.write(b'{}');return
                cls.payload=data
                cls.chat_started.set()
                if cls.mode=='partial_slow':
                    try:
                        self.wfile.write(b'{"message":{"content":"RAW_UNVALIDATED_CANARY"}}\n')
                        self.wfile.flush()
                        time.sleep(1)
                    except (BrokenPipeError,ConnectionResetError):pass
                    return
                if cls.slow:time.sleep(1)
                if cls.mode=='overflow':
                    try:self.wfile.write(b'x'*(MAX_LINE+1))
                    except (BrokenPipeError,ConnectionResetError):pass
                    return
                try:self.wfile.write(b'{"message":{"content":"verified"}}\n{"done":true}\n')
                except (BrokenPipeError,ConnectionResetError):pass
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join()

    def test_qt_stream_and_cancel_abort(self):
        client=OllamaClient('http://127.0.0.1:'+str(self.server.server_port))
        self.__class__.slow=False
        self.assertEqual(client.chat_stream([{'role':'user','content':'x'}]),'verified')
        self.assertTrue(self.payload['stream'])
        self.__class__.slow=True
        cancel=threading.Event();timer=threading.Timer(.05,cancel.set);timer.start()
        start=time.monotonic()
        with self.assertRaises(LocalAICancelled):client.chat_stream([{'role':'user','content':'x'}],cancel)
        self.assertLess(time.monotonic()-start,.5)
        timer.join();self.__class__.slow=False

    def test_qt_deadline_redirect_and_overflow(self):
        client=OllamaClient('http://127.0.0.1:'+str(self.server.server_port))
        try:
            for mode in ('redirect','overflow'):
                self.__class__.mode=mode
                with self.subTest(mode=mode),self.assertRaises(LocalAIError):
                    client.chat_stream([{'role':'user','content':'x'}])
            self.__class__.mode='normal';self.__class__.slow=True
            client.deadline=time.monotonic()+.08
            start=time.monotonic()
            with self.assertRaises(LocalAIError):client.chat_stream([{'role':'user','content':'x'}])
            self.assertLess(time.monotonic()-start,.5)
        finally:
            self.__class__.mode='normal';self.__class__.slow=False

    def test_real_request_thread_qt_transport_cancel_keeps_chat_history_clean(self):
        from unittest.mock import patch
        telemetry=patch('app.services.local_runtime.resource_advisory',return_value={'warning':None})
        telemetry.start();self.addCleanup(telemetry.stop)
        import tempfile
        from pathlib import Path
        from PySide6.QtCore import QTimer,QCoreApplication,QEvent
        from PySide6.QtTest import QTest
        from app.database.store import Store
        from app.ui.companion import CompanionDialog
        worker_ids=[]
        base_url='http://127.0.0.1:'+str(self.server.server_port)
        main_id=threading.get_ident()
        class Engine:
            def ask(self,query,workspace_id,**kwargs):
                worker_ids.append(threading.get_ident())
                text=OllamaClient(base_url).chat_stream([{'role':'user','content':query}],kwargs['cancel_event'])
                return {'text':text,'sources':[],'engine_label':'fixture'}
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(Path(tmp)/'notes.sqlite3')
            dialog=CompanionDialog(store,engine_factory=lambda path:Engine())
            beats=[]
            heartbeat=QTimer();heartbeat.setInterval(5);heartbeat.timeout.connect(lambda:beats.append(1))
            self.__class__.mode='partial_slow';self.chat_started.clear()
            try:
                dialog.input.setPlainText('fixture request');heartbeat.start()
                self.assertTrue(dialog.send())
                deadline=time.monotonic()+2
                while not self.chat_started.is_set() and time.monotonic()<deadline:QTest.qWait(5)
                self.assertTrue(self.chat_started.is_set())
                QTest.qWait(50)
                self.assertNotIn('RAW_UNVALIDATED_CANARY',dialog.chat.toPlainText())
                start=time.monotonic();dialog.cancel()
                while dialog.busy and time.monotonic()-start<1:QTest.qWait(5)
                self.assertFalse(dialog.busy)
                self.assertLess(time.monotonic()-start,.5)
                self.assertGreater(len(beats),2)
                self.assertNotEqual(worker_ids,[main_id])
                self.assertEqual(len(worker_ids),1)
                self.assertEqual(dialog._history,[])
                self.assertNotIn('RAW_UNVALIDATED_CANARY',dialog.chat.toPlainText())
            finally:
                heartbeat.stop();self.__class__.mode='normal'
                dialog.request_close()
                if dialog._thread is not None:
                    dialog._thread.wait(2000)
                    QTest.qWait(60)
                dialog.deleteLater()
                QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)
                self.app.processEvents();store.db.close()

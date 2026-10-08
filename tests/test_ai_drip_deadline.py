"""Real loopback HTTP body drips must not monopolise the assistant worker."""
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from app.services.local_ai import OllamaClient, LocalAIError, LocalAICancelled


class DripDeadline(unittest.TestCase):
    def setUp(self):
        class Drip(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', '100000')
                self.end_headers()
                try:
                    for _ in range(150):
                        self.wfile.write(b' ')
                        self.wfile.flush()
                        time.sleep(.025)
                except ConnectionError:
                    pass
            def log_message(self, *args):
                pass
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Drip)
        self.server.daemon_threads = True
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.client = OllamaClient('http://127.0.0.1:' + str(self.server.server_port))

    def test_drip_respects_total_deadline_and_probe_fallback(self):
        self.client.deadline = time.monotonic() + .18
        start = time.monotonic()
        result = self.client.probe()
        self.assertFalse(result['available'])
        self.assertTrue(result['error'])
        self.assertLess(time.monotonic() - start, .8)

    def test_cancel_interrupts_drip_between_reads(self):
        event = threading.Event()
        timer = threading.Timer(.12, event.set)
        timer.start()
        self.addCleanup(timer.cancel)
        start = time.monotonic()
        with self.assertRaises(LocalAICancelled):
            self.client._request('/drip', cancel_event=event)
        self.assertLess(time.monotonic() - start, .8)

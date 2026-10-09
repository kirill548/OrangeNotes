import json
import unittest
from app.services.local_ai import LocalAIError
from app.services.ollama_stream import NDJSONAnswer, MAX_LINE


class StreamTransportCleanupTests(unittest.TestCase):
    @staticmethod
    def packet(content='', done=False):
        return (json.dumps({'message': {'content': content}, 'done': done})+'\n').encode()

    def test_disconnect_mid_sentence_discards_private_text(self):
        parser=NDJSONAnswer()
        parser.feed(self.packet('Sensitive unfinished fact'))
        with self.assertRaisesRegex(LocalAIError, 'завершения'):
            parser.finish()
        self.assertTrue(parser.aborted)
        self.assertEqual(parser.parts, [])
        self.assertEqual(parser.pending, bytearray())

    def test_explicit_abort_discards_partial_json_and_rejects_reuse(self):
        parser=NDJSONAnswer()
        parser.feed(self.packet('Private fact')+b'{"message":')
        parser.abort()
        parser.abort()
        self.assertEqual(parser.parts, [])
        self.assertEqual(parser.pending, bytearray())
        with self.assertRaises(LocalAIError):parser.feed(self.packet(done=True))
        with self.assertRaises(LocalAIError):parser.finish()

    def test_protocol_error_discards_preceding_fact(self):
        parser=NDJSONAnswer()
        with self.assertRaises(LocalAIError):
            parser.feed(self.packet('Private fact')+b'{invalid}\n')
        self.assertEqual(parser.parts, [])
        self.assertEqual(parser.pending, bytearray())

    def test_oversized_line_discards_preceding_fact(self):
        parser=NDJSONAnswer()
        parser.feed(self.packet('Private fact'))
        with self.assertRaises(LocalAIError):parser.feed(b'x'*(MAX_LINE+1))
        self.assertEqual(parser.parts, [])
        self.assertEqual(parser.pending, bytearray())

    def test_success_releases_buffer_but_returns_complete_packet(self):
        parser=NDJSONAnswer()
        parser.feed(self.packet('First sentence. ')+self.packet('Second sentence.',True))
        self.assertEqual(parser.finish()['message']['content'],'First sentence. Second sentence.')
        self.assertEqual(parser.parts, [])
        self.assertEqual(parser.pending, bytearray())

    def test_empty_progress_packets_do_not_accumulate_string_entries(self):
        parser=NDJSONAnswer()
        for _ in range(10000):parser.feed(self.packet())
        self.assertEqual(parser.parts, [])
        parser.feed(self.packet('Complete.',True))
        self.assertEqual(parser.finish()['message']['content'],'Complete.')

    def test_private_callback_only_receives_valid_string_packets(self):
        observed=[]
        parser=NDJSONAnswer(observed.append)
        raw=self.packet('Private fact')
        parser.feed(raw[:5])
        self.assertEqual(observed, [])
        parser.feed(raw[5:])
        self.assertEqual(observed, ['Private fact'])
        with self.assertRaises(LocalAIError):parser.feed(b'{"message":{"content":42}}\n')
        self.assertEqual(observed, ['Private fact'])

    def test_callback_exception_discards_buffers_and_propagates(self):
        def fail(content):raise RuntimeError('validator failed')
        parser=NDJSONAnswer(fail)
        with self.assertRaisesRegex(RuntimeError,'validator failed'):
            parser.feed(self.packet('Private fact'))
        self.assertTrue(parser.aborted)
        self.assertEqual(parser.parts, [])

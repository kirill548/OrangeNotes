"""Streaming safety contracts, runnable by unittest and pytest."""
import gc
import json
import tracemalloc
import unittest

from app.services.local_ai import LocalAIError
from app.services.ollama_stream import NDJSONAnswer
from app.services.stream_validator import StreamSegmentValidator


class StreamValidatorTests(unittest.TestCase):
    sources = [{'excerpt': 'Позвонить Марии завтра в 09:00. Договор подписан.'}]

    def fact(self, quote='Позвонить Марии завтра в 09:00.', text='FABRICATED_PARAPHRASE'):
        return {'kind': 'fact', 'text': text, 'source_ids': [1],
                'quotes': [{'source_id': 1, 'quote': quote}]}

    def packet(self, segment=None):
        return json.dumps({'segments': [segment or self.fact()]}, ensure_ascii=False)

    def test_syntax_boundaries_and_reconstruction(self):
        validator = StreamSegmentValidator()
        text = 'Первое. Второе! Вопрос?\n' + 'а' * 110
        segments = validator.segment_verified(text)
        self.assertEqual(''.join(segments), text)
        self.assertEqual(segments[:4], ['Первое.', ' Второе!', ' Вопрос?', '\n'])
        self.assertTrue(all(0 < len(segment) <= 48 for segment in segments))

    def test_chunk_configuration_is_between_40_and_50(self):
        for size in (40, 48, 50):
            validator = StreamSegmentValidator(chunk_size=size)
            self.assertEqual([size, size, 3], [len(s) for s in validator.segment_verified('x' * (size * 2 + 3))])
        for size in (0, 39, 51):
            with self.assertRaises(ValueError):
                StreamSegmentValidator(chunk_size=size)

    def test_raw_sentences_and_partial_json_never_escape(self):
        for text in ('Секретный пароль 1234.\n', '{"segments":[{"kind":"fact","text":"invented.'):
            validator = StreamSegmentValidator(sources=self.sources)
            self.assertEqual(validator.feed(text), [])
            with self.assertRaises(LocalAIError):
                validator.finish(self.sources, strict_memory=True)

    def test_grounded_complete_object_emits_before_final_root(self):
        validator = StreamSegmentValidator(sources=self.sources)
        packet = self.packet()
        emitted = []
        for character in packet[:-2]:
            emitted.extend(validator.feed(character))
        self.assertIn('Позвонить Марии', ''.join(emitted))
        self.assertNotIn('FABRICATED_PARAPHRASE', ''.join(emitted))
        self.assertIn('[1]', ''.join(emitted))
        validator.feed(packet[-2:])
        self.assertEqual(''.join(validator.finish(self.sources, strict_memory=True)), ''.join(emitted))

    def test_invented_quote_is_rejected_before_emission(self):
        validator = StreamSegmentValidator(sources=self.sources)
        with self.assertRaises(LocalAIError):
            validator.feed(self.packet(self.fact(quote='Мария передала пароль 1234')))

    def test_revision_change_rejects_partial_fact(self):
        validator = StreamSegmentValidator(sources=self.sources, revision_check=lambda: False)
        with self.assertRaises(LocalAIError):
            validator.feed(self.packet())

    def test_control_tokens_cannot_be_displayed(self):
        for text in ('hello <|assistant', 'answer <think>', 'abc\x00def'):
            with self.subTest(text=text), self.assertRaises(LocalAIError):
                StreamSegmentValidator().segment_verified(text)

    def test_inference_is_not_published_without_full_validation(self):
        segment = {'kind': 'inference', 'text': 'Возможно, нужна встреча.', 'source_ids': [1], 'quotes': []}
        validator = StreamSegmentValidator(sources=self.sources)
        self.assertEqual(validator.feed(self.packet(segment)), [])
        with self.assertRaises(LocalAIError):
            validator.finish(self.sources, strict_memory=True)

    def test_abort_clears_buffers_and_is_terminal(self):
        validator = StreamSegmentValidator(sources=self.sources)
        validator.feed('{"segments":[')
        self.assertGreater(validator.buffered_chars, 0)
        validator.abort()
        self.assertEqual(validator.buffered_chars, 0)
        self.assertEqual(validator.feed(self.packet()), [])
        self.assertEqual(validator.finish(self.sources), [])

    def test_overflow_discards_private_buffer(self):
        validator = StreamSegmentValidator(max_buffer_chars=64)
        validator.feed('x' * 60)
        with self.assertRaises(LocalAIError):
            validator.feed('y' * 5)
        self.assertEqual(validator.buffered_chars, 0)

    def test_many_tiny_chunks_release_memory_after_abort(self):
        tracemalloc.start()
        try:
            gc.collect()
            baseline = tracemalloc.get_traced_memory()[0]
            validator = StreamSegmentValidator(max_buffer_chars=512 * 1024)
            for _ in range(100_000):
                self.assertEqual(validator.feed('x'), [])
            self.assertEqual(validator.buffered_chars, 100_000)
            self.assertLess(tracemalloc.get_traced_memory()[0] - baseline, 2 * 1024 * 1024)
            validator.abort()
            gc.collect()
            self.assertLess(tracemalloc.get_traced_memory()[0] - baseline, 128 * 1024)
        finally:
            tracemalloc.stop()

    def test_ndjson_callback_only_exposes_grounded_segments(self):
        validator = StreamSegmentValidator(sources=self.sources)
        emitted = []
        parser = NDJSONAnswer(on_content=lambda token: emitted.extend(validator.feed(token)))
        packet = self.packet()
        for token in (packet[:20], packet[20:-2]):
            parser.feed((json.dumps({'message': {'content': token}}, ensure_ascii=False) + '\n').encode())
        self.assertIn('Позвонить Марии', ''.join(emitted))
        self.assertFalse(parser.done)
        parser.feed((json.dumps({'message': {'content': packet[-2:]}, 'done': True}) + '\n').encode())
        self.assertEqual(parser.finish()['message']['content'], packet)

    def test_malformed_ndjson_discards_transport_private_text(self):
        parser = NDJSONAnswer()
        parser.feed(b'{"message":{"content":"PRIVATE"}}\n')
        with self.assertRaises(LocalAIError):
            parser.feed(b'{broken}\n')
        self.assertEqual(parser.parts, [])
        self.assertEqual(parser.pending, bytearray())
        with self.assertRaises(LocalAIError):
            parser.finish()

    def test_grounding_error_erases_unvalidated_buffer(self):
        validator=StreamSegmentValidator(sources=self.sources)
        with self.assertRaises(LocalAIError):
            validator.feed(self.packet(self.fact(quote='UNSUPPORTED-CANARY')))
        self.assertEqual(validator.buffered_chars,0)
        self.assertEqual(validator.feed('later'),[])

    def test_too_many_segments_abort_before_final_validation(self):
        validator=StreamSegmentValidator(sources=self.sources)
        with self.assertRaises(LocalAIError):
            validator.feed(json.dumps({'segments':[self.fact()]*17},ensure_ascii=False))
        self.assertEqual(validator.buffered_chars,0)

    def test_provisional_budget_bounds_queued_segments(self):
        quote='Доказательство '*100
        validator=StreamSegmentValidator(sources=[{'excerpt':quote}])
        fragments=validator.feed(json.dumps({'segments':[self.fact(quote=quote)]*16},ensure_ascii=False))
        self.assertLessEqual(sum(map(len,fragments)),16384)
        self.assertTrue(fragments)


class StreamEngineIntegration(unittest.TestCase):
    def test_engine_delivers_grounded_fact_before_generation_finishes(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from app.database.store import Store
        from app.services.companion import CompanionEngine
        quote='Телефон Влада: 5551122.'
        raw=json.dumps({'segments':[{'kind':'fact','text':'UNVERIFIED-PARAPHRASE','source_ids':[1],
                                    'quotes':[{'source_id':1,'quote':quote}]}]},ensure_ascii=False)
        class Client:
            finished=False
            def probe(self):return {'available':True,'model_ready':True,'models':[]}
            def chat_stream(self,messages,**kwargs):
                for char in raw[:-2]:self.on_stream_content(char)
                self.on_stream_content(raw[-2:])
                self.finished=True
                return raw
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'notes.sqlite3';store=Store(path)
            try:
                note=store.create_note();store.save_note(note,'Влад',quote,None,False,'')
                client=Client();engine=CompanionEngine(path,client_factory=lambda **kw:client)
                events=[]
                def receive(packet):events.append((dict(packet),client.finished))
                with patch('app.services.companion.ensure_runtime',return_value=True):
                    result=engine.ask('Влад телефон',on_segment=receive)
                segments=[(p,done) for p,done in events if p['kind']=='segment']
                self.assertTrue(segments)
                self.assertTrue(all(not done for p,done in segments))
                text=''.join(p['text'] for p,done in segments)
                self.assertIn('5551122',text);self.assertNotIn('UNVERIFIED-PARAPHRASE',text)
                self.assertEqual(result['status'],'answered')
                self.assertEqual(events[-1][0]['kind'],'reset')
                self.assertIsNone(client.on_stream_content)
            finally:store.db.close()


if __name__ == '__main__':
    unittest.main()

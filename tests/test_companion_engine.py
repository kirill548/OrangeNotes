"""Engine safety tests with controlled model responses, not live-model evaluations."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.database.store import Store
from app.services.companion import CompanionEngine
from app.services.local_ai import LocalAIError
from app.services.memory_store import MemoryStore


class FakeClient:
    def __init__(self):
        self.messages=[]
        self.reply=None
        self.before_reply=None

    def probe(self):
        return {'available':True,'model_ready':True,'models':[]}

    def chat(self,messages,**kwargs):
        self.messages.append(messages)
        packet=json.loads(messages[-1]['content'])
        if self.before_reply:
            self.before_reply()
        value=self.reply(packet) if callable(self.reply) else self.reply
        return value if isinstance(value,str) else json.dumps(value,ensure_ascii=False)


class CompanionTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.path=Path(self.directory.name)/'notes.sqlite3'
        self.store=Store(self.path)
        self.client=FakeClient()
        self.engine=CompanionEngine(self.path,client_factory=lambda **kwargs:self.client)
        self.runtime=patch('app.services.companion.ensure_runtime',return_value=True)
        self.runtime.start()

    def tearDown(self):
        self.runtime.stop()
        self.store.db.close()
        self.directory.cleanup()

    def test_approximate_identifier_is_a_suggestion_never_model_evidence(self):
        self.note('Заказ','ABCD12345678')
        result=self.engine.ask('ABCD12345679',config={'fuzzy_identifiers':True})
        self.assertEqual(result['status'],'fuzzy_suggestions')
        self.assertIn('Точного совпадения нет',result['text'])
        self.assertEqual(self.client.messages,[])

    def note(self,title,body,workspace=1):
        note=self.store.create_note()
        self.store.save_note(note,title,'<p>'+body+'</p>',None,False,'')
        memory=MemoryStore(self.path)
        try:
            if workspace!=1: memory.move_note(note,workspace)
        finally: memory.close()
        return note

    @staticmethod
    def facts(packet):
        return {'segments':[{'kind':'fact','text':source['text'],'source_ids':[source['source_id']],
                             'quotes':[{'source_id':source['source_id'],'quote':source['text']}]} for source in packet['sources']]}

    def test_two_old_notes_surface_a_cited_possible_relationship(self):
        garage=self.note('Аренда гаража','Аренда гаража: собственник Ирина, договор 18-Г.')
        studio=self.note('План студии','Студия рассматривает помещение по договору 18-Г; условия ещё не согласованы.')
        def reply(packet):
            result=self.facts(packet)
            result['segments'].append({'kind':'inference','text':'Возможно, гараж и студия относятся к одному договору 18-Г.',
                                       'source_ids':[source['source_id'] for source in packet['sources']]})
            return result
        self.client.reply=reply
        result=self.engine.ask('Как аренда гаража связана со студией?')
        self.assertEqual(result['status'],'answered')
        self.assertEqual({source['note_id'] for source in result['sources']},{garage,studio})
        self.assertIn('Возможная связь:',result['text'])
        self.assertIn('[1]',result['text']); self.assertIn('[2]',result['text'])

    def test_workspace_note_and_chat_history_canaries_never_reach_work_prompt(self):
        canary='PERSONAL_CANARY_ZEBRA_743921'
        private=self.note('Аренда личная',canary)
        work=self.note('Аренда рабочая','Рабочие условия аренды обсуждаются отдельно.',2)
        self.client.reply=self.facts
        history=[{'workspace_id':1,'role':'assistant','content':canary},
                 {'workspace_id':2,'role':'user','content':'WORK_HISTORY_ALLOWED'},
                 {'workspace_id':2,'role':'assistant','content':canary,'sources':[{'note_id':private,'revision':1}]}]
        result=self.engine.ask('Аренда',workspace_id=2,history=history)
        self.assertEqual({source['note_id'] for source in result['sources']},{work})
        prompt=json.dumps(self.client.messages,ensure_ascii=False)
        self.assertNotIn(canary,prompt)
        self.assertIn('WORK_HISTORY_ALLOWED',prompt)
        self.assertNotIn(canary,result['text'])

    def test_unknown_source_and_invalid_quote_return_search_only(self):
        self.note('Контакт','Телефон Ирины 5551122.')
        for segment in [
            {'kind':'fact','text':'FABRICATED_ANSWER','source_ids':[999],'quotes':[]},
            {'kind':'fact','text':'FABRICATED_ANSWER','source_ids':[1],'quotes':[{'source_id':1,'quote':'Ирина 9998888'}]},
            {'kind':'fact','text':'FABRICATED_ANSWER','source_ids':[True],'quotes':[]},
            {'kind':'fact','text':'FABRICATED_ANSWER','source_ids':[],'quotes':[]},
        ]:
            self.client.reply={'segments':[segment]}
            result=self.engine.ask('Контакт')
            self.assertEqual(result['status'],'search_only')
            self.assertNotIn('FABRICATED_ANSWER',result['text'])

    def test_valid_quote_cannot_justify_fabricated_fact_text(self):
        self.note('Контакт','Телефон Ирины 5551122.')
        self.client.reply={'segments':[{'kind':'fact','text':'Ирина просит позвонить 9998888.', 'source_ids':[1],
                                        'quotes':[{'source_id':1,'quote':'Телефон Ирины 5551122.'}]}]}
        result=self.engine.ask('Контакт')
        self.assertNotIn('9998888',result['text'])
        self.assertIn(result['status'],('answered','search_only'))
        self.assertIn('5551122',result['text'])

    def test_no_sources_memory_does_not_call_generation(self):
        self.client.reply={'segments':[{'kind':'suggestion','text':'Неподтверждённый ответ','source_ids':[]}]}
        result=self.engine.ask('НЕСУЩЕСТВУЮЩИЙОСЦИЛЛЯТОР7839')
        self.assertEqual(result['status'],'no_evidence')
        self.assertEqual(result['sources'],[])
        self.assertEqual(self.client.messages,[])

    def test_extra_forged_quote_cannot_hide_behind_one_valid_quote(self):
        self.note('Контакт','Телефон Ирины 5551122.')
        self.client.reply={'segments':[{'kind':'fact','text':'Контакт','source_ids':[1],
            'quotes':[{'source_id':1,'quote':'Телефон Ирины 5551122.'},
                      {'source_id':1,'quote':'FORGED_EXTRA_QUOTE: телефон 9998888'}]}]}
        result=self.engine.ask('Контакт')
        self.assertNotIn('FORGED_EXTRA_QUOTE',result['text'])
        self.assertNotIn('9998888',result['text'])

    def test_discuss_without_sources_can_offer_general_suggestion(self):
        self.client.reply={'segments':[{'kind':'suggestion','text':'Сначала определите один измеримый результат.','source_ids':[]}]}
        result=self.engine.ask('Как упорядочить мысли?',mode='discuss')
        self.assertEqual(result['status'],'answered')
        self.assertEqual(result['sources'],[])
        self.assertIn('Предложение:',result['text'])

    def test_changed_source_after_generation_is_not_presented_as_answer(self):
        note=self.note('Аренда','OLD_SOURCE_SENTINEL: аренда стоит 100 рублей.')
        self.client.reply=self.facts
        self.client.before_reply=lambda:self.store.save_note(note,'Аренда','<p>NEW_SOURCE_SENTINEL: аренда стоит 200 рублей.</p>',None,False,'')
        result=self.engine.ask('Аренда')
        self.assertEqual(result['status'],'search_only')
        self.assertNotIn('OLD_SOURCE_SENTINEL',result['text'])
        self.assertIn('NEW_SOURCE_SENTINEL',result['text'])

    def test_malformed_history_source_is_ignored(self):
        self.note('Аренда','Условия аренды согласуются.')
        self.client.reply=self.facts
        result=self.engine.ask('Аренда',history=[{'workspace_id':1,'role':'assistant','content':'MALFORMED_HISTORY_CANARY','sources':[{'note_id':1}]}])
        self.assertEqual(result['status'],'answered')
        self.assertNotIn('MALFORMED_HISTORY_CANARY',json.dumps(self.client.messages))

    def test_source_archived_during_reply_is_excluded_when_archive_disabled(self):
        note=self.note('Аренда','ARCHIVE_BEFORE_ANSWER_CANARY')
        self.client.reply=self.facts
        self.client.before_reply=lambda:self.store.execute('UPDATE notes SET archived=1 WHERE id=?',(note,))
        result=self.engine.ask('Аренда',include_archive=False)
        self.assertEqual(result['status'],'search_only')
        self.assertNotIn('ARCHIVE_BEFORE_ANSWER_CANARY',result['text'])
        self.assertEqual(result['sources'],[])

    def test_explicit_trash_scope_allows_citation_but_default_excludes(self):
        note=self.note('Аренда','Удалённая запись об аренде.')
        self.store.execute('UPDATE notes SET deleted=1 WHERE id=?',(note,))
        self.client.reply=self.facts
        default=self.engine.ask('Аренда')
        self.assertEqual(default['status'],'no_evidence')
        explicit=self.engine.ask('Аренда',include_trash=True)
        self.assertEqual(explicit['status'],'answered')
        self.assertEqual(explicit['sources'][0]['note_id'],note)
        self.assertEqual(explicit['sources'][0]['state'],'trash')


if __name__=='__main__':
    unittest.main()

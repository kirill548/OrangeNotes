"""Scoped graph tools and opt-in, evidence-backed context expansion."""
import threading
from unittest.mock import patch
import pytest
from app.services.tool_schema import validate_tool_call, ToolValidationError
from app.services.companion import CompanionEngine
from tests import test_companion_engine as fixtures

@pytest.mark.parametrize('name', ['get_linked_notes', 'get_backlinks'])
def test_graph_tool_schema(name):
    assert validate_tool_call({'name': name, 'arguments': {'note_id': 7}})['arguments'] == {'note_id': 7}
    for arguments in ({'note_id': True}, {'note_id': '7'}, {'note_id': 0},
                      {'note_id': 7, 'workspace_id': 2}, {'note_id': 2**64}):
        with pytest.raises(ToolValidationError):
            validate_tool_call({'name': name, 'arguments': arguments})

def test_untrusted_id_has_no_read_capability(tmp_path):
    engine = CompanionEngine(tmp_path/'does-not-exist.sqlite')
    with pytest.raises(ToolValidationError):
        engine.execute_graph_tool({'name':'get_backlinks','arguments':{'note_id':9}},
            workspace_id=1, authorized_note_ids=[1])
    assert not engine.path.exists()

@pytest.mark.parametrize('depth', [True, '1', -1, 3])
def test_depth_is_not_coerced(tmp_path, depth):
    with pytest.raises(ValueError):
        CompanionEngine(tmp_path/'notes.sqlite')._linked_context([1],depth,1,False,False,'',threading.Event())

def test_context_expansion_bounded_deduplicated_and_cancellable(tmp_path):
    engine = CompanionEngine(tmp_path/'notes.sqlite')
    def execute(call, **kwargs):
        assert kwargs['authorized_note_ids'] == [call['arguments']['note_id']]
        return [{'note_id': identity} for identity in range(1,30)]
    with patch.object(engine, 'execute_graph_tool', side_effect=execute):
        assert engine._linked_context([1],0,1,False,False,'',threading.Event()) == []
        values=engine._linked_context([1],2,1,False,False,'',threading.Event())
        assert len(values)==8 and len({v['note_id'] for v in values})==8
        assert all(v['note_id'] != 1 for v in values)

class TestGraphContextEngine:
    def setup_method(self):
        fixtures.CompanionTests.setUp(self)
    def teardown_method(self):
        fixtures.CompanionTests.tearDown(self)
    note=fixtures.CompanionTests.note

    def test_real_graph_tool_scope_and_sources(self):
        target=self.note('Target', 'Подтвержденный факт о поставке.')
        seed=self.note('Seed', 'Ссылка [[Target]].')
        self.note('Private', 'PRIVATE_CANARY',workspace=2)
        values=self.engine.execute_graph_tool({'name':'get_linked_notes','arguments':{'note_id':seed}},
            workspace_id=1,authorized_note_ids=[seed])
        assert [source['note_id'] for source in values] == [target]
        assert 'Подтвержденный' in values[0]['excerpt']
        with pytest.raises(ValueError):
            self.engine.execute_graph_tool({'name':'get_linked_notes','arguments':{'note_id':seed}},
                workspace_id=2,authorized_note_ids=[seed])

    def test_explicit_pin_expands_search_fallback_and_is_opt_in(self):
        target=self.note('Target','Подтвержденный факт о поставке.')
        seed=self.note('Seed','Ссылка [[Target]].')
        off=self.engine.ask('Информация',attached_note_ids=[seed],config={'provider':'search'})
        assert target not in {s['note_id'] for s in off['sources']}
        on=self.engine.ask('Информация',attached_note_ids=[seed],config={'provider':'search','linked_context_depth':1})
        assert {s['note_id'] for s in on['sources']} == {seed,target}
        assert next(s for s in on['sources'] if s['note_id']==target)['source_kind']=='linked'

    def test_two_levels_cycles_archive_and_deleted_filters(self):
        third=self.note('Third','Связь [[Seed]].')
        target=self.note('Target','Связь [[Third]].')
        seed=self.note('Seed','Ссылка [[Target]].')
        config={'provider':'search','linked_context_depth':2}
        result=self.engine.ask('Информация',attached_note_ids=[seed],config=config)
        assert {s['note_id'] for s in result['sources']} == {seed,target,third}
        self.store.execute('UPDATE notes SET archived=1 WHERE id=?',(target,))
        result=self.engine.ask('Информация',attached_note_ids=[seed],config=config,include_archive=False)
        assert target not in {s['note_id'] for s in result['sources']}
        self.store.execute('UPDATE notes SET deleted=1 WHERE id=?',(target,))
        result=self.engine.ask('Информация',attached_note_ids=[seed],config=config,include_archive=True)
        assert target not in {s['note_id'] for s in result['sources']}

    def test_linked_source_preserves_grounding_and_user_data_role(self):
        target=self.note('Target','Поставка ожидается в пятницу.')
        seed=self.note('Seed','Ссылка [[Target]].')
        self.client.reply=fixtures.CompanionTests.facts
        result=self.engine.ask('Что известно?',attached_note_ids=[seed],
            config={'linked_context_depth':1})
        assert result['status']=='answered'
        assert {s['note_id'] for s in result['sources']} == {seed,target}
        assert 'Поставка ожидается в пятницу' in result['text']
        assert 'Поставка ожидается в пятницу' not in self.client.messages[0][0]['content']


class PlanningClient(fixtures.FakeClient):
    def __init__(self, calls):
        super().__init__()
        self.calls=iter(calls);self.plans=[]
    def chat_graph_tools(self,messages,tools,**kwargs):
        import copy
        self.plans.append(copy.deepcopy(messages))
        assert {t['function']['name'] for t in tools}=={'get_linked_notes','get_backlinks'}
        return next(self.calls,{'content':'done'})


def planned(name,identity):
    return {'content':'UNTRUSTED_PLANNER_PROSE','thinking':'SECRET_THINKING',
        'tool_calls':[{'function':{'name':name,'arguments':{'note_id':identity}}}]}


class TestAutonomousGraph:
    setup_method=TestGraphContextEngine.setup_method
    teardown_method=TestGraphContextEngine.teardown_method
    note=TestGraphContextEngine.note
    def plan(self,calls):
        self.client=PlanningClient(calls)
        self.client.reply=fixtures.CompanionTests.facts
        self.engine=CompanionEngine(self.path,client_factory=lambda **kw:self.client)

    def test_actual_ask_two_hops_native_tools_and_final_grounding(self):
        import json
        third=self.note('Third','Delivery is Friday.')
        target=self.note('Target','Link [[Third]].')
        seed=self.note('Seed','Link [[Target]].')
        self.plan([planned('get_linked_notes',seed),planned('get_linked_notes',target),{'content':'done'}])
        result=self.engine.ask('Details?',attached_note_ids=[seed],config={'autonomous_graph':True})
        assert result['status']=='answered'
        assert {s['note_id'] for s in result['sources']}=={seed,target,third}
        assert result['graph_tool_audit']=={'steps':2,'max_depth':2,'stop_reason':'model_done'}
        assert 'Delivery is Friday' in result['text']
        assert 'UNTRUSTED_PLANNER_PROSE' not in json.dumps(self.client.plans+self.client.messages)
        assert self.client.plans[1][-1]['role']=='tool'

    def test_three_step_cap_and_repeated_call_stop(self):
        target=self.note('Target','Link [[Seed]].')
        seed=self.note('Seed','Link [[Target]].')
        self.plan([planned('get_linked_notes',seed),planned('get_backlinks',seed),
                   planned('get_linked_notes',target),planned('get_backlinks',target)])
        result=self.engine.ask('Details?',attached_note_ids=[seed],config={'autonomous_graph':True})
        assert len(self.client.plans)==3 and result['graph_tool_audit']['steps']==3
        self.plan([planned('get_linked_notes',seed)]*4)
        result=self.engine.ask('Details?',attached_note_ids=[seed],config={'autonomous_graph':True})
        assert result['graph_tool_audit']['steps']==1
        assert result['graph_tool_audit']['stop_reason']=='repeat_or_depth_limit'

    @pytest.mark.parametrize('call',[{'tool_calls':'bad'}, {'tool_calls':[{},{}]},
        {'tool_calls':[{'function':{'name':'create_task','arguments':{}}}]},
        {'tool_calls':[{'function':{'name':'get_backlinks','arguments':{'note_id':True}}}]}])
    def test_malformed_planning_fails_to_search_only(self,call):
        seed=self.note('Seed','Evidence text.')
        self.plan([call])
        result=self.engine.ask('Details?',attached_note_ids=[seed],config={'autonomous_graph':True})
        assert result['status']=='search_only' and not self.client.messages

    def test_guessed_private_id_never_authorized(self):
        seed=self.note('Seed','Evidence text.')
        private=self.note('Private','PRIVATE_CANARY',workspace=2)
        self.plan([planned('get_backlinks',private)])
        result=self.engine.ask('Details?',attached_note_ids=[seed],config={'autonomous_graph':True})
        assert result['status']=='search_only'
        assert 'PRIVATE_CANARY' not in str(result)+str(self.client.plans)

    def test_archived_bridge_not_discovered(self):
        third=self.note('Third','SECRET_AFTER_ARCHIVE')
        target=self.note('Target','Link [[Third]].')
        seed=self.note('Seed','Link [[Target]].')
        self.store.execute('UPDATE notes SET archived=1 WHERE id=?',(target,))
        self.plan([planned('get_linked_notes',seed),planned('get_linked_notes',target)])
        result=self.engine.ask('Details?',attached_note_ids=[seed],include_archive=False,
            config={'autonomous_graph':True})
        assert result['status']=='search_only'
        assert 'SECRET_AFTER_ARCHIVE' not in str(self.client.plans)

    def test_planning_revision_mutation_blocks_final_answer(self):
        seed=self.note('Seed','Evidence text.')
        self.plan([{'content':'done'}])
        original=self.client.chat_graph_tools
        def mutate(*args,**kwargs):
            result=original(*args,**kwargs)
            self.store.save_note(seed,'Seed','<p>Changed</p>',None,False,'')
            return result
        self.client.chat_graph_tools=mutate
        result=self.engine.ask('Details?',attached_note_ids=[seed],config={'autonomous_graph':True})
        assert result['status']=='search_only'


def test_native_graph_transport_uses_only_read_tools():
    from app.services.local_ai import OllamaClient
    from app.services.tool_schema import TOOL_DEFINITIONS
    client=OllamaClient();client.deadline=10**12
    tools=[t for t in TOOL_DEFINITIONS if t['function']['name'] in ('get_linked_notes','get_backlinks')]
    with patch.object(client,'_ensure_installed_local'),patch.object(client,'_request',
            return_value={'message':{'role':'assistant','tool_calls':[]}}) as request:
        result=client.chat_graph_tools([{'role':'user','content':'Question'}],tools)
    assert result['tool_calls']==[]
    payload=request.call_args.args[1]
    assert payload['think'] is False and payload['stream'] is False
    assert 'format' not in payload and len(payload['tools'])==2


class TestAutonomousGraphLimits:
    setup_method=TestGraphContextEngine.setup_method
    teardown_method=TestGraphContextEngine.teardown_method
    note=TestGraphContextEngine.note
    def test_eight_sources_cap(self):
        ids=[self.note('Target'+str(i),'Evidence number '+str(i)) for i in range(12)]
        seed=self.note('Seed',' '.join('[[Target'+str(i)+']]' for i in range(12)))
        client=PlanningClient([planned('get_linked_notes',seed)])
        client.deadline=10**12
        from app.services.memory_store import MemoryStore
        memory=MemoryStore(self.path)
        try:
            seeds=memory.attachment_sources([seed],workspace_id=1)
            values,audit=self.engine._autonomous_graph(client,seeds,1,True,False,'Details?',threading.Event(),memory)
        finally:memory.close()
        assert len(values)==8 and audit['stop_reason']=='source_limit'

    def test_cancelled_planner_never_generates(self):
        from app.services.local_ai import LocalAICancelled
        seed=self.note('Seed','Evidence text.')
        client=PlanningClient([{'content':'done'}])
        event=threading.Event(); original=client.chat_graph_tools
        def cancel(*args,**kwargs):
            result=original(*args,**kwargs);event.set();return result
        client.chat_graph_tools=cancel
        engine=CompanionEngine(self.path,client_factory=lambda **kw:client)
        with pytest.raises(LocalAICancelled):
            engine.ask('Details?',attached_note_ids=[seed],cancel_event=event,config={'autonomous_graph':True})
        assert not client.messages

    def test_shared_deadline_cannot_restart_after_planning(self):
        seed=self.note('Seed','Evidence text.')
        client=PlanningClient([{'content':'done'}]);original=client.chat_graph_tools
        def expire(*args,**kwargs):
            result=original(*args,**kwargs);client.deadline=0;return result
        client.chat_graph_tools=expire
        engine=CompanionEngine(self.path,client_factory=lambda **kw:client)
        result=engine.ask('Details?',attached_note_ids=[seed],config={'autonomous_graph':True})
        assert result['status']=='search_only' and not client.messages

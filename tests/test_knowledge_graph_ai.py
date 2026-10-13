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


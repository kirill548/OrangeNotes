import sqlite3
import time
import pytest
from app.database.store import Store
from app.services.knowledge_graph import KnowledgeGraph, KnowledgeGraphIndexer, parse_wiki_links

@pytest.fixture
def setup(tmp_path):
    store=Store(tmp_path/'notes.db'); graph=KnowledgeGraph(store.path)
    yield store,graph
    store.db.close()

def add(store,title,body='',workspace=1,deleted=0,archived=0):
    with store.db:
        return store.db.execute('INSERT INTO notes(title,body,workspace_id,deleted,archived,updated_at) VALUES(?,?,?,?,?,?)',(title,body,workspace,deleted,archived,'2026-10-13')).lastrowid

@pytest.mark.parametrize('body,target,label',[
    ('[[Заметка]]','Заметка','Заметка'),('[[ Заметка | Пример ]]','Заметка','Пример'),
    ('<p>[[Работа &amp; жизнь|Алиас]]</p>','Работа & жизнь','Алиас')])
def test_parser(body,target,label):
    links=parse_wiki_links(body)
    assert len(links)==1
    assert links[0]['target_title']==target
    assert links[0]['link_text']==label

@pytest.mark.parametrize('body',[
    r'\[[Заметка]]','`[[Заметка]]`','```md\n[[Заметка]]\n```',
    '<a href="[[Заметка]]">Текст</a>','<pre>[[Заметка]]</pre>',
    '<script>[[Заметка]]</script>','[[]]','[[|Алиас]]','[[Текст|]]',
    '[[А[[Б]]]]','[[A\nB]]','[[не закрыто]'])
def test_parser_ignores_non_links(body):
    assert parse_wiki_links(body)==[]

def test_resolve_alias_backlink_context_and_unlinked(setup):
    s,g=setup; target=add(s,'План'); source=add(s,'Работа','<p>Подробнее: [[План|план работы]]</p>')
    mention=add(s,'Обсуждение','Нужно обсудить План завтра.')
    assert [n['id'] for n in g.linked_notes(source,1)]==[target]
    backlinks=g.backlinks(target,1)
    assert backlinks[0]['id']==source and backlinks[0]['link_text']=='план работы'
    assert 'Подробнее' in backlinks[0]['context_snippet']
    assert [n['id'] for n in g.unlinked_mentions(target,1)]==[mention]

def test_unresolved_becomes_resolved_after_creation(setup):
    s,g=setup; source=add(s,'Источник','[[Будущее]]')
    assert not g.linked_notes(source,1)
    target=add(s,'Будущее')
    assert [n['id'] for n in g.linked_notes(source,1)]==[target]

def test_duplicate_titles_never_choose_arbitrary_target(setup):
    s,g=setup; source=add(s,'Источник','[[план]]'); add(s,'План'); add(s,'ПЛАН')
    assert not g.linked_notes(source,1)
    with s.db: s.db.execute('DELETE FROM notes WHERE title=?',('ПЛАН',))
    assert len(g.linked_notes(source,1))==1

def test_workspace_deleted_archive_isolation(setup):
    s,g=setup; one=add(s,'Источник','[[План]]'); foreign=add(s,'План',workspace=2)
    assert not g.linked_notes(one,1)
    target=add(s,'План',archived=1)
    assert g.linked_notes(one,1)[0]['id']==target
    assert not g.backlinks(target,2) and not g.context(one,2)
    assert foreign not in {n['id'] for n in g.graph(1)['nodes']}
    with s.db: s.db.execute('UPDATE notes SET deleted=1 WHERE id=?',(target,))
    assert not g.linked_notes(one,1)

def test_two_hops_bounded_cycles_and_physical_delete(setup):
    s,g=setup; a=add(s,'A','[[B]]'); b=add(s,'B','[[C]]'); c=add(s,'C','[[A]]'); add(s,'D','[[C]]')
    assert {n['id'] for n in g.context(a,1,depth=1)}=={b,c}
    assert len(g.context(a,1,depth=2,limit=1))==1
    with pytest.raises(ValueError): g.context(a,1,depth=3)
    with s.db: s.db.execute('DELETE FROM notes WHERE id=?',(b,))
    assert all(e['source_id']!=b and e['target_id']!=b for e in g.graph(1)['edges'])
    assert s.db.execute('PRAGMA foreign_key_check').fetchall()==[]

def test_rename_preserves_alias_attributes_code_and_workspace(setup):
    s,g=setup; target=add(s,'План'); body='<p>[[План]] [[План|Алиас]]</p><a href="[[План]]">ссылка</a><code>[[План]]</code>`[[План]]`'
    source=add(s,'Источник',body); foreign=add(s,'Другой','[[План]]',workspace=2)
    s.save_note(target,'Новый план','',None,False,'')
    actual=s.db.execute('SELECT body FROM notes WHERE id=?',(source,)).fetchone()[0]
    assert '<p>[[Новый план]] [[Новый план|Алиас]]</p>' in actual
    assert 'href="[[План]]"' in actual and '<code>[[План]]</code>`[[План]]`' in actual
    assert s.db.execute('SELECT body FROM notes WHERE id=?',(foreign,)).fetchone()[0]=='[[План]]'
    assert g.linked_notes(source,1)[0]['id']==target

def test_dirty_queue_coalesces_and_worker_stops(setup):
    s,g=setup; source=add(s,'A','[[B]]'); target=add(s,'B')
    for _ in range(10):
        with s.db:s.db.execute('UPDATE notes SET body=body WHERE id=?',(source,))
    assert s.db.execute('SELECT count(*) FROM note_links_dirty').fetchone()[0]==2
    worker=KnowledgeGraphIndexer(s.path,interval=.01); worker.start()
    deadline=time.monotonic()+3
    while time.monotonic()<deadline:
        if s.db.execute('SELECT count(*) FROM note_links_dirty').fetchone()[0]==0:break
        time.sleep(.01)
    worker.stop()
    assert not worker.thread.is_alive() and worker.last_error is None
    assert g.linked_notes(source,1)[0]['id']==target


def test_unicode_duplicate_rename_does_not_rewrite_ambiguous_links(setup):
    s,g=setup; target=add(s,'План'); add(s,'ПЛАН'); source=add(s,'Источник','[[План]]')
    s.save_note(target,'Новое','',None,False,'')
    assert s.db.execute('SELECT body FROM notes WHERE id=?',(source,)).fetchone()[0]=='[[План]]'

def test_self_link_renaming(setup):
    s,g=setup; target=add(s,'A','[[A|себя]]')
    s.save_note(target,'B','[[A|себя]]',None,False,'')
    assert s.db.execute('SELECT body FROM notes WHERE id=?',(target,)).fetchone()[0]=='[[B|себя]]'
    assert g.linked_notes(target,1)[0]['id']==target

def test_archive_seed_and_related_flags(setup):
    s,g=setup; a=add(s,'A','[[B]]'); b=add(s,'B',archived=1)
    assert not g.linked_notes(a,1,include_archive=False)
    assert not g.backlinks(b,1,include_archive=False)
    assert g.backlinks(b,1,include_archive=True)[0]['id']==a

def test_atomic_rename_rolls_back_on_invalid_folder(setup):
    s,g=setup; target=add(s,'A'); source=add(s,'Source','[[A]]')
    with pytest.raises(sqlite3.IntegrityError): s.save_note(target,'B','',99999,False,'')
    assert s.db.execute('SELECT title FROM notes WHERE id=?',(target,)).fetchone()[0]=='A'
    assert s.db.execute('SELECT body FROM notes WHERE id=?',(source,)).fetchone()[0]=='[[A]]'


def test_reopening_store_does_not_invalidate_clean_index(setup):
    s, g = setup
    add(s, 'A', '[[B]]'); add(s, 'B')
    g.refresh()
    reopened = Store(s.path)
    try:
        assert reopened.db.execute('SELECT count(*) FROM note_links_dirty').fetchone()[0] == 0
    finally:
        reopened.db.close()


def test_incremental_edit_preserves_unaffected_edges(setup):
    s,g=setup
    ids=[add(s,f'Note {i}',f'[[Note {(i+1)%550}]]') for i in range(550)]
    g.refresh()
    assert g.last_refresh_stats['rebuilt_sources']==550
    edge_ids={r['source_id']:r['id'] for r in s.db.execute('SELECT * FROM note_links')}
    with s.db: s.db.execute('UPDATE notes SET body=? WHERE id=?',('[[Note 3]]',ids[0]))
    g.refresh()
    assert g.last_refresh_stats['rebuilt_sources']==1
    assert all(r['id']==edge_ids[r['source_id']] for r in s.db.execute('SELECT * FROM note_links WHERE source_id != ?',(ids[0],)))
    assert g.linked_notes(ids[0],1)[0]['id']==ids[3]


def test_move_title_resolution_updates_both_workspaces(setup):
    s,g=setup; target=add(s,'Target'); a=add(s,'A','[[Target]]'); b=add(s,'B','[[Target]]',workspace=2)
    g.refresh()
    with s.db: s.db.execute('UPDATE notes SET workspace_id=2 WHERE id=?',(target,))
    assert not g.linked_notes(a,1)
    assert g.linked_notes(b,2)[0]['id']==target


def test_legacy_index_migration_rebuilds_once(setup):
    s,g=setup; add(s,'A','[[B]]'); add(s,'B'); g.refresh()
    for name in ('links_insert','links_update','links_delete'): s.db.execute('DROP TRIGGER '+name)
    s.db.execute('DROP TABLE note_links_dirty')
    s.db.execute('CREATE TABLE note_links_dirty(id INTEGER PRIMARY KEY CHECK(id=1))')
    s.db.commit()
    from app.services.knowledge_graph import migrate
    migrate(s.db); s.db.commit(); g.refresh()
    assert len(g.graph(1)['edges'])==1
    assert s.db.execute('PRAGMA foreign_key_check').fetchall()==[]

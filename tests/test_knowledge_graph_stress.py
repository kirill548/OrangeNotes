import json
import sqlite3
import threading
import time
from collections import Counter
from app.database.store import Store
from app.services.knowledge_graph import KnowledgeGraph, KnowledgeGraphIndexer, key, migrate, parse_wiki_links


def populate(db, count=2100):
    with db:
        db.executemany('INSERT INTO notes(title,body,workspace_id,updated_at) VALUES(?,?,1,?)',
          [(f'Узел {i}', f'[[Узел {(i+1)%count}|цикл]] [[Узел {(2*i+1)%count}]] [[Узел {(2*i+2)%count}]] [[Будущее {i%120}]]', '2026-10-13') for i in range(count)])
        db.executemany('INSERT INTO notes(title,body,workspace_id,updated_at) VALUES(?,?,2,?)',
          [(f'Узел {i}', '[[Узел 1]]', '2026-10-13') for i in range(30)])


def assert_oracle(db):
    rows=db.execute('SELECT id,title,body,workspace_id,deleted FROM notes').fetchall()
    lookup={}
    for n in rows:
        if not n[4]: lookup.setdefault((n[3],key(n[1])),[]).append(n[0])
    expected=[]
    for n in rows:
        for link in parse_wiki_links(n[2]):
            ids=lookup.get((n[3],key(link['target_title'])),[])
            expected.append((n[0],ids[0] if len(ids)==1 else None,link['target_title'],link['link_text'],link['context_snippet'],key(link['target_title'])))
    actual=db.execute('SELECT source_id,target_id,target_title,link_text,context_snippet,target_key FROM note_links').fetchall()
    assert Counter(expected)==Counter(tuple(r) for r in actual)
    assert not db.execute('PRAGMA foreign_key_check').fetchall()
    assert db.execute('SELECT count(*) FROM note_links_dirty').fetchone()[0]==0


def test_2100_notes_bulk_mutations_match_full_oracle(tmp_path):
    s=Store(tmp_path/'stress.db'); g=KnowledgeGraph(s.path)
    try:
        populate(s.db); start=time.perf_counter(); g.refresh(); first=time.perf_counter()-start
        assert_oracle(s.db)
        with s.db:
            s.db.executemany('UPDATE notes SET title=? WHERE title=? AND workspace_id=1',[(f'Переименован {i}',f'Узел {i}') for i in range(120)])
            s.db.executemany('DELETE FROM notes WHERE title=? AND workspace_id=1',[(f'Узел {i}',) for i in range(120,240)])
            s.db.executemany('INSERT INTO notes(title,body,workspace_id,updated_at) VALUES(?,?,1,?)',[(f'Будущее {i}',f'[[Переименован {i}]] [[Узел 500]]','2026-10-13') for i in range(120)])
            s.db.execute('UPDATE notes SET workspace_id=2 WHERE title IN ("Узел 500","Узел 501") AND workspace_id=1')
            s.db.execute('INSERT INTO notes(title,body,workspace_id,updated_at) VALUES("УЗЕЛ 600","",1,"2026-10-13")')
            s.db.execute('UPDATE notes SET deleted=1 WHERE title="Узел 700" AND workspace_id=1')
        start=time.perf_counter(); g.refresh(); bulk=time.perf_counter()-start
        assert_oracle(s.db)
        with s.db: s.db.execute('UPDATE notes SET body="[[Узел 900]]" WHERE title="Узел 800" AND workspace_id=1')
        start=time.perf_counter(); g.refresh(); single=time.perf_counter()-start
        assert g.last_refresh_stats['rebuilt_sources']==1
        assert_oracle(s.db)
        metrics={'notes':s.db.execute('SELECT count(*) FROM notes').fetchone()[0], 'links':s.db.execute('SELECT count(*) FROM note_links').fetchone()[0], 'initial_ms':round(first*1000,2),'bulk_ms':round(bulk*1000,2),'single_ms':round(single*1000,2),'fk_errors':0}
        print('INDEX_STRESS_METRICS='+json.dumps(metrics))
    finally: s.db.close()


def test_concurrent_writes_reader_and_worker_stop(tmp_path):
    s=Store(tmp_path/'concurrent.db'); populate(s.db,550); KnowledgeGraph(s.path).refresh()
    worker=KnowledgeGraphIndexer(s.path,interval=.005); errors=[]; finished=threading.Event()
    def reader():
        try:
            for _ in range(12):
                graph=KnowledgeGraph(s.path).graph(1)
                assert all(e['source_id']>0 and e['target_id']>0 for e in graph['edges'])
        except BaseException as exc: errors.append(exc)
        finally: finished.set()
    t=threading.Thread(target=reader); worker.start(); t.start()
    try:
        for batch in range(12):
            with s.db:
                s.db.executemany('UPDATE notes SET body=? WHERE id=?',[(f'[[Узел {(i+batch)%550}]]',i+1) for i in range(batch*10,(batch+1)*10)])
        t.join(15); assert finished.is_set(); assert not errors
        worker.stop(); assert not worker.thread.is_alive(); assert worker.last_error is None
        KnowledgeGraph(s.path).refresh(); assert_oracle(s.db)
        # Repeated start/stop must not leave indexer threads or locked DB handles.
        for _ in range(5): worker.start(); worker.stop(); assert not worker.thread.is_alive()
        assert not [t for t in threading.enumerate() if t.name=='note-links-indexer']
        s.db.execute('BEGIN EXCLUSIVE'); s.db.rollback()
    finally: worker.stop(); s.db.close()


def test_interrupted_incremental_migration_recovers_triggers(tmp_path):
    s=Store(tmp_path/'partial.db')
    try:
        with s.db: s.db.execute('DROP TRIGGER links_insert')
        migrate(s.db); s.db.commit()
        assert s.db.execute("SELECT 1 FROM sqlite_master WHERE name='links_insert'").fetchone()
        with s.db: s.db.execute('INSERT INTO notes(title,body,updated_at) VALUES("После сбоя","[[Цель]]","2026-10-13")')
        assert s.db.execute('SELECT count(*) FROM note_links_dirty').fetchone()[0]>0
    finally: s.db.close()


def test_adversarial_parser_unicode_and_boundaries():
    text='<p>[[ПЛАН &amp; ДЕЛА|работа]] [[東京|日本]] [[emoji 🐝]]</p><script>[[секрет]]</script> `[[код]]` \\[[экран]] [[[вложено]]]' 
    assert [(r['target_title'],r['link_text']) for r in parse_wiki_links(text)]==[('ПЛАН & ДЕЛА','работа'),('東京','日本'),('emoji 🐝','emoji 🐝')]


def test_cancelled_refresh_keeps_queue_and_resumes(tmp_path):
    s=Store(tmp_path/'cancel.db'); populate(s.db,2100); g=KnowledgeGraph(s.path)
    calls=0
    def cancel():
        nonlocal calls
        calls+=1
        return calls>1
    try:
        g.refresh(cancelled=cancel)
        assert g.last_refresh_stats=={'rebuilt_sources':128,'batches':1}
        assert s.db.execute('SELECT count(*) FROM note_links_dirty').fetchone()[0]>0
        g.refresh(); assert_oracle(s.db)
    finally: s.db.close()

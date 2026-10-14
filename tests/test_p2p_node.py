import socket
import sqlite3
import threading
from uuid import uuid4
import pytest
from test_p2p_network import certificates
from app.database.store import Store
from app.database.sync_identity import prepare_identity_copy, inspect_identities
from app.services.p2p_node import P2PNode
from app.services.p2p_transport import PeerIdentity, server_context, client_context


def worker_pair(tmp_path, certificates):
    workspace=str(uuid4())
    paths=[]
    actors=[]
    for name in ('a','b'):
        source=tmp_path/f'{name}-source.db'
        legacy=Store(source)
        legacy.db.close()
        path=prepare_identity_copy(source,tmp_path/f'{name}.db')
        with sqlite3.connect(path) as db:
            actors.append(inspect_identities(db)['device_uuid'])
        paths.append(path)
    nodes=[]
    for index,name in enumerate(('a','b')):
        other='b' if name=='a' else 'a'
        nodes.append(P2PNode(paths[index],workspace_id=workspace,local_workspace_id=1,
            actor_id=actors[index],authorized_actors=[actors[1-index]],
            server_context=server_context(*certificates[name][:2],certificates['ca']),
            client_context=client_context(*certificates[name][:2],certificates['ca']),
            peers=[PeerIdentity(actors[1-index],workspace,certificates[other][2])],timeout=.5))
    return nodes,actors,paths


def test_two_worker_sqlite_tls_sync_and_clean_stop(tmp_path,certificates):
    nodes,actors,paths=worker_pair(tmp_path,certificates)
    before={thread.ident for thread in threading.enumerate()}
    a,b=nodes
    entity=str(uuid4())
    try:
        for node in nodes:
            node.start().wait_ready()
        a.mutate_note(entity,{'title':'Background note'}).result(3)
        b.mutate_note(entity,{'body':'Independent field'}).result(3)
        fa=a.sync_to(*b.address,actors[1])
        fb=b.sync_to(*a.address,actors[0])
        assert fa.result(5)==fb.result(5)==1
        expected={'title':'Background note','body':'Independent field','lifecycle':'active'}
        assert a.get_note(entity).result(3)==b.get_note(entity).result(3)==expected
        assert a.pending_outbox(actors[1]).result(3)==[]
        assert b.pending_outbox(actors[0]).result(3)==[]
    finally:
        for node in nodes:
            node.stop()
    assert not any(node.is_alive for node in nodes)
    assert not [thread for thread in threading.enumerate() if thread.ident not in before and thread.name=='OrangeNotes-P2P']
    for path in paths:
        with sqlite3.connect(path) as db:
            db.execute('BEGIN EXCLUSIVE')
            assert not list(db.execute('PRAGMA foreign_key_check'))


def test_failed_delivery_keeps_outbox_and_retry_works(tmp_path,certificates):
    nodes,actors,_=worker_pair(tmp_path,certificates)
    a,b=nodes
    try:
        a.start().wait_ready()
        b.start().wait_ready()
        wire=a.mutate_note(str(uuid4()),{'title':'Retry'}).result(3)
        with socket.socket() as unused:
            unused.bind(('127.0.0.1',0))
            port=unused.getsockname()[1]
        with pytest.raises(OSError):
            a.sync_to('127.0.0.1',port,actors[1]).result(3)
        assert a.pending_outbox(actors[1]).result(3)==[wire]
        assert a.sync_to(*b.address,actors[1]).result(3)==1
        assert not a.pending_outbox(actors[1]).result(3)
    finally:
        for node in nodes:
            node.stop()


def test_shutdown_cancels_blocked_transport_and_keeps_delta(tmp_path,certificates):
    nodes,actors,_=worker_pair(tmp_path,certificates)
    a,_=nodes
    with socket.socket() as blocker:
        blocker.bind(('127.0.0.1',0))
        blocker.listen()
        try:
            a.start().wait_ready()
            wire=a.mutate_note(str(uuid4()),{'title':'Pending'}).result(3)
            future=a.sync_to(*blocker.getsockname(),actors[1])
            connection,_=blocker.accept()
            with connection:
                a.stop()
                assert not a.is_alive
                assert future.done()
                with pytest.raises(BaseException):
                    future.result()
        finally:
            a.stop()
    with sqlite3.connect(a._path) as db:
        assert db.execute('SELECT wire FROM sync_outbox').fetchone()[0]==wire
        assert db.execute('SELECT count(*) FROM sync_outbox_ack').fetchone()[0]==0


def test_start_failure_is_reported_without_worker_leak(tmp_path,certificates):
    nodes,_,_=worker_pair(tmp_path,certificates)
    node=nodes[0]
    node._path=tmp_path/'missing.db'
    node.start()
    with pytest.raises(FileNotFoundError):
        node.wait_ready()
    node.stop()
    assert not node.is_alive
    with pytest.raises(RuntimeError):
        node.mutate_note(str(uuid4()),{'title':'Not ready'}).result()


def test_sync_batch_limit_leaves_remaining_outbox_for_next_round(tmp_path,certificates):
    nodes,actors,_=worker_pair(tmp_path,certificates)
    a,b=nodes
    try:
        for node in nodes:
            node.start().wait_ready()
        for index in range(3):
            a.mutate_note(str(uuid4()),{'title':f'Note {index}'}).result(3)
        assert a.sync_to(*b.address,actors[1],max_batch=2).result(3)==2
        assert len(a.pending_outbox(actors[1]).result(3))==1
        assert a.sync_to(*b.address,actors[1],max_batch=2).result(3)==1
        with pytest.raises(ValueError):
            a.sync_to(*b.address,actors[1],max_batch=129).result()
        assert not a.pending_outbox(actors[1]).result(3)
    finally:
        for node in nodes:
            node.stop()


@pytest.mark.parametrize('operation',['pending_outbox','acknowledge'])
def test_sync_wall_deadline_covers_blocking_sqlite_calls(tmp_path,certificates,monkeypatch,operation):
    import time
    import app.services.p2p_node as module
    from app.database.p2p_store import P2PStore
    original=getattr(P2PStore,operation)
    reached=[]
    monkeypatch.setattr(module,'SYNC_DEADLINE_SECONDS',1)
    def slow(self,*args):
        result=original(self,*args)
        reached.append(True)
        time.sleep(1.2)
        return result
    monkeypatch.setattr(P2PStore,operation,slow)
    nodes,actors,paths=worker_pair(tmp_path,certificates)
    a,b=nodes
    try:
        for node in nodes:
            node.start().wait_ready()
        wire=a.mutate_note(str(uuid4()),{'title':'Slow SQLite'}).result(3)
        with pytest.raises(TimeoutError):
            a.sync_to(*b.address,actors[1]).result(5)
        assert reached
    finally:
        for node in nodes:
            node.stop()
    with sqlite3.connect(paths[0]) as db:
        assert db.execute('SELECT wire FROM sync_outbox').fetchone()[0]==wire
        # An ACK commit may have completed before its overrun was detected;
        # immutable outbox history is still durable and never discarded.
        assert db.execute('SELECT count(*) FROM sync_outbox_ack').fetchone()[0]==(operation=='acknowledge')

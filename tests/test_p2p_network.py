"""Real local TLS sockets, independent keys, certificate chains and Replica data."""
import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import ipaddress
import ssl
from uuid import uuid4
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID
from app.services.p2p_sync import Replica
from app.services.p2p_transport import (PeerIdentity, TLS13Server, TLS13Client,
    TransportError, client_context, server_context, pair_peer_qr, MAX_FRAME)

@pytest.fixture
def certificates(tmp_path):
    now = datetime.now(timezone.utc)
    def key():
        return ec.generate_private_key(ec.SECP256R1())
    ca_key = key()
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Orange Notes Test CA')])
    ca = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=1))
        .not_valid_after(now+timedelta(days=1)).add_extension(x509.BasicConstraints(ca=True,path_length=0),True)
        .sign(ca_key,hashes.SHA256()))
    ca_path = tmp_path/'ca.pem'
    ca_path.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    result = {'ca': ca_path}
    for actor in ('a','b','c'):
        leaf_key = key()
        leaf_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, actor)])
        cert = (x509.CertificateBuilder().subject_name(leaf_name).issuer_name(name).public_key(leaf_key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=1))
            .not_valid_after(now+timedelta(days=1)).add_extension(x509.BasicConstraints(ca=False,path_length=None),True)
            .add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost'),x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]),False)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH,ExtendedKeyUsageOID.SERVER_AUTH]),False)
            .sign(ca_key,hashes.SHA256()))
        cert_path,key_path=tmp_path/f'{actor}.pem',tmp_path/f'{actor}.key'
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(leaf_key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
        der=cert.public_bytes(serialization.Encoding.DER)
        result[actor]=(cert_path,key_path,hashlib.sha256(der).hexdigest(),der)
    return result

def setup_pair(certs, callback=None, timeout=2):
    workspace,a,b = str(uuid4()),str(uuid4()),str(uuid4())
    replica_a,replica_b=Replica(workspace,a),Replica(workspace,b)
    ca=certs['ca']
    ap,bp=PeerIdentity(a,workspace,certs['a'][2]),PeerIdentity(b,workspace,certs['b'][2])
    def apply(actor,space,wire):
        assert actor==a and space==workspace
        return replica_b.apply_delta(wire)
    server=TLS13Server(server_context(*certs['b'][:2],ca),[ap],callback or apply,local_actor_id=b,timeout=timeout)
    client=TLS13Client(client_context(*certs['a'][:2],ca),a,workspace,bp,timeout=timeout)
    return server,client,replica_a,replica_b


def test_two_real_replicas_retry_idempotent_and_restart(certificates):
    async def run():
        server,client,a,b=setup_pair(certificates)
        entity=str(uuid4())
        wire=a.make_delta(entity,{'title':'План','body':'Связи [[Работа]]'})
        try:
            address=await server.start()
            ack=await client.send_delta(*address,wire)
            assert ack['status']=='committed'
            assert ack['sha256']==hashlib.sha256(wire.encode()).hexdigest()
            await client.send_delta(*address,wire)
            assert a.get(entity)==b.get(entity)
            await server.close()
            address=await server.start()
            await client.send_delta(*address,wire)
            assert a.get(entity)==b.get(entity)
        finally:
            await server.close()
        assert not server._tasks
    asyncio.run(run())

@pytest.mark.parametrize('failure',['server_pin','client_pin','workspace','actor','hostname','untrusted_ca','missing_client_cert'])
def test_authentication_fails_closed(certificates,tmp_path,failure):
    async def run():
        applied=[]
        server,client,a,_=setup_pair(certificates,lambda *args:applied.append(args))
        if failure=='server_pin':
            client.peer=PeerIdentity(client.peer.actor_id,client.workspace,'0'*64)
        elif failure=='client_pin':
            server.peers={}
        elif failure=='workspace':
            client.workspace=str(uuid4())
        elif failure=='actor':
            client.actor=str(uuid4())
        elif failure=='untrusted_ca':
            client.context=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            client.context.minimum_version=client.context.maximum_version=ssl.TLSVersion.TLSv1_3
            client.context.load_cert_chain(*map(str,certificates['a'][:2]))
        elif failure=='missing_client_cert':
            client.context=ssl.create_default_context(cafile=str(certificates['ca']))
            client.context.minimum_version=client.context.maximum_version=ssl.TLSVersion.TLSv1_3
        wire=Replica(client.workspace,client.actor).make_delta(str(uuid4()),{'title':'Denied'})
        try:
            address=await server.start()
            with pytest.raises((TransportError,ssl.SSLError,ConnectionError,asyncio.IncompleteReadError)):
                await client.send_delta(*address,wire,server_hostname='wrong.invalid' if failure=='hostname' else 'localhost')
            assert applied==[]
        finally:
            await server.close()
    asyncio.run(run())


def test_ack_waits_for_durable_callback_and_failure_never_acks(certificates):
    async def run():
        entered,commit=asyncio.Event(),asyncio.Event()
        async def apply(*args):
            entered.set()
            await commit.wait()
            raise RuntimeError('Transaction rolled back')
        server,client,a,_=setup_pair(certificates,apply)
        try:
            address=await server.start()
            request=asyncio.create_task(client.send_delta(*address,a.make_delta(str(uuid4()),{'title':'Draft'})))
            await entered.wait()
            await asyncio.sleep(.03)
            assert not request.done()
            commit.set()
            with pytest.raises(asyncio.IncompleteReadError):
                await request
        finally:
            await server.close()
    asyncio.run(run())

@pytest.mark.parametrize('mode',['deadline','cancel','disconnect','oversize'])
def test_deadlines_cancel_disconnect_bounded_frames(certificates,mode):
    async def run():
        entered=asyncio.Event()
        async def apply(*args):
            entered.set()
            await asyncio.Event().wait()
        server,client,a,_=setup_pair(certificates,apply,timeout=.15)
        writer=None
        try:
            address=await server.start()
            if mode in ('disconnect','oversize'):
                reader,writer=await asyncio.open_connection(*address,ssl=client.context,server_hostname='localhost')
                writer.write(b'\xff\xff\xff\xff' if mode=='oversize' else b'\x00\x00')
                await writer.drain()
                writer.close()
                await writer.wait_closed()
            else:
                task=asyncio.create_task(client.send_delta(*address,a.make_delta(str(uuid4()),{'title':'Wait'})))
                await entered.wait()
                if mode=='cancel':
                    task.cancel()
                with pytest.raises((TimeoutError,asyncio.CancelledError,asyncio.IncompleteReadError)):
                    await task
        finally:
            await server.close()
        assert not server._tasks
    asyncio.run(run())


def test_pairing_requires_full_qr_and_explicit_confirmation(certificates):
    actor,workspace=str(uuid4()),str(uuid4())
    pin,der=certificates['a'][2:]
    with pytest.raises(TransportError):
        pair_peer_qr(actor,workspace,der,pin)
    with pytest.raises(TransportError):
        pair_peer_qr(actor,workspace,der,'123456',confirmed=True)
    with pytest.raises(TransportError):
        pair_peer_qr(actor,workspace,der,'0'*64,confirmed=True)
    assert pair_peer_qr(actor,workspace,der,pin,confirmed=True).actor_id==actor


def test_insecure_context_rejected():
    with pytest.raises(TransportError):
        TLS13Server(ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER),[],lambda *a:None,local_actor_id=str(uuid4()))




def test_real_tls_ciphertext_corruption_never_reaches_receiver(certificates):
    async def run():
        applied=[]
        server,client,a,_=setup_pair(certificates,lambda *args:applied.append(args))
        proxy_tasks=set()
        flipped=[]
        async def proxy(incoming, outgoing):
            task=asyncio.current_task()
            proxy_tasks.add(task)
            remote=None
            try:
                upstream,remote=await asyncio.open_connection(*server.address)
                async def client_to_server():
                    while True:
                        header=await incoming.readexactly(5)
                        size=int.from_bytes(header[3:5],'big')
                        body=await incoming.readexactly(size)
                        if header[0]==23 and not flipped:
                            body=body[:-1]+bytes([body[-1]^1])
                            flipped.append(True)
                        remote.write(header+body)
                        await remote.drain()
                async def server_to_client():
                    while True:
                        data=await upstream.read(65536)
                        if not data:
                            return
                        outgoing.write(data)
                        await outgoing.drain()
                pumps=[asyncio.create_task(client_to_server()),asyncio.create_task(server_to_client())]
                try:
                    done,pending=await asyncio.wait(pumps,return_when=asyncio.FIRST_COMPLETED)
                finally:
                    for pump in pumps:
                        pump.cancel()
                    await asyncio.gather(*pumps,return_exceptions=True)
            finally:
                outgoing.close()
                if remote:
                    remote.close()
                proxy_tasks.discard(task)
        proxy_server=None
        try:
            await server.start()
            proxy_server=await asyncio.start_server(proxy,'127.0.0.1',0)
            address=proxy_server.sockets[0].getsockname()[:2]
            with pytest.raises((ssl.SSLError,ConnectionError,asyncio.IncompleteReadError)):
                await client.send_delta(*address,a.make_delta(str(uuid4()),{'title':'Tampered'}))
            assert flipped and not applied
        finally:
            if proxy_server:
                proxy_server.close()
                await proxy_server.wait_closed()
            tasks=tuple(proxy_tasks)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)
            await server.close()
    asyncio.run(run())

@pytest.fixture
def durable_pair(tmp_path):
    import sqlite3
    from app.database.store import Store
    from app.database.sync_identity import prepare_identity_copy, inspect_identities
    from app.database.p2p_store import P2PStore
    from app.services.knowledge_graph import KnowledgeGraph
    workspace=str(uuid4())
    paths=[]
    actors=[]
    for name in ('node-a','node-b'):
        source=tmp_path/f'{name}-legacy.db'
        legacy=Store(source)
        legacy.db.close()
        path=prepare_identity_copy(source,tmp_path/f'{name}.db')
        KnowledgeGraph(path).refresh()
        with sqlite3.connect(path) as db:
            actors.append(inspect_identities(db)['device_uuid'])
        paths.append(path)
    stores=[P2PStore(path,workspace_id=workspace,local_workspace_id=1,actor_id=actors[index],
        authorized_actors=[actors[1-index]]) for index,path in enumerate(paths)]
    yield stores,paths
    for store in stores:
        store.close()


def durable_transport(certs, stores, direction=0, receiver=None):
    sender,recipient=stores[direction],stores[1-direction]
    sender_cert,recipient_cert=('a','b') if direction==0 else ('b','a')
    def apply(actor,workspace,wire):
        assert workspace==recipient.workspace_id
        return recipient.receive_delta(wire,actor)
    server=TLS13Server(server_context(*certs[recipient_cert][:2],certs['ca']),
        [PeerIdentity(sender.actor_id,sender.workspace_id,certs[sender_cert][2])],receiver or apply,
        local_actor_id=recipient.actor_id)
    client=TLS13Client(client_context(*certs[sender_cert][:2],certs['ca']),sender.actor_id,sender.workspace_id,
        PeerIdentity(recipient.actor_id,recipient.workspace_id,certs[recipient_cert][2]))
    return server,client


def test_durable_sqlite_outbox_inbox_roundtrip_graph_and_restart(certificates,durable_pair):
    async def run():
        import sqlite3
        from app.database.p2p_store import P2PStore
        from app.services.knowledge_graph import KnowledgeGraph
        stores,paths=durable_pair
        a,b=stores
        entity=str(uuid4())
        wire=a.mutate_note(entity,{'title':'Meeting','body':'Discuss [[Report]]'})
        server,client=durable_transport(certificates,stores)
        try:
            address=await server.start()
            assert a.pending_outbox(b.actor_id)==[wire]
            ack=await client.send_delta(*address,wire)
            assert b.get(entity)==a.get(entity)
            assert b.db.execute('SELECT title,body FROM notes').fetchone()==('Meeting','Discuss [[Report]]')
            assert b.db.execute('SELECT count(*) FROM sync_inbox').fetchone()[0]==1
            assert b.db.execute('SELECT count(*) FROM note_links_dirty').fetchone()[0]==1
            a.acknowledge(b.actor_id,ack)
            assert a.pending_outbox(b.actor_id)==[]
            before=b.db.total_changes
            await client.send_delta(*address,wire)
            assert b.db.total_changes==before
        finally:
            await server.close()
        b.close()
        reopened=P2PStore(paths[1],workspace_id=b.workspace_id,local_workspace_id=1,actor_id=b.actor_id,
            authorized_actors=[a.actor_id])
        try:
            assert reopened.get(entity)==a.get(entity)
            assert reopened.db.execute('SELECT count(*) FROM sync_inbox').fetchone()[0]==1
            assert list(reopened.db.execute('PRAGMA foreign_key_check'))==[]
        finally:
            reopened.close()
    asyncio.run(run())


def test_durable_commit_then_lost_ack_replays_without_extra_mutation(certificates,durable_pair):
    async def run():
        stores,_=durable_pair
        a,b=stores
        wire=a.mutate_note(str(uuid4()),{'title':'Exactly one materialization'})
        dropped=[]
        def commit_without_ack(actor,workspace,wire):
            b.receive_delta(wire,actor)
            dropped.append(True)
            raise ConnectionError('Connection lost after durable commit, before ACK')
        server,client=durable_transport(certificates,stores,receiver=commit_without_ack)
        try:
            address=await server.start()
            with pytest.raises(asyncio.IncompleteReadError):
                await client.send_delta(*address,wire)
            assert dropped and a.pending_outbox(b.actor_id)==[wire]
            assert b.db.execute('SELECT count(*) FROM notes').fetchone()[0]==1
            before=b.db.total_changes
            server.on_delta=lambda actor,workspace,wire:b.receive_delta(wire,actor)
            ack=await client.send_delta(*address,wire)
            a.acknowledge(b.actor_id,ack)
            assert not a.pending_outbox(b.actor_id)
            assert b.db.total_changes==before
            assert b.db.execute('SELECT count(*) FROM sync_inbox').fetchone()[0]==1
        finally:
            await server.close()
    asyncio.run(run())


def test_durable_both_directions_merge_concurrent_fields(certificates,durable_pair):
    async def run():
        stores,_=durable_pair
        a,b=stores
        entity=str(uuid4())
        wa=a.mutate_note(entity,{'title':'Shared title'})
        wb=b.mutate_note(entity,{'body':'Independent body'})
        ab,client_ab=durable_transport(certificates,stores)
        ba,client_ba=durable_transport(certificates,stores,direction=1)
        try:
            addr_ab,addr_ba=await asyncio.gather(ab.start(),ba.start())
            ack_b,ack_a=await asyncio.gather(client_ab.send_delta(*addr_ab,wa),client_ba.send_delta(*addr_ba,wb))
            a.acknowledge(b.actor_id,ack_b)
            b.acknowledge(a.actor_id,ack_a)
            assert a.get(entity)==b.get(entity)=={'title':'Shared title','body':'Independent body','lifecycle':'active'}
            assert a.pending_outbox(b.actor_id)==b.pending_outbox(a.actor_id)==[]
            for store in stores:
                assert not list(store.db.execute('PRAGMA foreign_key_check'))
        finally:
            await asyncio.gather(ab.close(),ba.close())
    asyncio.run(run())


def test_durable_revoked_actor_rejected_even_with_valid_tls(certificates,durable_pair):
    async def run():
        stores,_=durable_pair
        a,b=stores
        server,client=durable_transport(certificates,stores)
        wire=a.mutate_note(str(uuid4()),{'title':'Revoked'})
        b.revoke_peer(a.actor_id)
        try:
            address=await server.start()
            with pytest.raises(asyncio.IncompleteReadError):
                await client.send_delta(*address,wire)
            assert b.db.execute('SELECT count(*) FROM notes').fetchone()[0]==0
            assert b.db.execute('SELECT count(*) FROM sync_inbox').fetchone()[0]==0
            assert a.pending_outbox(b.actor_id)==[wire]
        finally:
            await server.close()
    asyncio.run(run())



def test_slow_synchronous_commit_never_emits_late_ack(certificates):
    async def run():
        import time
        committed=[]
        def slow_commit(*args):
            time.sleep(.08)
            committed.append(True)
        server,client,a,_=setup_pair(certificates,slow_commit,timeout=.03)
        client.timeout=2
        try:
            address=await server.start()
            with pytest.raises((TimeoutError,asyncio.IncompleteReadError,ConnectionResetError)):
                await client.send_delta(*address,a.make_delta(str(uuid4()),{'title':'Slow commit'}))
            assert committed
            assert server.rejected==1
        finally:
            await server.close()
    asyncio.run(run())



def test_maximum_escaped_delta_fits_bounded_transport_frame(certificates):
    async def run():
        server,client,a,b=setup_pair(certificates)
        entity=str(uuid4())
        body='\\'*100000
        wire=a.make_delta(entity,{'body':body})
        assert len(wire.encode())>200000
        try:
            address=await server.start()
            ack=await client.send_delta(*address,wire)
            assert ack['status']=='committed'
            assert a.get(entity)==b.get(entity)
        finally:
            await server.close()
    asyncio.run(run())

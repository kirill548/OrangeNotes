"""Explicit background owner of one SQLite replica and its TLS asyncio server.

No application startup hooks: callers enroll disposable/prepared databases and
paired certificates before constructing this worker. Futures carry errors to UI
controllers; the worker never accesses Qt widgets or another thread's SQLite.
"""
from __future__ import annotations
import asyncio
from concurrent.futures import Future
import threading

from app.database.p2p_store import P2PStore
from app.services.p2p_transport import TLS13Client, TLS13Server, TransportError


SYNC_DEADLINE_SECONDS = 30


class P2PNode:
    def __init__(self, path, *, workspace_id, local_workspace_id, actor_id,
                 authorized_actors, server_context, client_context, peers,
                 host='127.0.0.1', port=0, timeout=5):
        if not 0 < timeout <= 30:
            raise ValueError('Deadline must be at most 30 seconds')
        self._store_kwargs = dict(workspace_id=workspace_id,
            local_workspace_id=local_workspace_id, actor_id=actor_id,
            authorized_actors=tuple(authorized_actors))
        self._path, self._server_context, self._client_context = path, server_context, client_context
        self._peers = tuple(peers)
        self._host, self._port, self._timeout = host, port, timeout
        self._lock = threading.Lock()
        self._ready = Future()
        self._thread = None
        self._loop = self._store = self._server = None
        self._stopping = False
        self._address = None

    def start(self):
        with self._lock:
            if self._thread is not None:
                raise RuntimeError('Node is single-use and already started')
            self._thread = threading.Thread(target=self._run, name='OrangeNotes-P2P', daemon=True)
            self._thread.start()
        return self

    def wait_ready(self, timeout=10):
        return self._ready.result(timeout)

    @property
    def address(self):
        return self._address

    @property
    def is_alive(self):
        return self._thread is not None and self._thread.is_alive()

    def _run(self):
        try:
            asyncio.run(self._main())
        except BaseException as exc:
            if not self._ready.done():
                self._ready.set_exception(exc)
        finally:
            with self._lock:
                self._loop = None

    async def _main(self):
        self._loop = asyncio.get_running_loop()
        self._stop_event = asyncio.Event()
        try:
            self._store = P2PStore(self._path, **self._store_kwargs)
            self._server = TLS13Server(self._server_context, self._peers, self._receive,
                local_actor_id=self._store.actor_id, timeout=self._timeout)
            self._address = await self._server.start(self._host, self._port)
            self._ready.set_result(self._address)
            if self._stopping:
                self._stop_event.set()
            await self._stop_event.wait()
        finally:
            # Cancel outbound operations before closing the store they own.
            current = asyncio.current_task()
            tasks = [task for task in asyncio.all_tasks() if task is not current]
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            if self._server:
                await self._server.close()
            if self._store:
                self._store.close()
                self._store = None

    def _receive(self, actor, workspace, wire):
        if workspace != self._store.workspace_id:
            raise TransportError('Workspace mismatch')
        return self._store.receive_delta(wire, actor)

    def _submit(self, operation, *args):
        with self._lock:
            if not self._ready.done() or self._ready.exception() is not None or self._stopping or self._loop is None:
                failed = Future()
                failed.set_exception(RuntimeError('P2P node is not ready'))
                return failed
            return asyncio.run_coroutine_threadsafe(self._invoke(operation, args), self._loop)

    async def _invoke(self, operation, args):
        return getattr(self, operation)(*args)

    def mutate_note(self, entity_id, fields):
        return self._submit('_mutate', entity_id, dict(fields))

    def _mutate(self, entity_id, fields):
        return self._store.mutate_note(entity_id, fields)

    def get_note(self, entity_id):
        return self._submit('_get', entity_id)

    def _get(self, entity_id):
        return self._store.get(entity_id)

    def pending_outbox(self, peer_actor):
        return self._submit('_pending', peer_actor)

    def _pending(self, peer_actor):
        return self._store.pending_outbox(peer_actor)

    def sync_to(self, host, port, peer_actor, *, max_batch=32, server_hostname='localhost'):
        if type(max_batch) is not int or not 1 <= max_batch <= 128:
            failed = Future()
            failed.set_exception(ValueError('Sync batch must be between 1 and 128'))
            return failed
        return self._submit_async(self._sync(host, port, peer_actor, max_batch, server_hostname))

    def _submit_async(self, coroutine):
        with self._lock:
            if not self._ready.done() or self._ready.exception() is not None or self._stopping or self._loop is None:
                coroutine.close()
                failed = Future()
                failed.set_exception(RuntimeError('P2P node is not ready'))
                return failed
            return asyncio.run_coroutine_threadsafe(coroutine, self._loop)

    async def _sync(self, host, port, peer_actor, max_batch, hostname):
        peers = [peer for peer in self._peers if peer.actor_id == peer_actor and peer.workspace_id == self._store.workspace_id]
        if len(peers) != 1:
            raise TransportError('One paired peer required')
        client = TLS13Client(self._client_context, self._store.actor_id,
            self._store.workspace_id, peers[0], timeout=self._timeout)
        count = 0
        loop = asyncio.get_running_loop()
        deadline = loop.time() + SYNC_DEADLINE_SECONDS
        def check_deadline():
            if loop.time() >= deadline:
                raise TimeoutError('Sync transaction exceeded wall deadline')
        async with asyncio.timeout_at(deadline):
            pending = self._store.pending_outbox(peer_actor)[:max_batch]
            check_deadline()
            for wire in pending:
                # Pending reads and ACK commits are synchronous SQLite calls.
                # Explicit clock checks cover code paths with no actual I/O yield.
                await asyncio.sleep(0)
                check_deadline()
                ack = await client.send_delta(host, port, wire, server_hostname=hostname)
                check_deadline()
                self._store.acknowledge(peer_actor, ack)
                check_deadline()
                count += 1
            check_deadline()
        return count

    def stop(self, timeout=10):
        with self._lock:
            self._stopping = True
            loop = self._loop
            if loop is not None and hasattr(self, '_stop_event'):
                loop.call_soon_threadsafe(self._stop_event.set)
            thread = self._thread
        if thread is not None:
            if thread is threading.current_thread():
                raise RuntimeError('Cannot join P2P worker from itself')
            thread.join(timeout)
            if thread.is_alive():
                raise TimeoutError('P2P worker did not stop; database ownership retained')

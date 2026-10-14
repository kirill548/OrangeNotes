"""Explicit opt-in durable single-workspace replication on a prepared copy.

No GUI hooks and no implicit enrollment: applications must route synchronized
mutations through this boundary. TLS peer grants remain owned by the transport.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3

from app.services.p2p_sync import Replica, DeltaError, _uuid

SCHEMA = (
    'CREATE TABLE sync_p2p_schema(singleton INTEGER PRIMARY KEY CHECK(singleton=1), version INTEGER NOT NULL CHECK(version=1), workspace_id TEXT NOT NULL, local_workspace_id INTEGER NOT NULL, actor_id TEXT NOT NULL)',
    'CREATE TABLE sync_events(actor_id TEXT NOT NULL, clock INTEGER NOT NULL, wire TEXT NOT NULL, PRIMARY KEY(actor_id,clock))',
    'CREATE TABLE sync_outbox(sha256 TEXT PRIMARY KEY, wire TEXT NOT NULL)',
    'CREATE TABLE sync_outbox_ack(sha256 TEXT NOT NULL REFERENCES sync_outbox(sha256) ON DELETE CASCADE, peer_actor TEXT NOT NULL, PRIMARY KEY(sha256,peer_actor))',
    'CREATE TABLE sync_inbox(sha256 TEXT PRIMARY KEY, actor_id TEXT NOT NULL, clock INTEGER NOT NULL, UNIQUE(actor_id,clock))',
)


class P2PStore:
    """One enrolled workspace, synchronous transactions; own per worker thread.

    Peers are explicitly allowed actor UUIDs. Removing a peer immediately blocks
    both delivery and ACK processing. ACKs are peer-specific and do not delete the
    durable event history needed to reconstruct CRDT winners and conflicts.
    """

    def __init__(self, path, *, workspace_id, local_workspace_id, actor_id, authorized_actors=()):
        self.workspace_id, self.actor_id = _uuid(workspace_id), _uuid(actor_id)
        if type(local_workspace_id) is not int or local_workspace_id < 1:
            raise DeltaError('Invalid local workspace')
        self.local_workspace_id = local_workspace_id
        self.authorized_actors = {_uuid(actor) for actor in authorized_actors}
        if self.actor_id in self.authorized_actors:
            raise DeltaError('Local actor cannot be its own peer')
        path = Path(path).resolve(strict=True)
        self.db = sqlite3.connect(path)
        try:
            from app.database.sync_identity import _validate_metadata
            _validate_metadata(self.db)
            self.db.execute('PRAGMA foreign_keys=ON')
            self.db.execute('PRAGMA busy_timeout=5000')
            self.db.execute('PRAGMA journal_mode=WAL')
            self.db.execute('PRAGMA synchronous=FULL')
            if self.db.execute('SELECT device_uuid FROM sync_device WHERE singleton=1').fetchone()[0] != self.actor_id:
                raise DeltaError('Actor differs from enrolled device identity')
            with self._transaction():
                existing = self.db.execute("SELECT 1 FROM sqlite_master WHERE name='sync_p2p_schema'").fetchone()
                if not existing:
                    for statement in SCHEMA:
                        self.db.execute(statement)
                    self.db.execute('INSERT INTO sync_p2p_schema VALUES(1,1,?,?,?)',
                                    (self.workspace_id, self.local_workspace_id, self.actor_id))
                binding = self.db.execute('SELECT version,workspace_id,local_workspace_id,actor_id FROM sync_p2p_schema WHERE singleton=1').fetchone()
                if binding != (1, self.workspace_id, self.local_workspace_id, self.actor_id):
                    raise DeltaError('Store enrollment mismatch')
                row = self.db.execute('SELECT workspace_uuid FROM sync_workspaces WHERE local_id=?', (self.local_workspace_id,)).fetchone()
                if row is None:
                    raise DeltaError('Unknown local workspace')
                if row[0] != self.workspace_id:
                    if existing or self.db.execute('SELECT 1 FROM notes WHERE workspace_id=? LIMIT 1', (self.local_workspace_id,)).fetchone():
                        raise DeltaError('Workspace UUID mismatch; explicit empty workspace join required')
                    self.db.execute('UPDATE sync_workspaces SET workspace_uuid=? WHERE local_id=?', (self.workspace_id, self.local_workspace_id))
                replica = self._load_replica()
                if not existing:
                    for entity, title, body, deleted in self.db.execute("SELECT i.entity_uuid,n.title,n.body,n.deleted FROM notes n JOIN sync_identities i ON i.entity='notes' AND i.local_id=n.id AND i.tombstone=0 WHERE n.workspace_id=? ORDER BY n.id", (self.local_workspace_id,)).fetchall():
                        wire = replica.make_delta(entity, {'title':title,'body':body,'lifecycle':'deleted' if deleted else 'active'})
                        data = json.loads(wire)
                        self.db.execute('INSERT INTO sync_events VALUES(?,?,?)',(self.actor_id,data['clock'],wire))
                        self.db.execute('INSERT INTO sync_outbox VALUES(?,?)',(hashlib.sha256(wire.encode('utf-8')).hexdigest(),wire))
                self._verify_materialized(replica)
        except BaseException:
            self.db.close()
            raise

    def close(self):
        self.db.close()

    @contextmanager
    def _transaction(self):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            yield
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def _load_replica(self):
        replica = Replica(self.workspace_id, self.actor_id)
        for actor,clock,wire in self.db.execute('SELECT actor_id,clock,wire FROM sync_events ORDER BY clock,actor_id'):
            if not replica.apply_delta(wire) or replica._dots.get((actor,clock)) != wire:
                raise DeltaError('Corrupt durable event identity or encoding')
        return replica

    def _peer(self, actor):
        actor = _uuid(actor)
        if actor not in self.authorized_actors:
            raise DeltaError('Actor not authorized for workspace')
        return actor

    def grant_peer(self, actor):
        actor = _uuid(actor)
        if actor == self.actor_id:
            raise DeltaError('Local actor cannot be its own peer')
        self.authorized_actors.add(actor)

    def revoke_peer(self, actor):
        self.authorized_actors.discard(_uuid(actor))

    def _mapping(self, entity):
        rows = self.db.execute("SELECT i.local_id,i.tombstone,n.workspace_id FROM sync_identities i LEFT JOIN notes n ON n.id=i.local_id WHERE i.entity='notes' AND i.entity_uuid=?", (entity,)).fetchall()
        if not rows:
            return None
        local_id, tombstone, workspace = rows[0]
        if tombstone or workspace != self.local_workspace_id:
            raise DeltaError('Foreign or physically removed note identity')
        return local_id

    def _verify_materialized(self, replica):
        rows = self.db.execute("SELECT i.entity_uuid,n.title,n.body,n.deleted FROM notes n JOIN sync_identities i ON i.entity='notes' AND i.local_id=n.id AND i.tombstone=0 WHERE n.workspace_id=?",(self.local_workspace_id,)).fetchall()
        mapped = {row[0] for row in rows}
        if mapped != set(replica._entities):
            raise DeltaError('Out-of-band note changes; explicit reconciliation required')
        for entity,title,body,deleted in rows:
            state = replica.get(entity)
            actual = {'title':title,'body':body,'lifecycle':'deleted' if deleted else 'active'}
            expected = {'title':state.get('title',''),'body':state.get('body',''),'lifecycle':state['lifecycle']}
            if actual != expected:
                raise DeltaError('Out-of-band note changes; explicit reconciliation required')

    def _materialize(self, replica, data):
        stamp = datetime.now(timezone.utc).isoformat(timespec='seconds')
        for op in data['ops']:
            entity = op['entity_id']
            local_id = self._mapping(entity)
            state = replica.get(entity)
            if local_id is None:
                local_id = self.db.execute('INSERT INTO notes(title,body,deleted,workspace_id,updated_at,created_at) VALUES(?,?,?,?,?,?)',
                    (state.get('title',''), state.get('body',''), int(state['lifecycle']=='deleted'), self.local_workspace_id, stamp, stamp)).lastrowid
                # The established lifetime trigger created a fresh local identity;
                # bind that new row to the authenticated remote entity UUID.
                self.db.execute("UPDATE sync_identities SET entity_uuid=? WHERE entity='notes' AND local_id=? AND tombstone=0", (entity, local_id))
            else:
                current = self.db.execute('SELECT title,body,deleted FROM notes WHERE id=?', (local_id,)).fetchone()
                self.db.execute('UPDATE notes SET title=?,body=?,deleted=?,updated_at=? WHERE id=?',
                    (state.get('title',current[0]), state.get('body',current[1]), int(state['lifecycle']=='deleted'), stamp, local_id))

    def _persist(self, replica, wire):
        data = json.loads(wire)
        self._materialize(replica, data)
        self.db.execute('INSERT INTO sync_events VALUES(?,?,?)', (data['actor_id'], data['clock'], wire))
        return data

    def mutate_note(self, entity_id, fields):
        """Commit note and canonical outbox event together or neither."""
        entity_id = _uuid(entity_id)
        with self._transaction():
            replica = self._load_replica()
            self._verify_materialized(replica)
            self._mapping(entity_id)
            wire = replica.make_delta(entity_id, fields)
            self._persist(replica, wire)
            self.db.execute('INSERT INTO sync_outbox VALUES(?,?)', (hashlib.sha256(wire.encode('utf-8')).hexdigest(), wire))
        return wire

    create_note = mutate_note
    edit_note = mutate_note

    def receive_delta(self, wire, authorized_actor):
        """Return a receipt only after atomic inbox + note + event commit."""
        actor = self._peer(authorized_actor)
        with self._transaction():
            self._peer(actor)
            replica = self._load_replica()
            self._verify_materialized(replica)
            accepted = replica.apply_delta(wire)
            canonical = replica._dots[(actor, json.loads(wire)['clock'])] if json.loads(wire)['actor_id'] == actor else None
            if canonical is None:
                raise DeltaError('Wire actor differs from authenticated peer')
            digest = hashlib.sha256(canonical.encode('utf-8')).hexdigest()
            if accepted:
                data = self._persist(replica, canonical)
                self.db.execute('INSERT INTO sync_inbox VALUES(?,?,?)', (digest, actor, data['clock']))
            self._peer(actor)
        return {'sha256':digest, 'actor_id':self.actor_id, 'workspace_id':self.workspace_id}

    def pending_outbox(self, peer_actor):
        peer = self._peer(peer_actor)
        return [row[0] for row in self.db.execute('SELECT o.wire FROM sync_outbox o WHERE NOT EXISTS(SELECT 1 FROM sync_outbox_ack a WHERE a.sha256=o.sha256 AND a.peer_actor=?) ORDER BY o.rowid', (peer,))]

    def acknowledge(self, peer_actor, receipt):
        peer = self._peer(peer_actor)
        if not isinstance(receipt, dict) or receipt.get('actor_id') != peer or receipt.get('workspace_id') != self.workspace_id:
            raise DeltaError('Invalid ACK identity')
        digest = receipt.get('sha256')
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
            raise DeltaError('Invalid ACK digest')
        with self._transaction():
            self._peer(peer)
            if not self.db.execute('SELECT 1 FROM sync_outbox WHERE sha256=?', (digest,)).fetchone():
                raise DeltaError('Unknown ACK')
            self.db.execute('INSERT OR IGNORE INTO sync_outbox_ack VALUES(?,?)', (digest, peer))

    def get(self, entity_id):
        return self._load_replica().get(entity_id)

    def body_conflicts(self, entity_id):
        return self._load_replica().body_conflicts(entity_id)

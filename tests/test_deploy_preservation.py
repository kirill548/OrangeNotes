"""Deployment keeps live databases, portable state and models outside payload."""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from scripts.deploy_local import MARKER, deploy_bundle


class DeployPreservationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.bundle = self.root / 'source' / 'OrangeNotes'
        self.bundle.mkdir(parents=True)
        (self.bundle / 'OrangeNotes').write_bytes(b'new executable')
        self.target = self.root / 'installed'
        self.target.mkdir()

    def test_live_wal_database_and_portable_user_state_survive_two_updates(self):
        database = self.target / 'notes.sqlite3'
        connection = sqlite3.connect(database)
        self.addCleanup(connection.close)
        connection.execute('PRAGMA journal_mode=WAL')
        connection.execute('PRAGMA wal_autocheckpoint=0')
        connection.execute('CREATE TABLE notes(body TEXT)')
        connection.execute('INSERT INTO notes VALUES (?)', ('Private note',))
        connection.commit()
        files = {
            'settings.json': b'{"language":"ru"}',
            'ai_settings.json': b'{"model":"qwen3:4b"}',
            'ai_packs/models/blob': b'managed model weights',
            'runtime/models/blob': b'legacy model weights',
            'runtime/ollama_profile/state': b'profile',
            'custom/nested/file': b'user file',
        }
        for name, data in files.items():
            path = self.target / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        database_files = [database, database.with_name(database.name + '-wal'),
                          database.with_name(database.name + '-shm')]
        snapshots = {path: path.read_bytes() for path in database_files}
        for version in (b'v1', b'v2'):
            (self.bundle / 'OrangeNotes').write_bytes(version)
            installed = deploy_bundle(self.bundle, self.target, 'linux')
            self.assertEqual((installed / 'OrangeNotes').read_bytes(), version)
            for path, data in snapshots.items():
                self.assertEqual(path.read_bytes(), data)
            for name, data in files.items():
                self.assertEqual((self.target / name).read_bytes(), data)
        self.assertEqual(connection.execute('SELECT body FROM notes').fetchone()[0], 'Private note')

    def test_unknown_file_inside_previous_payload_refuses_update_without_deletion(self):
        installed = deploy_bundle(self.bundle, self.target, 'linux')
        private_file = installed / 'my-private-file.txt'
        private_file.write_bytes(b'private')
        with self.assertRaises(ValueError):
            deploy_bundle(self.bundle, self.target, 'linux')
        self.assertEqual(private_file.read_bytes(), b'private')
        self.assertEqual((installed / 'OrangeNotes').read_bytes(), b'new executable')

    def test_forged_inventory_cannot_authorize_deletion_of_database(self):
        installed = deploy_bundle(self.bundle, self.target, 'linux')
        database = installed / 'notes.sqlite3'
        database.write_bytes(b'user database')
        marker = self.target / MARKER
        previous = json.loads(marker.read_text(encoding='utf-8'))
        previous['files'].append('OrangeNotes/notes.sqlite3')
        marker.write_text(json.dumps(previous), encoding='utf-8')
        with self.assertRaises(ValueError):
            deploy_bundle(self.bundle, self.target, 'linux')
        self.assertEqual(database.read_bytes(), b'user database')

    def test_manifest_failure_restores_previous_executable_and_user_files(self):
        installed = deploy_bundle(self.bundle, self.target, 'linux')
        old_marker = (self.target / MARKER).read_bytes()
        (self.target / 'settings.json').write_bytes(b'private settings')
        (self.bundle / 'OrangeNotes').write_bytes(b'newer executable')
        original_replace = Path.replace

        def fail_manifest(path, destination):
            if path.name.startswith('.manifest-'):
                raise OSError('simulated manifest write failure')
            return original_replace(path, destination)

        with patch.object(Path, 'replace', fail_manifest):
            with self.assertRaises(OSError):
                deploy_bundle(self.bundle, self.target, 'linux')
        self.assertEqual((installed / 'OrangeNotes').read_bytes(), b'new executable')
        self.assertEqual((self.target / MARKER).read_bytes(), old_marker)
        self.assertEqual((self.target / 'settings.json').read_bytes(), b'private settings')


if __name__ == '__main__':
    unittest.main()

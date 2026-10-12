"""Edition switches preserve portable user state and reject unverified payloads."""
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from scripts import deploy_local as deploy


class EditionDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bundle = self.root / 'source/OrangeNotes'
        self.bundle.mkdir(parents=True)
        (self.bundle / 'OrangeNotes').write_bytes(b'executable')
        self.target = self.root / 'installed'
        self.target.mkdir()
        for name, data in {'notes.db': b'private database', 'settings.json': b'private settings',
                           'ai_settings.json': b'external runtime selection',
                           'ai_packs/models/user-model': b'private weights'}.items():
            path = self.target / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)

    def test_portable_database_settings_are_verified_and_change_detected(self):
        before = deploy.snapshot_user_state(self.target)
        deploy.deploy_bundle(self.bundle, self.target, 'linux',
                             verify=lambda _: deploy.verify_user_state(self.target, before))
        (self.target / 'settings.json').write_bytes(b'changed')
        with self.assertRaises(RuntimeError):
            deploy.verify_user_state(self.target, before)

    def test_complete_to_lite_keeps_user_models_and_preferences(self):
        seed = self.bundle / 'bundled_ai'
        (seed / 'models').mkdir(parents=True)
        (seed / 'models/weight').write_bytes(b'bundled weights')
        before = deploy.snapshot_user_state(self.target)
        with patch('app.services.bundled_ai.find_complete_seed', return_value=(seed, {})):
            deploy.deploy_bundle(self.bundle, self.target, 'linux')
        (seed / 'models/weight').unlink()
        (seed / 'models').rmdir()
        seed.rmdir()
        previous_seed = self.target / 'package/OrangeNotes/bundled_ai'
        with patch('app.services.bundled_ai.find_complete_seed', return_value=(previous_seed, {})):
            deploy.deploy_bundle(self.bundle, self.target, 'linux')
        deploy.verify_user_state(self.target, before)
        self.assertEqual((self.target / 'ai_packs/models/user-model').read_bytes(), b'private weights')

    def test_unverified_seed_never_authorizes_model_deletion(self):
        seed = self.bundle / 'bundled_ai'
        (seed / 'models').mkdir(parents=True)
        (seed / 'models/weight').write_bytes(b'weights')
        with patch('app.services.bundled_ai.find_complete_seed', side_effect=ValueError('bad checksum')):
            with self.assertRaises(ValueError):
                deploy.deploy_bundle(self.bundle, self.target, 'linux')
        self.assertFalse((self.target / 'package').exists())

    def test_complete_requires_pack_before_cleanup(self):
        with patch.object(deploy, 'clean_workspace') as cleanup:
            self.assertEqual(deploy.main(['--edition', 'complete', '--target', str(self.target), '--no-launch']), 1)
        cleanup.assert_not_called()

    def test_lite_refuses_seed_argument_before_cleanup(self):
        with patch.object(deploy, 'clean_workspace') as cleanup:
            self.assertEqual(deploy.main(['--edition', 'lite', '--ai-pack', str(self.root), '--no-launch']), 1)
        cleanup.assert_not_called()

    def test_complete_build_command_is_isolated_and_forwards_seed(self):
        import subprocess
        workspace = self.root / 'workspace'
        workspace.mkdir()
        with patch.object(deploy, 'ROOT', workspace), patch.object(deploy, 'assert_application_closed'), \
                patch.object(deploy.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, ['build'])) as build:
            self.assertEqual(deploy.main(['--edition', 'complete', '--ai-pack', str(self.root),
                                        '--target', str(self.target), '--no-launch']), 1)
        command = build.call_args.args[0]
        self.assertEqual(command[command.index('--edition') + 1], 'complete')
        self.assertEqual(command[command.index('--ai-pack') + 1], str(self.root.resolve()))
        self.assertIn('complete', command[command.index('--dist-dir') + 1])

    def test_verified_internal_complete_to_lite_preserves_real_sqlite(self):
        import shutil
        import sqlite3
        from contextlib import closing
        from tests.test_bundled_ai import BundledAITests
        fixture = BundledAITests('test_verified_seed_and_install')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        internal = self.bundle / '_internal/bundled_ai'
        shutil.copytree(fixture.seed, internal)
        database = self.target / 'notes.db'
        database.unlink()
        with closing(sqlite3.connect(database)) as connection:
            connection.execute('CREATE TABLE notes (body TEXT)')
            connection.execute('INSERT INTO notes VALUES (?)', ('Private unchanged note',))
            connection.commit()
        before = deploy.snapshot_user_state(self.target)
        deploy.deploy_bundle(self.bundle, self.target, 'linux',
                             verify=lambda _: deploy.verify_user_state(self.target, before))
        shutil.rmtree(internal)
        deploy.deploy_bundle(self.bundle, self.target, 'linux',
                             verify=lambda _: deploy.verify_user_state(self.target, before))
        with closing(sqlite3.connect(database)) as connection:
            self.assertEqual(connection.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(connection.execute('SELECT body FROM notes').fetchone()[0], 'Private unchanged note')

    def test_even_verified_seed_exemption_cannot_delete_private_database(self):
        seed = self.bundle / 'bundled_ai'
        seed.mkdir()
        (seed / 'notes.db').write_bytes(b'private')
        with patch('app.services.bundled_ai.find_complete_seed', return_value=(seed, {})):
            with self.assertRaises(ValueError):
                deploy.deploy_bundle(self.bundle, self.target, 'linux')
        self.assertFalse((self.target / 'package').exists())

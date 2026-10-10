"""Local deployment must replace code without ever deleting user state."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch


def load_deploy():
    filename = Path(__file__).resolve().parents[1] / 'scripts/deploy_local.py'
    spec = importlib.util.spec_from_file_location('deploy_local_tests_subject', filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LocalDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.deploy = load_deploy()
        # Every CLI test is isolated from a developer's running real instance.
        default_guard = patch.object(self.deploy, 'default_database_path')
        self.default_database = default_guard.start()
        self.addCleanup(default_guard.stop)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.default_database.return_value = self.root / 'userdata/notes.db'
        self.source = self.root / 'source/OrangeNotes'
        self.source.mkdir(parents=True)
        (self.source / 'OrangeNotes.exe').write_bytes(b'new executable')
        (self.source / '_internal').mkdir()
        (self.source / '_internal/resource.dat').write_bytes(b'resource')
        self.target = self.root / 'installed'
        self.target.mkdir()

    def install(self):
        return self.deploy.deploy_bundle(self.source, self.target, platform_name='win32')

    def test_install_and_update_preserve_user_state_byte_for_byte(self):
        preserved = {'notes.db': b'SQLite user data', 'settings.json': b'{"theme":"orange"}',
                     'ai_packs/models/model.gguf': b'model weights'}
        for name, value in preserved.items():
            path = self.target / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(value)
        installed = self.install()
        self.assertEqual((installed / 'OrangeNotes.exe').read_bytes(), b'new executable')
        (self.source / 'OrangeNotes.exe').write_bytes(b'updated executable')
        self.install()
        self.assertEqual((installed / 'OrangeNotes.exe').read_bytes(), b'updated executable')
        for name, value in preserved.items():
            self.assertEqual((self.target / name).read_bytes(), value)

    def test_unmanaged_existing_package_is_never_overwritten(self):
        existing = self.target / 'package'
        existing.mkdir()
        (existing / 'OrangeNotes.exe').write_bytes(b'unmanaged executable')
        with self.assertRaises((ValueError, RuntimeError, OSError)):
            self.install()
        self.assertEqual((existing / 'OrangeNotes.exe').read_bytes(), b'unmanaged executable')

    def test_user_files_added_inside_installed_package_block_update(self):
        installed = self.install()
        private = installed / 'notes.db'
        private.write_bytes(b'private database')
        with self.assertRaises((ValueError, RuntimeError, OSError)):
            self.install()
        self.assertEqual(private.read_bytes(), b'private database')

    def test_cleanup_removes_generated_caches_and_preserves_dependencies_and_packages(self):
        workspace = self.root / 'workspace'
        for name in ('app/__pycache__/module.pyc', '.pytest_cache/cache', 'build/generated.tmp',
                     'work/qa-runtime/__pycache__/dependency.pyc', 'dist/release.zip',
                     'notes.db', 'settings.json', 'ai_packs/models/model.gguf'):
            path = workspace / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(name.encode())
        self.deploy.clean_workspace(workspace)
        for name in ('app/__pycache__', '.pytest_cache', 'build'):
            self.assertFalse((workspace / name).exists(), name)
        for name in ('work/qa-runtime/__pycache__/dependency.pyc', 'dist/release.zip',
                     'notes.db', 'settings.json', 'ai_packs/models/model.gguf'):
            self.assertTrue((workspace / name).exists(), name)

    def test_cleanup_preserves_build_containing_user_data(self):
        workspace = self.root / 'workspace'
        private = workspace / 'build/notes.db'
        private.parent.mkdir(parents=True)
        private.write_bytes(b'private database')
        try:
            self.deploy.clean_workspace(workspace)
        except (ValueError, RuntimeError, OSError):
            pass
        self.assertEqual(private.read_bytes(), b'private database')

    def test_cleanup_preserves_custom_install_target_and_legacy_runtime(self):
        workspace = self.root / 'workspace'
        install = workspace / 'app/custom-install'
        preserved = [install / '__pycache__/installed.pyc',
                     workspace / 'runtime/__pycache__/model.pyc',
                     workspace / 'work/arbitrary-dependencies/__pycache__/library.pyc']
        source_cache = workspace / 'app/services/__pycache__/source.pyc'
        for path in [*preserved, source_cache]:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'cache')
        self.deploy.clean_workspace(workspace, preserve=(install,))
        self.assertFalse(source_cache.exists())
        for path in preserved:
            self.assertEqual(path.read_bytes(), b'cache')

    def test_non_bytecode_file_inside_pycache_is_preserved(self):
        workspace = self.root / 'workspace'
        private = workspace / 'app/__pycache__/my-private-notes.txt'
        private.parent.mkdir(parents=True)
        private.write_text('private text')
        try:
            self.deploy.clean_workspace(workspace)
        except (ValueError, RuntimeError, OSError):
            pass
        self.assertEqual(private.read_text(), 'private text')

    def test_log_cleanup_is_age_limited_and_preserves_unrelated_logs(self):
        workspace = self.root / 'workspace'
        folder = workspace / 'work/deploy-local'
        folder.mkdir(parents=True)
        old = folder / 'deploy-old.log'
        recent = folder / 'deploy-recent.log'
        unrelated = folder / 'user-private.log'
        for path in (old, recent, unrelated):
            path.write_text('log')
        expired = time.time() - 8 * 86400
        os.utime(old, (expired, expired))
        os.utime(unrelated, (expired, expired))
        self.deploy.clean_workspace(workspace)
        self.assertFalse(old.exists())
        self.assertTrue(recent.exists())
        self.assertTrue(unrelated.exists())

    def test_symlink_install_target_is_rejected(self):
        outside = self.root / 'outside'
        outside.mkdir()
        link = self.root / 'linked-target'
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest('Creating symlinks requires privileges on this Windows host')
        with self.assertRaises((ValueError, RuntimeError, OSError)):
            self.deploy.deploy_bundle(self.source, link, platform_name='win32')
        self.assertEqual(list(outside.iterdir()), [])

    def test_manifest_write_failure_rolls_back_old_application(self):
        installed = self.install()
        marker = self.target / self.deploy.MARKER
        original_manifest = marker.read_bytes()
        (self.source / 'OrangeNotes.exe').write_bytes(b'failed update')
        original_write = Path.write_text

        def fail_manifest(path, *args, **kwargs):
            if path.name.startswith('.manifest-'):
                raise OSError('simulated disk full')
            return original_write(path, *args, **kwargs)

        with patch.object(Path, 'write_text', fail_manifest):
            with self.assertRaises(OSError):
                self.install()
        self.assertEqual((installed / 'OrangeNotes.exe').read_bytes(), b'new executable')
        self.assertEqual(marker.read_bytes(), original_manifest)
        self.assertFalse(list(self.target.glob('.stage-*')))

    def test_startup_verification_failure_restores_previous_version(self):
        installed = self.install()
        (self.source / 'OrangeNotes.exe').write_bytes(b'broken executable')

        def reject_startup(bundle):
            self.assertEqual((bundle / 'OrangeNotes.exe').read_bytes(), b'broken executable')
            raise RuntimeError('Qt platform plugin initialization failed')

        with self.assertRaises(RuntimeError):
            self.deploy.deploy_bundle(self.source, self.target, platform_name='win32',
                                      verify=reject_startup)
        self.assertEqual((installed / 'OrangeNotes.exe').read_bytes(), b'new executable')

    def test_failed_build_does_not_replace_current_application(self):
        installed = self.install()
        workspace = self.root / 'workspace'
        workspace.mkdir()
        with patch.object(self.deploy, 'ROOT', workspace), \
                patch.object(self.deploy.subprocess, 'run',
                             side_effect=subprocess.CalledProcessError(1, ['build'])):
            result = self.deploy.main(['--target', str(self.target), '--no-launch'])
        self.assertEqual(result, 1)
        self.assertEqual((installed / 'OrangeNotes.exe').read_bytes(), b'new executable')

    def test_running_application_blocks_update_before_cleanup_and_build(self):
        workspace = self.root / 'workspace'
        workspace.mkdir()
        database = self.root / 'userdata/notes.db'
        database.parent.mkdir()
        lock = database.parent / 'app.lock'
        lock.write_text('12345\nOrange Notes')
        with patch.object(self.deploy, 'ROOT', workspace), \
                patch.object(self.deploy, '_process_alive', return_value=True), \
                patch.object(self.deploy, 'clean_workspace') as cleanup, \
                patch.object(self.deploy.subprocess, 'run') as build:
            result = self.deploy.main(['--target', str(self.target), '--no-launch',
                                      '--database', str(database)])
        self.assertEqual(result, 1)
        cleanup.assert_not_called()
        build.assert_not_called()
        self.assertEqual(lock.read_text(), '12345\nOrange Notes')

    def test_stale_application_lock_allows_build_and_is_not_deleted(self):
        workspace = self.root / 'workspace'
        workspace.mkdir()
        database = self.root / 'userdata/notes.db'
        database.parent.mkdir()
        lock = database.parent / 'app.lock'
        lock.write_text('99999999\nOrange Notes')
        with patch.object(self.deploy, 'ROOT', workspace), \
                patch.object(self.deploy, '_process_alive', return_value=False), \
                patch.object(self.deploy.subprocess, 'run',
                             side_effect=subprocess.CalledProcessError(1, ['build'])) as build:
            result = self.deploy.main(['--target', str(self.target), '--no-launch',
                                      '--database', str(database)])
        self.assertEqual(result, 1)  # injected build error, not application guard
        build.assert_called_once()
        self.assertEqual(lock.read_text(), '99999999\nOrange Notes')

    def test_build_uses_isolated_output_without_touching_existing_distribution(self):
        workspace = self.root / 'workspace'
        private = workspace / 'dist/OrangeNotes/runtime/models/private.gguf'
        private.parent.mkdir(parents=True)
        private.write_bytes(b'old models')
        with patch.object(self.deploy, 'ROOT', workspace), \
                patch.object(self.deploy.subprocess, 'run',
                             side_effect=subprocess.CalledProcessError(1, ['build'])) as build:
            result = self.deploy.main(['--target', str(self.target), '--no-launch'])
        self.assertEqual(result, 1)
        command = build.call_args.args[0]
        self.assertIn('--dist-dir', command)
        output = Path(command[command.index('--dist-dir') + 1])
        self.assertEqual(output, workspace / 'work/deploy-local/dist')
        self.assertEqual(private.read_bytes(), b'old models')

    def test_managed_build_output_containing_models_blocks_pyinstaller(self):
        workspace = self.root / 'workspace'
        private = workspace / 'work/deploy-local/dist' / ('OrangeNotes.app' if self.deploy.sys.platform == 'darwin' else 'OrangeNotes') / 'runtime/models/private.gguf'
        private.parent.mkdir(parents=True)
        private.write_bytes(b'private model')
        with patch.object(self.deploy, 'ROOT', workspace), \
                patch.object(self.deploy.subprocess, 'run') as build:
            result = self.deploy.main(['--target', str(self.target), '--no-launch'])
        self.assertEqual(result, 1)
        build.assert_not_called()
        self.assertEqual(private.read_bytes(), b'private model')

    def test_target_under_build_is_rejected_before_cleanup(self):
        workspace = self.root / 'workspace'
        target = workspace / 'build/install'
        target.mkdir(parents=True)
        sentinel = target / 'OrangeNotes.exe'
        sentinel.write_bytes(b'old working application')
        with patch.object(self.deploy, 'ROOT', workspace), \
                patch.object(self.deploy.subprocess, 'run') as build:
            result = self.deploy.main(['--target', str(target), '--no-launch'])
        self.assertEqual(result, 1)
        self.assertEqual(sentinel.read_bytes(), b'old working application')
        build.assert_not_called()

    def test_cleanup_preserves_user_database_misplaced_inside_cache(self):
        workspace = self.root / 'workspace'
        private = workspace / 'app/__pycache__/notes.db'
        private.parent.mkdir(parents=True)
        private.write_bytes(b'private database')
        try:
            self.deploy.clean_workspace(workspace)
        except (ValueError, RuntimeError, OSError):
            pass
        self.assertEqual(private.read_bytes(), b'private database')

    def test_symlink_build_cleanup_never_follows_external_target(self):
        workspace = self.root / 'workspace'
        workspace.mkdir()
        outside = self.root / 'outside'
        outside.mkdir()
        sentinel = outside / 'keep.txt'
        sentinel.write_text('keep')
        try:
            (workspace / 'build').symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest('Creating symlinks requires privileges on this Windows host')
        try:
            self.deploy.clean_workspace(workspace)
        except (ValueError, RuntimeError, OSError):
            pass
        self.assertEqual(sentinel.read_text(), 'keep')

    def test_linked_build_is_rejected_before_inventory_read(self):
        workspace = self.root / 'workspace'
        build = workspace / 'build'
        build.mkdir(parents=True)
        # cleanup canonicalizes its root. macOS /var and Windows short temp
        # names can describe the same directory with different lexical paths.
        workspace = workspace.resolve()
        build = workspace / 'build'
        original = Path.is_symlink

        def linked(path):
            return path == build or original(path)

        with patch.object(Path, 'is_symlink', linked), \
                patch.object(self.deploy, '_files') as inventory:
            with self.assertRaises((ValueError, RuntimeError, OSError)):
                self.deploy.clean_workspace(workspace)
        inventory.assert_not_called()


if __name__ == '__main__':
    unittest.main()



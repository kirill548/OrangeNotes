import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.services.ai_pack_storage import AIPackStorage, StorageSafetyError


def add_model(root, name, digests):
    model, tag = name.split(':')
    manifest = root / 'manifests/registry.ollama.ai/library' / model / tag
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({'layers': [{'digest': 'sha256:' + digest} for digest in digests]}))
    for digest in digests:
        blob = root / 'blobs' / ('sha256-' + digest)
        blob.parent.mkdir(exist_ok=True)
        if not blob.exists():
            blob.write_bytes(b'model data')
    return manifest


class PackStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'models'
        self.storage = AIPackStorage(self.root)
        self.storage.initialize()

    def test_existing_nonempty_folder_is_never_adopted(self):
        external = Path(self.temp.name) / 'external'
        external.mkdir()
        (external / 'keep').write_text('untouched')
        with self.assertRaises(StorageSafetyError):
            AIPackStorage(external).initialize()
        self.assertEqual((external / 'keep').read_text(), 'untouched')
        self.assertFalse((external / AIPackStorage.MARKER).exists())

    def test_model_names_cannot_escape_storage(self):
        for name in ('../outside', 'a/b', 'a\\b', 'a:../../x', 'a:b:c', '', 'a..b'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.storage.delete_model(name)

    def test_delete_preserves_shared_blobs_and_other_manifests(self):
        first = add_model(self.root, 'one:latest', ['a' * 64, 'b' * 64])
        second = add_model(self.root, 'two:latest', ['a' * 64])
        entries = self.storage.scan()
        one = next(item for item in entries if item.name == 'one:latest')
        expected = first.stat().st_size + 10
        self.assertEqual(one.size_bytes, 20)
        self.assertEqual(one.reclaimable_bytes, expected)
        self.assertEqual(self.storage.delete_model('one:latest'), expected)
        self.assertFalse(first.exists())
        self.assertTrue(second.exists())
        self.assertTrue((self.root / 'blobs' / ('sha256-' + 'a' * 64)).exists())
        self.assertFalse((self.root / 'blobs' / ('sha256-' + 'b' * 64)).exists())

    def test_malformed_other_manifest_blocks_deletion(self):
        first = add_model(self.root, 'one:latest', ['a' * 64])
        bad = self.root / 'manifests/broken'
        bad.write_text('{')
        with self.assertRaises(StorageSafetyError):
            self.storage.delete_model('one:latest')
        self.assertTrue(first.exists())

    def test_link_anywhere_in_tree_blocks_scan_and_delete(self):
        manifest = add_model(self.root, 'one:latest', ['a' * 64])
        external = Path(self.temp.name) / 'external'
        external.write_text('keep')
        link = self.root / 'unsafe'
        try:
            link.symlink_to(external)
        except OSError:
            self.skipTest('This Windows account cannot create symbolic links')
        for operation in (self.storage.scan, lambda: self.storage.delete_model('one:latest')):
            with self.assertRaises(StorageSafetyError):
                operation()
        self.assertTrue(manifest.exists())
        self.assertEqual(external.read_text(), 'keep')

    def test_cleanup_only_claimed_new_partial_files(self):
        blobs = self.root / 'blobs'
        blobs.mkdir()
        old = blobs / ('sha256-' + 'a' * 64 + '-partial')
        old.write_bytes(b'old')
        snapshot = self.storage.begin_operation()
        new = blobs / ('sha256-' + 'b' * 64 + '-partial-0')
        new.write_bytes(b'new')
        complete = blobs / ('sha256-' + 'c' * 64)
        complete.write_bytes(b'complete')
        for path in (old, complete):
            with self.assertRaises(StorageSafetyError):
                self.storage.register_partial(snapshot, path.relative_to(self.root).as_posix())
        self.storage.register_partial(snapshot, new.relative_to(self.root).as_posix())
        self.assertEqual(self.storage.cleanup_partial(snapshot), 3)
        self.assertFalse(new.exists())
        self.assertEqual(old.read_bytes(), b'old')
        self.assertEqual(complete.read_bytes(), b'complete')
        with self.assertRaises(StorageSafetyError):
            self.storage.cleanup_partial(snapshot)

    def test_unregistered_new_partial_is_not_deleted(self):
        snapshot = self.storage.begin_operation()
        partial = self.root / 'blobs' / ('sha256-' + 'a' * 64 + '-partial')
        partial.parent.mkdir()
        partial.write_bytes(b'unclaimed')
        self.assertEqual(self.storage.cleanup_partial(snapshot), 0)
        self.assertTrue(partial.exists())

    def test_windows_reparse_point_is_rejected_without_following_it(self):
        with patch.object(Path, 'lstat', return_value=SimpleNamespace(st_mode=0o040755, st_file_attributes=0x400)):
            with self.assertRaises(StorageSafetyError):
                self.storage._links(self.root)

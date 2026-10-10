"""macOS system aliases must not weaken managed-storage link checks."""
from pathlib import Path
import stat
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.services.ai_pack_storage import AIPackStorage, StorageSafetyError


class DarwinStorageAliasesTests(unittest.TestCase):
    def metadata(self, mode=stat.S_IFDIR | 0o755, uid=0):
        return SimpleNamespace(st_mode=mode, st_uid=uid, st_file_attributes=0)

    def test_standard_aliases_allow_storage_below_system_directory(self):
        for name in ('var',):
            for target in ('private/' + name, '/private/' + name):
                with self.subTest(alias=name, target=target):
                    alias = Path('/') / name
                    storage = AIPackStorage(alias / 'folders/session/ai_packs/models')
                    # Retain the POSIX-shaped spelling when simulating Darwin on Windows.
                    storage.root = alias / 'folders/session/ai_packs/models'
                    def metadata(path):
                        return self.metadata(stat.S_IFLNK | 0o777) if path == alias else self.metadata()
                    with patch('app.services.ai_pack_storage.sys.platform', 'darwin'), \
                            patch.object(Path, 'lstat', autospec=True, side_effect=metadata), \
                            patch('app.services.ai_pack_storage.os.readlink', return_value=target):
                        self.assertEqual(storage._safe('blobs'), storage.root / 'blobs')

    def test_alias_is_never_allowed_as_managed_root_or_descendant(self):
        alias = Path('/var')
        def metadata(path):
            return self.metadata(stat.S_IFLNK | 0o777) if path == alias else self.metadata()
        with patch('app.services.ai_pack_storage.sys.platform', 'darwin'), \
                patch.object(Path, 'lstat', autospec=True, side_effect=metadata), \
                patch('app.services.ai_pack_storage.os.readlink', return_value='private/var'):
            for root in (alias, Path('/')):
                storage = AIPackStorage(root)
                storage.root = root
                with self.subTest(root=root), self.assertRaises(StorageSafetyError):
                    storage._safe('.' if root == alias else 'var')

    def test_wrong_target_owner_or_writable_target_is_rejected(self):
        alias = Path('/var')
        cases = [
            ('private/elsewhere', 0, stat.S_IFDIR | 0o755, 0),
            ('private/var', 501, stat.S_IFDIR | 0o755, 0),
            ('private/var', 0, stat.S_IFDIR | 0o777, 0),
            ('private/var', 0, stat.S_IFDIR | 0o755, 501),
            ('private/var', 0, stat.S_IFLNK | 0o777, 0),
        ]
        for target, alias_uid, destination_mode, destination_uid in cases:
            with self.subTest(case=(target, alias_uid, destination_mode, destination_uid)):
                def metadata(path):
                    if path == alias:
                        return self.metadata(stat.S_IFLNK | 0o777, alias_uid)
                    if path == Path('/private'):
                        return self.metadata(destination_mode, destination_uid)
                    return self.metadata()
                with patch('app.services.ai_pack_storage.sys.platform', 'darwin'), \
                        patch.object(Path, 'lstat', autospec=True, side_effect=metadata), \
                        patch('app.services.ai_pack_storage.os.readlink', return_value=target):
                    with self.assertRaises(StorageSafetyError):
                        AIPackStorage._links(alias, allow_system_alias=True)

    def test_arbitrary_links_and_non_darwin_aliases_remain_forbidden(self):
        for platform, alias in [('linux', Path('/var')), ('darwin', Path('/custom')),
                                ('darwin', Path('/tmp'))]:
            with self.subTest(platform=platform, alias=alias), \
                    patch('app.services.ai_pack_storage.sys.platform', platform), \
                    patch.object(Path, 'lstat', return_value=self.metadata(stat.S_IFLNK | 0o777)), \
                    patch('app.services.ai_pack_storage.os.readlink', return_value='private/var'):
                with self.assertRaises(StorageSafetyError):
                    AIPackStorage(alias / 'models')._safe(alias / 'models')


if __name__ == '__main__':
    unittest.main()

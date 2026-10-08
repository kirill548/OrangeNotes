"""Cross-platform packaging contracts without invoking PyInstaller."""
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class ReleasePackagingTests(unittest.TestCase):
    def setUp(self):
        filename = Path(__file__).resolve().parents[1] / 'tools/build.py'
        spec = importlib.util.spec_from_file_location('release_build', filename)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.module.ROOT = self.root
        (self.root / 'app/assets').mkdir(parents=True)
        (self.root / 'app/assets/app.png').write_bytes(b'test-icon')
        (self.root / 'DISTRIBUTION.md').write_text('distribution', encoding='utf-8')

    def test_macos_resources_are_inside_app_not_windows_folder(self):
        folder = self.module.bundle_directory('darwin')
        with patch.object(self.module.subprocess, 'run') as run:
            archive = self.module.package_bundle(folder, 'darwin')
        self.assertEqual(folder.name, 'OrangeNotes.app')
        self.assertTrue((folder / 'Contents/Resources/README.txt').is_file())
        self.assertEqual(run.call_args.args[0][:3], ['ditto', '-c', '-k'])
        self.assertTrue(archive.endswith('.zip'))

    def test_linux_archive_retains_permissions_and_includes_desktop_metadata(self):
        folder = self.module.bundle_directory('linux')
        with patch.object(self.module.shutil, 'make_archive', return_value='release.tar.gz') as archive:
            self.module.package_bundle(folder, 'linux')
        self.assertEqual(archive.call_args.args[1], 'gztar')
        self.assertIn('Terminal=false', (folder / 'OrangeNotes.desktop').read_text())
        self.assertTrue((folder / 'OrangeNotes.png').is_file())

    def test_32_bit_runtime_rejected_before_any_build(self):
        with patch.object(self.module.sys, 'maxsize', 2**31 - 1), patch.object(self.module.subprocess, 'run') as run:
            with self.assertRaisesRegex(SystemExit, '64-bit'):
                self.module.main()
        run.assert_not_called()

    def test_no_personal_database_copied_to_windows_archive(self):
        (self.root / 'private.sqlite3').write_bytes(b'secret')
        folder = self.module.bundle_directory('win32')
        with patch.object(self.module.shutil, 'make_archive'):
            self.module.package_bundle(folder, 'win32')
        self.assertFalse((folder / 'private.sqlite3').exists())
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ['README.txt'])


if __name__ == '__main__':
    unittest.main()

import hashlib
import tempfile
import unittest
from pathlib import Path
from tools.prepare_release_assets import ARTIFACTS, prepare, checksums


class PublicationTests(unittest.TestCase):
    def fixture(self, root):
        downloads = root / 'downloads'
        for platform in ARTIFACTS:
            folder = downloads / ('OrangeNotes-' + platform)
            folder.mkdir(parents=True)
            (folder / ('OrangeNotes-' + platform + '.zip')).write_bytes(platform.encode())
        linux = downloads / 'OrangeNotes-Linux-x64'
        for suffix in ('.deb', '.AppImage', '.flatpak'):
            (linux / ('OrangeNotes-Linux-x64' + suffix)).write_bytes(b'package')
        return downloads

    def test_four_platforms_and_source_checksums(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            downloads = self.fixture(root)
            out = root / 'assets'
            out.mkdir()
            (out / 'OrangeNotes-source-v0.1.0.zip').write_bytes(b'source')
            prepare(downloads, out)
            checksums(out)
            text = (out / 'SHA256SUMS.txt').read_text()
            self.assertEqual(len(text.splitlines()), 8)
            self.assertIn(hashlib.sha256(b'source').hexdigest(), text)
            checksums(out)
            self.assertEqual((out / 'SHA256SUMS.txt').read_text(), text)

    def test_missing_platform_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            downloads = self.fixture(root)
            import shutil
            shutil.rmtree(downloads / 'OrangeNotes-macOS-Intel')
            with self.assertRaisesRegex(ValueError, 'macOS-Intel'):
                prepare(downloads, root / 'assets')

    def test_conflicting_duplicate_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            downloads = self.fixture(root)
            (downloads / 'OrangeNotes-Linux-x64' / 'OrangeNotes-Windows-x64.zip').write_bytes(b'wrong')
            with self.assertRaisesRegex(ValueError, 'Conflicting'):
                prepare(downloads, root / 'assets')

    def test_missing_flatpak_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            downloads = self.fixture(root)
            (downloads / 'OrangeNotes-Linux-x64' / 'OrangeNotes-Linux-x64.flatpak').unlink()
            with self.assertRaisesRegex(ValueError, '.flatpak'):
                prepare(downloads, root / 'assets')

import hashlib
import json
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


class CandidateProvenanceTests(unittest.TestCase):
    fixture = PublicationTests.fixture
    def manifests(self, downloads, commit):
        for platform in ARTIFACTS:
            folder = downloads / ('OrangeNotes-' + platform)
            files = sorted(p for p in folder.iterdir() if p.name.startswith('OrangeNotes-'))
            records = [{'file': p.name, 'size': p.stat().st_size,
                        'sha256': hashlib.sha256(p.read_bytes()).hexdigest()} for p in files]
            (folder / 'CANDIDATE_MANIFEST.json').write_text(json.dumps(
                {'version': '0.1.5', 'source_commit': commit, 'packages': records}), encoding='utf-8')

    def test_exact_candidate_manifests_recorded_in_global_checksums(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            downloads = self.fixture(root)
            commit = 'a' * 40
            self.manifests(downloads, commit)
            prepare(downloads, root / 'assets', commit)
            checksums(root / 'assets')
            data = json.loads((root / 'assets/RELEASE_PROVENANCE.json').read_text())
            self.assertEqual(data['source_commit'], commit)
            self.assertEqual(set(data['native_build_manifests']), set(ARTIFACTS))
            self.assertIn('RELEASE_PROVENANCE.json', (root / 'assets/SHA256SUMS.txt').read_text())

    def test_wrong_source_commit_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            downloads = self.fixture(root)
            self.manifests(downloads, 'a' * 40)
            with self.assertRaisesRegex(ValueError, 'source/version mismatch'):
                prepare(downloads, root / 'assets', 'b' * 40)

    def test_tampered_native_package_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            downloads = self.fixture(root)
            self.manifests(downloads, 'a' * 40)
            (downloads / 'OrangeNotes-Windows-x64/OrangeNotes-Windows-x64.zip').write_bytes(b'tampered')
            with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                prepare(downloads, root / 'assets', 'a' * 40)

    def test_missing_manifest_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            downloads = self.fixture(root)
            with self.assertRaises(FileNotFoundError):
                prepare(downloads, root / 'assets', 'a' * 40)


    def test_nested_actions_artifacts_preserve_manifest_verification(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            downloads = self.fixture(root)
            self.manifests(downloads, 'a' * 40)
            for platform in ARTIFACTS:
                folder = downloads / ('OrangeNotes-' + platform)
                entries = list(folder.iterdir())
                (folder / 'dist').mkdir()
                for entry in entries:
                    entry.rename(folder / 'dist' / entry.name)
            prepare(downloads, root / 'assets', 'a' * 40)
            self.assertTrue((root / 'assets/RELEASE_PROVENANCE.json').is_file())

    def test_ambiguous_candidate_manifests_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            downloads = self.fixture(root)
            self.manifests(downloads, 'a' * 40)
            folder = downloads / 'OrangeNotes-Windows-x64'
            (folder / 'duplicate').mkdir()
            (folder / 'duplicate/CANDIDATE_MANIFEST.json').write_bytes(
                (folder / 'CANDIDATE_MANIFEST.json').read_bytes())
            with self.assertRaisesRegex(ValueError, 'Ambiguous'):
                prepare(downloads, root / 'assets', 'a' * 40)

"""Verify sealed bundles and separation of mutable AI data from application code."""
import importlib.util
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


class PackagingResourceTests(unittest.TestCase):
    def setUp(self):
        filename = Path(__file__).resolve().parents[1] / 'tools/build.py'
        spec = importlib.util.spec_from_file_location('packaging_resource_build', filename)
        self.build = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.build)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.build.ROOT = self.root
        (self.root / 'licenses').mkdir()
        (self.root / 'licenses/NOTICE.txt').write_text('license notice')
        (self.root / 'DISTRIBUTION.md').write_text('distribution instructions')
        (self.root / 'app/assets').mkdir(parents=True)
        (self.root / 'app/assets/app.png').write_bytes(b'icon')

    def test_macos_main_completes_resources_before_signing(self):
        folder = self.build.bundle_directory('darwin')
        sealed = {}
        archived = []

        def contents():
            return {p.relative_to(folder).as_posix(): p.read_bytes()
                    for p in folder.rglob('*') if p.is_file()}

        def run(arguments, **kwargs):
            if arguments[0] == 'codesign' and '--sign' in arguments:
                sealed.update(contents())
                self.assertIn('Contents/Resources/README.txt', sealed)
                self.assertIn('Contents/Resources/licenses/NOTICE.txt', sealed)
            if arguments[0] in ('ditto', 'hdiutil'):
                self.assertTrue(sealed, 'Archive must follow code signing')
                self.assertEqual(contents(), sealed, 'Signed resources changed')
                archived.append(arguments[0])

        with patch.object(self.build.sys, 'platform', 'darwin'), \
                patch.object(self.build.sys, 'argv', ['build.py']), \
                patch.object(self.build.platform, 'system', return_value='Darwin'), \
                patch.object(self.build.platform, 'machine', return_value='arm64'), \
                patch.object(self.build, 'mac_icon', return_value=self.root / 'icon.icns'), \
                patch.object(self.build.subprocess, 'run', side_effect=run), \
                patch.dict(os.environ, {'MAC_SIGN_IDENTITY': 'Developer ID Test',
                                        'MAC_NOTARY_PROFILE': ''}), \
                patch('sys.stdout', new=io.StringIO()):
            self.build.main()
        self.assertEqual(archived, ['ditto', 'hdiutil'])

    def test_application_archives_exclude_mutable_models_and_databases(self):
        (self.root / 'ai_packs/models').mkdir(parents=True)
        (self.root / 'ai_packs/models/weights.gguf').write_bytes(b'private weights')
        (self.root / 'notes.sqlite3').write_bytes(b'private database')
        for target in ('linux', 'darwin'):
            with self.subTest(target=target):
                folder = self.build.bundle_directory(target)
                with patch.object(self.build.subprocess, 'run'), \
                        patch.object(self.build.platform, 'system', return_value=target), \
                        patch.object(self.build.platform, 'machine', return_value='test'), \
                        patch.object(self.build.shutil, 'make_archive'):
                    self.build.package_bundle(folder, target)
                names = {p.relative_to(folder).as_posix() for p in folder.rglob('*')}
                self.assertFalse(any('ai_packs' in name or 'weights.gguf' in name
                                     or 'notes.sqlite3' in name for name in names))
                prefix = 'Contents/Resources/' if target == 'darwin' else ''
                self.assertIn(prefix + 'licenses/NOTICE.txt', names)


if __name__ == '__main__':
    unittest.main()

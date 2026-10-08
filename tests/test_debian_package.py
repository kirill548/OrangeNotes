import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('package_deb', Path(__file__).resolve().parents[1] / 'tools/package_deb.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class DebianPackage(unittest.TestCase):
    def test_package_layout_and_unprivileged_build(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            frozen = root / 'frozen'
            frozen.mkdir()
            (frozen / 'OrangeNotes').write_bytes(b'executable')
            (frozen / 'OrangeNotes.png').write_bytes(b'icon')
            def inspect(arguments, **kwargs):
                self.assertEqual(arguments[:3], ['dpkg-deb', '--build', '--root-owner-group'])
                package_root = Path(arguments[3])
                self.assertIn('Architecture: amd64', (package_root / 'DEBIAN/control').read_text())
                self.assertIn('libnotify-bin', (package_root / 'DEBIAN/control').read_text())
                self.assertTrue((package_root / 'opt/orangenotes/OrangeNotes').exists())
                self.assertIn('Terminal=false', (package_root / 'usr/share/applications/orange-notes.desktop').read_text())
                self.assertFalse((package_root / 'DEBIAN/postinst').exists())
            with patch.object(module.subprocess, 'run', side_effect=inspect) as run:
                module.package(frozen, root / 'output.deb', '0.1.0', 'amd64')
                self.assertEqual(run.call_count, 1)

    def test_rejects_unknown_architecture_and_control_injection(self):
        with self.assertRaises(ValueError):
            module.package('.', 'a.deb', '0.1.0', 'unknown')
        with self.assertRaises(ValueError):
            module.package('.', 'a.deb', '1\nDepends: unwanted', 'amd64')

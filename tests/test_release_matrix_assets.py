import tempfile
import unittest
import zipfile
from pathlib import Path
from tools.verify_release_matrix_assets import verify

class MatrixAssetsTests(unittest.TestCase):
    def test_windows_archive_requires_executable(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with zipfile.ZipFile(root/'OrangeNotes-Windows-AMD64.zip','w') as archive:
                archive.writestr('OrangeNotes/OrangeNotes.exe',b'fixture')
            verify(root,'Windows')
            with zipfile.ZipFile(root/'OrangeNotes-Windows-AMD64.zip','w') as archive:
                archive.writestr('wrong.txt','fixture')
            with self.assertRaises(ValueError): verify(root,'Windows')
    def test_linux_requires_all_three_formats(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for suffix in ('deb','AppImage'): (root/('OrangeNotes.'+suffix)).write_bytes(b'fixture')
            with self.assertRaises(ValueError): verify(root,'Linux')
            (root/'OrangeNotes.flatpak').write_bytes(b'fixture')
            (root/'OrangeNotes.tar.gz').write_bytes(b'fixture')
            verify(root,'Linux')

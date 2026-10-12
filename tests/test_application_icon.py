"""Real icon decoding, frozen lookup, and fallback without starting user GUI."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication
from app.utils import app_icon


class ApplicationIconTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_packaged_png_and_ico_are_decodable_on_all_platform_branches(self):
        for target in ('win32', 'linux', 'darwin'):
            with self.subTest(target=target), patch.object(app_icon.sys, 'platform', target):
                result = app_icon.application_icon()
                self.assertFalse(result.isNull())
                self.assertFalse(result.pixmap(32, 32).isNull())
                self.assertFalse(result.pixmap(128, 128).isNull())

    def test_frozen_asset_lookup_and_corrupt_preferred_icon_fallback(self):
        source = app_icon.resource_path('app/assets/app.png')
        with tempfile.TemporaryDirectory() as directory:
            assets = Path(directory) / 'app/assets'
            assets.mkdir(parents=True)
            (assets / 'app.ico').write_bytes(b'corrupt-icon')
            (assets / 'app.png').write_bytes(source.read_bytes())
            with patch.object(app_icon.sys, '_MEIPASS', directory, create=True), patch.object(app_icon.sys, 'platform', 'win32'):
                self.assertFalse(app_icon.application_icon().pixmap(32, 32).isNull())

    def test_missing_assets_return_empty_icon_without_exception(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(app_icon.sys, '_MEIPASS', directory, create=True):
            self.assertTrue(app_icon.application_icon().isNull())

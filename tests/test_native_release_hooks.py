"""Signing and optional packaging fail visibly without credentials/tools."""
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('release_native',Path(__file__).resolve().parents[1]/'tools/release_native.py')
release=importlib.util.module_from_spec(spec);spec.loader.exec_module(release)

class NativeReleaseHooksTests(unittest.TestCase):
    def test_unsigned_windows_explicit_no_command(self):
        with patch.dict(os.environ,{},clear=True),patch.object(release.subprocess,'run') as run:
            self.assertFalse(release.sign_bundle(Path('/bundle'),'win32'))
        run.assert_not_called()

    def test_windows_verifies_after_signing(self):
        with patch.dict(os.environ,{'WINDOWS_SIGN_THUMBPRINT':'abc','SIGNTOOL_PATH':'signtool'}),patch.object(release.subprocess,'run') as run:
            self.assertTrue(release.sign_bundle(Path('/bundle'),'win32'))
        self.assertEqual(run.call_args_list[1].args[0][1:3],['verify','/pa'])

    def test_appimage_without_tool_retains_tar_fallback(self):
        with patch.dict(os.environ,{},clear=True),patch.object(release.subprocess,'run') as run:
            self.assertIsNone(release.linux_appimage(Path('/bundle'),Path('/release.AppImage')))
        run.assert_not_called()

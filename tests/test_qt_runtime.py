"""Frozen Qt startup must not inherit a foreign application's plugin setup."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.utils.qt_runtime import configure_platform


class QtRuntimeTests(unittest.TestCase):
    def setUp(self):
        scratch=Path(__file__).resolve().parents[3]/'work';scratch.mkdir(exist_ok=True)
        self.temporary=tempfile.TemporaryDirectory(prefix='qt-runtime-tests-',dir=scratch)
        self.bundle=Path(self.temporary.name)/'Bundle 中文 with spaces'/'_internal'
        self.plugins=self.bundle/'PySide6'/'plugins'
        self.platforms=self.plugins/'platforms';self.platforms.mkdir(parents=True)
        self.hostile={'QT_PLUGIN_PATH':r'C:\OtherApp\Qt5\plugins','QT_QPA_PLATFORM_PLUGIN_PATH':r'C:\Wrong\platforms',
                      'QT_QPA_PLATFORM':'wayland','QT_QPA_PLATFORMTHEME':'qt5ct','QT_QPA_GENERIC_PLUGINS':'foreign-plugin',
                      'PATH':r'C:\Windows\System32','UNCHANGED_KEY':'retain'}

    def tearDown(self):self.temporary.cleanup()

    def test_frozen_windows_uses_own_plugins_and_windows_backend(self):
        (self.platforms/'qwindows.dll').write_bytes(b'presence fixture; not loaded by unit test')
        with patch.object(sys,'frozen',True,create=True),patch.object(sys,'_MEIPASS',str(self.bundle),create=True),patch.object(sys,'platform','win32'),patch.dict(os.environ,self.hostile,clear=True):
            result=configure_platform()
            self.assertEqual(result,self.plugins)
            self.assertEqual(os.environ['QT_PLUGIN_PATH'],str(self.plugins))
            self.assertEqual(os.environ['QT_QPA_PLATFORM_PLUGIN_PATH'],str(self.platforms))
            self.assertEqual(os.environ['QT_QPA_PLATFORM'],'windows')
            self.assertNotIn('QT_QPA_PLATFORMTHEME',os.environ)
            self.assertNotIn('QT_QPA_GENERIC_PLUGINS',os.environ)
            self.assertEqual(os.environ['PATH'],self.hostile['PATH'])
            self.assertEqual(os.environ['UNCHANGED_KEY'],'retain')

    def test_missing_windows_plugin_fails_before_environment_changes(self):
        (self.platforms/'qminimal.dll').write_bytes(b'wrong plugin cannot substitute qwindows')
        with patch.object(sys,'frozen',True,create=True),patch.object(sys,'_MEIPASS',str(self.bundle),create=True),patch.object(sys,'platform','win32'),patch.dict(os.environ,self.hostile,clear=True):
            before=dict(os.environ)
            with self.assertRaisesRegex(RuntimeError,'плагин Windows') as error:configure_platform()
            self.assertIn('_internal',str(error.exception))
            self.assertEqual(dict(os.environ),before)

    def test_source_mode_keeps_inherited_environment_untouched(self):
        with patch.object(sys,'frozen',False,create=True),patch.dict(os.environ,self.hostile,clear=True):
            before=dict(os.environ)
            self.assertIsNone(configure_platform())
            self.assertEqual(dict(os.environ),before)

    def test_linux_and_mac_find_qt_subdirectory_and_remove_foreign_backend(self):
        unix_plugins = self.bundle/'PySide6/Qt/plugins'
        (unix_plugins/'platforms').mkdir(parents=True)
        for platform in ('linux', 'darwin'):
            with self.subTest(platform=platform),patch.object(sys,'frozen',True,create=True),patch.object(sys,'_MEIPASS',str(self.bundle),create=True),patch.object(sys,'platform',platform),patch.dict(os.environ,self.hostile,clear=True):
                self.assertEqual(configure_platform(), unix_plugins)
                self.assertEqual(os.environ['QT_QPA_PLATFORM_PLUGIN_PATH'],str(unix_plugins/'platforms'))
                self.assertNotIn('QT_QPA_PLATFORM',os.environ)

    def test_source_without_frozen_attribute_never_checks_bundle(self):
        with patch.dict(os.environ,self.hostile,clear=True):
            before=dict(os.environ)
            with patch.dict(sys.__dict__),patch.object(Path,'is_file',side_effect=AssertionError('Source execution must not inspect bundled plugins')):
                sys.__dict__.pop('frozen',None)
                self.assertFalse(hasattr(sys,'frozen'))
                self.assertIsNone(configure_platform())
            self.assertEqual(dict(os.environ),before)


if __name__=='__main__':unittest.main(verbosity=2)

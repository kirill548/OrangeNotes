"""Verify dependency collection is isolated; never invoke a real build."""
import importlib.util
import io
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch


class BuildEnvironmentTests(unittest.TestCase):
    def build_module(self):
        filename=Path(__file__).resolve().parents[1]/'tools/build.py'
        spec=importlib.util.spec_from_file_location('orange_notes_build_environment_test',filename)
        module=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def invoke(self,clean=False):
        module=self.build_module()
        dependency=Path('C:/isolated-qt/PySide6/__init__.py').resolve()
        fake_qt=SimpleNamespace(__file__=str(dependency))
        contaminated='C:/unrelated/poppler/bin'+os.pathsep+'C:/unrelated/conda/Library/bin'
        with patch.dict(sys.modules,{'PySide6':fake_qt}),patch.object(module.sys,'platform','win32'),patch.object(module.sys,'argv',['build.py']+(['--clean'] if clean else [])),patch.dict(os.environ,{'PATH':contaminated,'SystemRoot':'C:/Windows','PYTHONPATH':'C:/build-dependencies'}),patch.object(module.platform,'system',return_value='Windows'),patch.object(module.platform,'machine',return_value='AMD64'),patch.object(module.subprocess,'run') as run,patch.object(module.shutil,'copy2'),patch.object(module.shutil,'copytree'),patch.object(module.shutil,'make_archive',return_value='mocked-distribution.zip'),patch('sys.stdout',new=io.StringIO()):
            module.main()
        return module,dependency,run.call_args

    def test_windows_collection_excludes_unrelated_dll_directories(self):
        module,dependency,call=self.invoke()
        environment=call.kwargs['env']
        self.assertEqual(environment['PATH'].split(os.pathsep),[str(dependency.parent),str(Path(sys.executable).parent),str(Path('C:/Windows')/'System32'),str(Path('C:/Windows'))])
        self.assertNotIn('poppler',environment['PATH'].casefold())
        self.assertNotIn('conda/Library',environment['PATH'])
        self.assertEqual(environment['PYTHONPATH'],'C:/build-dependencies')
        self.assertTrue(call.kwargs['check'])

    def test_clean_build_and_required_native_resources_are_forwarded(self):
        module,dependency,call=self.invoke(clean=True)
        arguments=call.args[0]
        self.assertIn('--clean',arguments)
        self.assertIn(str(module.ROOT/'app/services/windows_toast.ps1')+':app/services',arguments)
        self.assertIn(str(module.ROOT/'app/assets')+':app/assets',arguments)
        self.assertIn('--windowed',arguments)
        self.assertIn('--onedir',arguments)


if __name__=='__main__':
    unittest.main()

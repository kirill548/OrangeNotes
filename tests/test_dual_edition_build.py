import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch
class DualEditionBuildTests(unittest.TestCase):
    def setUp(self):
        path=Path(__file__).resolve().parents[1]/'tools/build.py'
        spec=importlib.util.spec_from_file_location('dual_build',path)
        self.build=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.build)
    def test_complete_requires_pack_before_build(self):
        with patch.object(self.build.subprocess,'run') as execute:
            with self.assertRaises(SystemExit):self.build.main(['--edition','complete'])
            execute.assert_not_called()
    def test_lite_refuses_pack(self):
        with patch.object(self.build.subprocess,'run') as execute:
            with self.assertRaises(SystemExit):self.build.main(['--edition','lite','--ai-pack','private-models'])
            execute.assert_not_called()
    def test_complete_corrupt_pack_refused_before_build(self):
        import tempfile
        with tempfile.TemporaryDirectory() as root,patch.object(self.build.subprocess,'run') as execute:
            with self.assertRaises((FileNotFoundError,ValueError)):self.build.main(['--edition','complete','--ai-pack',root])
            execute.assert_not_called()

    def test_pack_in_generated_build_refused_before_validation(self):
        with patch.object(self.build.subprocess,'run') as execute:
            with self.assertRaises(SystemExit):self.build.main(['--edition','complete','--ai-pack',str(self.build.ROOT/'build/seed')])
            execute.assert_not_called()
    def test_pack_in_output_bundle_refused_before_validation(self):
        with patch.object(self.build.subprocess,'run') as execute:
            with self.assertRaises(SystemExit):self.build.main(['--edition','complete','--ai-pack',str(self.build.ROOT/'dist/complete/OrangeNotes/seed')])
            execute.assert_not_called()
    def test_parent_pack_of_generated_directory_refused(self):
        with patch.object(self.build.subprocess,'run') as execute:
            with self.assertRaises(SystemExit):self.build.main(['--edition','complete','--ai-pack',str(self.build.ROOT)])
            execute.assert_not_called()

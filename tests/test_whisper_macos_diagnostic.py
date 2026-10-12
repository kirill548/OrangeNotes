import importlib.util
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location('whisper_macos_diagnostic', Path(__file__).resolve().parents[1] / 'tools/diagnose_whisper_macos.py')
diagnostic = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diagnostic)


class MacWhisperDiagnosticTests(unittest.TestCase):
    def test_foreign_host_never_claims_native_build(self):
        with patch.object(diagnostic.sys, 'platform', 'win32'), patch.object(diagnostic.platform, 'machine', return_value='AMD64'), patch.object(diagnostic.subprocess, 'run') as run:
            result = diagnostic.diagnose()
        self.assertFalse(result['native_macos'])
        self.assertFalse(result['ready_to_build'])
        self.assertFalse(result['compiled'])
        run.assert_not_called()

    def test_missing_prerequisites_reported_without_install(self):
        with patch.object(diagnostic.sys, 'platform', 'darwin'), patch.object(diagnostic.platform, 'machine', return_value='arm64'), patch.object(diagnostic.shutil, 'which', return_value=None):
            result = diagnostic.diagnose()
        self.assertFalse(result['ready_to_build'])
        self.assertFalse(result['checks']['compiler']['available'])

    def test_ready_does_not_mean_compiled(self):
        completed = Mock(returncode=0, stdout='native prerequisite', stderr='')
        with patch.object(diagnostic.sys, 'platform', 'darwin'), patch.object(diagnostic.platform, 'machine', return_value='x86_64'), patch.object(diagnostic.shutil, 'which', return_value='/tool'), patch.object(diagnostic.subprocess, 'run', return_value=completed):
            result = diagnostic.diagnose()
        self.assertTrue(result['ready_to_build'])
        self.assertFalse(result['compiled'])

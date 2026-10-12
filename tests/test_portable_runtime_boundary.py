"""Legacy discovery candidates never authorize launching outside app-owned AI."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from app.services import local_runtime


class PortableRuntimeBoundaryTests(unittest.TestCase):
    def test_legacy_runtime_and_work_candidates_never_launch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidates = []
            for directory in ('runtime', 'work', 'stt'):
                executable = root / directory / 'ollama.exe'
                executable.parent.mkdir()
                executable.write_bytes(b'never execute')
                candidates.append((executable, root / directory / 'models'))
            client = Mock(base_url='http://127.0.0.1:11434', managed_runtime=None, runtime_root=None)
            client.probe.return_value = {'available': False}
            with patch('app.services.app_paths.get_app_dir', return_value=root), \
                 patch.object(local_runtime, 'runtime_candidates', return_value=candidates), \
                 patch.object(local_runtime.subprocess, 'Popen') as launch:
                self.assertFalse(local_runtime.ensure_runtime(client))
                launch.assert_not_called()


if __name__ == '__main__':
    unittest.main()

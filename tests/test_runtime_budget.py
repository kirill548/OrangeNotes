import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from app.services.local_ai import OllamaClient,LocalAIError
from app.services.local_runtime import runtime_candidates,resource_advisory

class RuntimeBudgetTests(unittest.TestCase):
    def test_shared_budget_stops_retry_before_network(self):
        client=OllamaClient();client.deadline=time.monotonic()-1
        with patch.object(client._opener,'open') as open_request:
            with self.assertRaises(LocalAIError):client._request('/api/chat',{})
        open_request.assert_not_called()

    def test_remaining_budget_limits_http_timeout(self):
        client=OllamaClient();client.deadline=time.monotonic()+.2
        with patch.object(client._opener,'open',side_effect=TimeoutError) as request:
            with self.assertRaises(LocalAIError):client._request('/api/chat',{})
        self.assertLessEqual(request.call_args.kwargs['timeout'],.2+1e-9)

    def test_architecture_specific_runtime_names(self):
        root=Path('/tmp/app')
        self.assertEqual(runtime_candidates(root,'win32','AMD64')[0][0].name,'ollama.exe')
        self.assertEqual(runtime_candidates(root,'darwin','arm64')[0][0].name,'ollama-darwin-arm64')
        self.assertEqual(runtime_candidates(root,'linux','x86_64')[0][0].name,'ollama-linux-amd64')

    def test_mac_runtime_in_resources(self):
        root=Path('/tmp/OrangeNotes.app/Contents/MacOS')
        self.assertIn('Resources',str(runtime_candidates(root,'darwin','arm64')[0][0]))

    def test_low_ram_is_advisory_not_rejection(self):
        with patch('app.services.local_runtime.os.name','posix'),patch('app.services.local_runtime.os.sysconf',return_value=1,create=True),patch('app.services.local_runtime.subprocess.run',side_effect=FileNotFoundError):
            result=resource_advisory()
        self.assertTrue(result['warning']);self.assertIsNone(result['vram_bytes'])


class ResourceGPUChecks(unittest.TestCase):
    def test_optional_gpu_uses_free_memory_max_single_device(self):
        from types import SimpleNamespace
        with patch('app.services.local_runtime.subprocess.run',return_value=SimpleNamespace(stdout='4096\n8192\n')) as run:
            report=resource_advisory()
        self.assertEqual(report['vram_bytes'],8192*1024**2)
        self.assertEqual(run.call_args.kwargs['timeout'],2)

    def test_unknown_gpu_is_not_zero_memory(self):
        with patch('app.services.local_runtime.subprocess.run',side_effect=FileNotFoundError):
            self.assertIsNone(resource_advisory()['vram_bytes'])


class UnsupportedRuntimeChecks(unittest.TestCase):
    def test_unknown_architecture_does_not_select_amd64(self):
        for arch in ('i686', 'x86', 'riscv64', 'unknown'):
            self.assertEqual(runtime_candidates(Path('/tmp/app'), 'linux', arch), [])

    def test_unknown_operating_system_has_no_runtime(self):
        self.assertEqual(runtime_candidates(Path('/tmp/app'), 'freebsd', 'amd64'), [])

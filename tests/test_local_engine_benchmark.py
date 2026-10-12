import unittest
from unittest.mock import patch
from tools.benchmark_local_engines import run, summarize, _arm_remaining_timeout


class LocalEngineBenchmarkTests(unittest.TestCase):
    def test_median_is_measured_without_outlier_fabrication(self):
        self.assertEqual(summarize([1, 2, 3, 100])['p50_ms'], 2.5)

    def test_no_measurements_are_none(self):
        self.assertIsNone(summarize([])['p50_ms'])

    def test_remote_endpoint_rejected(self):
        with self.assertRaises(ValueError):
            run('http://192.0.2.1:11434', ['qwen3:4b'])

    def test_missing_server_honest_skip(self):
        with patch('tools.benchmark_local_engines.build_opener') as factory:
            factory.return_value.open.side_effect = OSError('offline')
            report = run('http://127.0.0.1:11434', ['qwen3:4b'])
        self.assertIn('skipped', report)
        self.assertIsNone(report['vram_peak_bytes'])
        self.assertFalse(report['downloads'])

    def test_invalid_cold_mode_rejected(self):
        with self.assertRaises(ValueError):
            run('http://127.0.0.1:11434', [], mode='reboot')

    def test_overall_deadline_not_renewed_per_line(self):
        from types import SimpleNamespace
        from unittest.mock import Mock
        sock = Mock()
        response = SimpleNamespace(fp=SimpleNamespace(raw=SimpleNamespace(_sock=sock)))
        with patch('tools.benchmark_local_engines.time.monotonic', return_value=29):
            _arm_remaining_timeout(response, 30)
        sock.settimeout.assert_called_once_with(1)
        with patch('tools.benchmark_local_engines.time.monotonic', return_value=31):
            with self.assertRaises(TimeoutError):
                _arm_remaining_timeout(response, 30)

    def test_warm_setup_failure_is_reported_without_fake_attempts(self):
        import io
        import json
        snapshots = []
        tags = io.BytesIO(json.dumps({'models': [{'name': 'local:latest'}]}).encode())
        with patch('tools.benchmark_local_engines.build_opener') as factory:
            factory.return_value.open.side_effect = [tags, TimeoutError('preload')]
            report = run('http://127.0.0.1:11434', ['local:latest'], 20,
                         mode='warm', checkpoint=snapshots.append)
        entry = report['models'][0]
        self.assertEqual(entry['setup_error'], 'TimeoutError')
        self.assertEqual(entry['attempted_cases'], 0)
        self.assertEqual(entry['transport_completed_cases'], 0)
        self.assertEqual(entry['cases'], [])
        self.assertEqual(len(snapshots), 1)

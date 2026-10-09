import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.services import ai_hardware as hardware


class HardwareMetricsTests(unittest.TestCase):
    def test_missing_providers_are_unknown_not_cpu_or_zero(self):
        with patch.object(hardware.platform, 'system', return_value='Windows'), \
             patch.object(hardware, '_nvml', side_effect=ImportError), \
             patch.object(hardware, '_nvidia_smi', side_effect=FileNotFoundError):
            result = hardware.collect_hardware()
        self.assertEqual(result['mode'], 'unknown')
        for key in ('gpu_utilization_pct', 'vram_used_bytes', 'vram_total_bytes'):
            self.assertIsNone(result[key])

    def test_nvidia_smi_measures_and_bounds_call(self):
        with patch.object(hardware.subprocess, 'run', return_value=SimpleNamespace(stdout='25, 512, 8192\n')) as run:
            result = hardware._nvidia_smi()
        self.assertEqual(result['gpu_utilization_pct'], 25)
        self.assertEqual(result['vram_used_bytes'], 512 * 1048576)
        self.assertEqual(run.call_args.kwargs['timeout'], 1)

    def test_real_zero_utilization_is_allowed(self):
        with patch.object(hardware.subprocess, 'run', return_value=SimpleNamespace(stdout='0, 0, 8192\n')):
            result = hardware._nvidia_smi()
        self.assertEqual(result['gpu_utilization_pct'], 0)
        self.assertEqual(result['vram_used_bytes'], 0)

    def test_unsupported_utilization_remains_none(self):
        with patch.object(hardware.subprocess, 'run', return_value=SimpleNamespace(stdout='N/A, 512, 8192\n')):
            result = hardware._nvidia_smi()
        self.assertIsNone(result['gpu_utilization_pct'])

    def test_invalid_memory_does_not_create_gpu_snapshot(self):
        with patch.object(hardware.subprocess, 'run', return_value=SimpleNamespace(stdout='500, 9000, 8192\n')):
            self.assertIsNone(hardware._nvidia_smi())

    def test_timeout_falls_back(self):
        with patch.object(hardware.platform, 'system', return_value='Windows'), \
             patch.object(hardware, '_nvml', side_effect=ImportError), \
             patch.object(hardware, '_nvidia_smi', side_effect=subprocess.TimeoutExpired('nvidia-smi', 1)):
            self.assertEqual(hardware.collect_hardware()['mode'], 'unknown')

    def test_macos_does_not_fake_metal_vram(self):
        with patch.object(hardware.platform, 'system', return_value='Darwin'), \
             patch.object(hardware, '_nvidia_smi') as probe:
            result = hardware.collect_hardware()
        probe.assert_not_called()
        self.assertIsNone(result['vram_total_bytes'])
        self.assertEqual(result['mode'], 'unknown')

    def test_amd_sysfs_real_measurements(self):
        with tempfile.TemporaryDirectory() as tmp:
            device = Path(tmp) / 'card0' / 'device'
            device.mkdir(parents=True)
            for name, value in {'vendor': '0x1002', 'gpu_busy_percent': '42',
                                'mem_info_vram_used': '1024', 'mem_info_vram_total': '4096'}.items():
                (device / name).write_text(value)
            result = hardware._amd_sysfs(Path(tmp))
        self.assertEqual(result['gpu_utilization_pct'], 42)
        self.assertEqual(result['vram_total_bytes'], 4096)

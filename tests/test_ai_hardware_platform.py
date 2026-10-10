"""Platform probes tested without drivers, optional packages, or real devices."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.services import ai_hardware as hardware


class HardwarePlatformTests(unittest.TestCase):
    def nvml(self, memories):
        return SimpleNamespace(
            nvmlInit=Mock(), nvmlShutdown=Mock(),
            nvmlDeviceGetCount=Mock(return_value=len(memories)),
            nvmlDeviceGetHandleByIndex=Mock(side_effect=lambda index: index),
            nvmlDeviceGetMemoryInfo=Mock(side_effect=lambda index: memories[index]),
            nvmlDeviceGetUtilizationRates=Mock(return_value=SimpleNamespace(gpu=0)),
        )

    def test_nvml_invalid_device_cannot_mask_valid_device_memory(self):
        nvml = self.nvml([SimpleNamespace(used=-100, total=1000),
                          SimpleNamespace(used=200, total=1000)])
        with patch.object(hardware.importlib, 'import_module', return_value=nvml):
            result = hardware._nvml()
        self.assertEqual(result['vram_used_bytes'], 200)
        self.assertEqual(result['vram_total_bytes'], 1000)
        self.assertIn('1 NVIDIA', result['detail'])
        nvml.nvmlShutdown.assert_called_once()

    def test_nvml_cleanup_failure_preserves_measurement(self):
        nvml = self.nvml([SimpleNamespace(used=100, total=1000)])
        nvml.nvmlShutdown.side_effect = RuntimeError('driver disappeared')
        nvml.nvmlDeviceGetUtilizationRates.side_effect = RuntimeError('unsupported')
        with patch.object(hardware.importlib, 'import_module', return_value=nvml):
            result = hardware._nvml()
        self.assertEqual(result['vram_used_bytes'], 100)
        self.assertIsNone(result['gpu_utilization_pct'])

    def test_nvml_failed_initialization_does_not_shutdown(self):
        nvml = self.nvml([])
        nvml.nvmlInit.side_effect = RuntimeError('sandbox denied')
        with patch.object(hardware.importlib, 'import_module', return_value=nvml):
            with self.assertRaises(RuntimeError):
                hardware._nvml()
        nvml.nvmlShutdown.assert_not_called()

    def test_linux_denied_nvidia_probes_reach_sysfs(self):
        measured = hardware._snapshot('AMD sysfs')
        with patch.object(hardware.platform, 'system', return_value='Linux'), \
             patch.object(hardware, '_nvml', side_effect=PermissionError), \
             patch.object(hardware, '_nvidia_smi', side_effect=PermissionError), \
             patch.object(hardware, '_amd_sysfs', return_value=measured):
            self.assertIs(hardware.collect_hardware(), measured)

    def test_all_linux_probes_denied_remain_unavailable(self):
        with patch.object(hardware.platform, 'system', return_value='Linux'), \
             patch.object(hardware, '_nvml', side_effect=ImportError), \
             patch.object(hardware, '_nvidia_smi', side_effect=PermissionError), \
             patch.object(hardware, '_amd_sysfs', side_effect=PermissionError):
            result = hardware.collect_hardware()
        self.assertEqual(result['mode'], 'unknown')
        self.assertIsNone(result['gpu_utilization_pct'])
        self.assertIsNone(result['vram_used_bytes'])

    def test_macos_skips_every_probe(self):
        with patch.object(hardware.platform, 'system', return_value='Darwin'), \
             patch.object(hardware, '_nvml') as nvml, \
             patch.object(hardware, '_nvidia_smi') as smi, \
             patch.object(hardware, '_amd_sysfs') as amd:
            result = hardware.collect_hardware()
        for probe in (nvml, smi, amd):
            probe.assert_not_called()
        self.assertIsNone(result['vram_total_bytes'])
        self.assertIn('unified memory', result['detail'])

    def test_sysfs_missing_load_preserves_vram_and_skips_other_vendors(self):
        with tempfile.TemporaryDirectory() as tmp:
            for index, vendor in enumerate(('0x1002', '0x8086')):
                device = Path(tmp) / f'card{index}' / 'device'
                device.mkdir(parents=True)
                for name, value in {'vendor': vendor, 'mem_info_vram_used': '32',
                                    'mem_info_vram_total': '128'}.items():
                    (device / name).write_text(value)
            result = hardware._amd_sysfs(Path(tmp))
        self.assertEqual(result['vram_used_bytes'], 32)
        self.assertEqual(result['vram_total_bytes'], 128)
        self.assertIsNone(result['gpu_utilization_pct'])

    def test_sysfs_unresolvable_card_does_not_hide_remaining_cards(self):
        with tempfile.TemporaryDirectory() as tmp:
            device = Path(tmp) / 'card1' / 'device'
            device.mkdir(parents=True)
            for name, value in {'vendor': '0x1002', 'mem_info_vram_used': '32',
                                'mem_info_vram_total': '128'}.items():
                (device / name).write_text(value)
            inaccessible = Mock()
            inaccessible.resolve.side_effect = PermissionError('sandbox denied')
            root = Mock()
            root.glob.return_value = [inaccessible, device, device]
            result = hardware._amd_sysfs(root)
        self.assertEqual(result['vram_used_bytes'], 32)
        self.assertEqual(result['vram_total_bytes'], 128)

    def test_smi_partial_rows_aggregate_only_valid_memory(self):
        output = 'N/A, 2, 8\n40, 3, 16\n90, 100, 8\nmalformed\n'
        with patch.object(hardware.subprocess, 'run', return_value=SimpleNamespace(stdout=output)) as run:
            result = hardware._nvidia_smi()
        self.assertEqual(result['vram_used_bytes'], 5 * 1048576)
        self.assertEqual(result['vram_total_bytes'], 24 * 1048576)
        self.assertEqual(result['gpu_utilization_pct'], 40)
        self.assertTrue(run.call_args.kwargs['check'])
        self.assertEqual(run.call_args.kwargs['timeout'], 1)


if __name__ == '__main__':
    unittest.main()

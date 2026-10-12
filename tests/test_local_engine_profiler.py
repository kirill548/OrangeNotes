import os
import platform
import unittest

from tools.profile_local_engine import windows_tree_memory


@unittest.skipUnless(platform.system() == 'Windows', 'Windows working-set provider')
class LocalEngineProfilerTests(unittest.TestCase):
    def test_owned_current_process_has_real_nonzero_memory(self):
        readings = windows_tree_memory(os.getpid())
        current = next(item for item in readings if item['pid'] == os.getpid())
        self.assertGreater(current['rss_bytes'], 0)
        self.assertGreater(current['private_bytes'], 0)
        self.assertGreaterEqual(current['process_lifetime_peak_rss_bytes'], current['rss_bytes'])

    def test_absent_pid_does_not_measure_unrelated_processes(self):
        self.assertEqual([], windows_tree_memory(0x7FFFFFFF))

"""Real Qt signal paths with synthetic engines and hardware probes only."""
import threading
import time
import unittest
from unittest.mock import patch

from PySide6.QtCore import QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app.services.ai_metrics import AIMetricsCollector
from app.services.ai_monitor import AIHardwareMonitor
from app.ui.companion import CompanionDialog
import test_companion_ui as helpers


class HardwareMonitorIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def wait(self, predicate, timeout=3):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return
            QTest.qWait(5)
        self.fail('Hardware worker did not settle')

    def test_probe_runs_off_gui_thread_without_overlap_or_disabled_poll(self):
        entered, release = threading.Event(), threading.Event()
        thread_ids, beats, delivery_ids = [], [], []
        gui_thread = threading.get_ident()
        collector = AIMetricsCollector()
        collector.changed.connect(lambda _: delivery_ids.append(threading.get_ident()))
        monitor = AIHardwareMonitor(collector)
        timer = QTimer()
        timer.setInterval(5)
        timer.timeout.connect(lambda: beats.append(True))

        def probe():
            thread_ids.append(threading.get_ident())
            entered.set()
            release.wait(2)
            return dict(mode='gpu', gpu_utilization_pct=42)

        try:
            with patch('app.services.ai_hardware.collect_hardware', side_effect=probe) as mocked:
                timer.start()
                monitor.poll()
                self.wait(entered.is_set)
                for _ in range(5):
                    monitor.poll()
                self.wait(lambda: len(beats) >= 3)
                self.assertNotEqual(thread_ids[0], gui_thread)
                self.assertEqual(mocked.call_count, 1)
                release.set()
                self.wait(lambda: monitor._worker is None)
                self.assertEqual(collector.snapshot()['gpu_utilization_pct'], 42)
                self.assertEqual(delivery_ids, [gui_thread])
                collector.set_enabled(False)
                monitor.poll()
                self.app.processEvents()
                self.assertEqual(mocked.call_count, 1)
                self.assertIsNone(monitor._worker)
        finally:
            release.set()
            timer.stop()
            monitor.stop()
            self.app.processEvents()


class CompanionMetricsIntegrationTests(unittest.TestCase):
    setUpClass = classmethod(helpers.CompanionUITests.setUpClass.__func__)
    setUp = helpers.CompanionUITests.setUp
    tearDown = helpers.CompanionUITests.tearDown
    wait = helpers.CompanionUITests.wait

    def make_dialog(self, slow=False):
        collector = AIMetricsCollector()
        observations = []
        collector.changed.connect(lambda sample: observations.append(dict(sample)))
        suite = self

        class Engine:
            supports_stream_segments = True

            def ask(self, query, workspace_id, *, on_segment, cancel_event, **kwargs):
                start = time.monotonic() - 0.1
                on_segment(dict(kind='metrics_start', at=start))
                on_segment(dict(kind='first_token', at=start + 0.025))
                on_segment(dict(kind='grounding'))
                suite.started.set()
                if slow:
                    suite.release.wait(2)
                return dict(text='MEASURED-ANSWER', sources=[], status='answered', engine_label='fake')

        dialog = CompanionDialog(self.store, self.parent, engine_factory=lambda _: Engine(),
                                 metrics_collector=collector)
        self.dialogs.append(dialog)
        dialog.show()
        self.app.processEvents()
        observations.clear()
        return dialog, collector, observations

    def test_answered_request_records_worker_timestamps_and_state_signals(self):
        dialog, collector, observations = self.make_dialog()
        dialog.input.setPlainText('test question')
        self.assertTrue(dialog.send())
        self.wait(lambda: not dialog.busy)
        snapshot = collector.snapshot()
        self.assertEqual(snapshot['sample_count'], 1)
        self.assertEqual(snapshot['state'], 'idle')
        self.assertEqual(snapshot['active_count'], 0)
        self.assertGreaterEqual(snapshot['latency_p50_ms'], 100-1e-6)
        self.assertAlmostEqual(snapshot['ttft_p50_ms'], 25, places=3)
        self.assertEqual([sample['state'] for sample in observations],
                         ['active', 'active', 'grounding', 'idle'])
        self.assertIn('MEASURED-ANSWER', dialog.chat.toPlainText())

    def test_cancelled_request_discards_late_answer_and_metrics_sample(self):
        dialog, collector, observations = self.make_dialog(slow=True)
        dialog.input.setPlainText('test question')
        self.assertTrue(dialog.send())
        self.wait(lambda: collector.snapshot()['state'] == 'grounding')
        dialog.cancel()
        self.assertEqual(collector.snapshot()['state'], 'idle')
        self.release.set()
        self.wait(lambda: not dialog.busy)
        snapshot = collector.snapshot()
        self.assertEqual(snapshot['sample_count'], 0)
        self.assertEqual(snapshot['active_count'], 0)
        self.assertIsNone(snapshot['latency_p50_ms'])
        self.assertIsNone(snapshot['ttft_p50_ms'])
        self.assertNotIn('MEASURED-ANSWER', dialog.chat.toPlainText())


if __name__ == '__main__':
    unittest.main()

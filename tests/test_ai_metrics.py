import math
import unittest

from app.services.ai_metrics import AIMetricsCollector


class MetricsStatisticsTests(unittest.TestCase):
    def setUp(self):
        self.now = 0.0
        self.metrics = AIMetricsCollector(clock=lambda: self.now)

    def record(self, key, duration, ttft=None, success=True):
        start = self.now
        self.metrics.begin(key, "test-model")
        if ttft is not None:
            self.now = start + ttft
            self.metrics.first_token(key)
        self.now = start + duration
        self.metrics.complete(key, success)

    def test_empty_and_even_medians(self):
        self.assertIsNone(self.metrics.snapshot()["latency_p50_ms"])
        self.assertIsNone(self.metrics.snapshot()["ttft_p50_ms"])
        self.record("a", 2, 0.5)
        self.record("b", 4, 1.5)
        self.assertEqual(self.metrics.snapshot()["latency_p50_ms"], 3000)
        self.assertEqual(self.metrics.snapshot()["ttft_p50_ms"], 1000)

    def test_failure_abort_and_missing_token(self):
        self.record("failure", 2, 1, success=False)
        self.metrics.begin("abort", "test")
        self.metrics.abort("abort")
        self.record("success", 3)
        snapshot = self.metrics.snapshot()
        self.assertEqual(snapshot["sample_count"], 1)
        self.assertEqual(snapshot["latency_p50_ms"], 3000)
        self.assertIsNone(snapshot["ttft_p50_ms"])

    def test_window_is_last_twenty_successes_including_slow_requests(self):
        for index in range(21):
            self.record(str(index), index + 1)
        self.assertEqual(self.metrics.snapshot()["sample_count"], 20)
        self.assertEqual(self.metrics.snapshot()["latency_p50_ms"], 11500)
        slow = AIMetricsCollector(clock=lambda: self.now)
        slow.begin("slow", "test")
        self.now += 86400
        slow.complete("slow")
        self.assertEqual(slow.snapshot()["latency_p50_ms"], 86400000)

    def test_invalid_clock_samples_are_rejected(self):
        for value in (math.nan, math.inf, -1):
            self.now = value
            self.metrics.begin("invalid", "test")
            self.assertEqual(self.metrics.snapshot()["active_count"], 0)
        self.now = 10
        self.metrics.begin("backwards", "test")
        self.now = 9
        self.metrics.complete("backwards")
        self.assertEqual(self.metrics.snapshot()["sample_count"], 0)

    def test_duplicate_token_and_late_events_do_not_change_history(self):
        self.metrics.begin("a", "test")
        self.now = 1
        self.metrics.first_token("a")
        self.now = 2
        self.metrics.first_token("a")
        self.now = 3
        self.metrics.complete("a")
        self.metrics.complete("a")
        self.metrics.first_token("a")
        self.assertEqual(self.metrics.snapshot()["ttft_p50_ms"], 1000)
        self.assertEqual(self.metrics.snapshot()["sample_count"], 1)

    def test_worker_timestamps_ignore_delivery_delay(self):
        self.now = 1000
        self.metrics.begin("worker", "test", at=10)
        self.metrics.first_token("worker", at=10.5)
        self.metrics.complete("worker", at=12)
        self.assertEqual(self.metrics.snapshot()["latency_p50_ms"], 2000)
        self.assertEqual(self.metrics.snapshot()["ttft_p50_ms"], 500)

    def test_state_and_disable_discard_inflight_request(self):
        self.assertEqual(self.metrics.snapshot()["state"], "idle")
        self.metrics.begin("a", "test")
        self.assertEqual(self.metrics.snapshot()["state"], "active")
        self.metrics.grounding("a")
        self.assertEqual(self.metrics.snapshot()["state"], "grounding")
        self.metrics.first_token("a")
        self.assertEqual(self.metrics.snapshot()["state"], "active")
        self.metrics.set_enabled(False)
        self.assertEqual(self.metrics.snapshot()["state"], "disabled")
        self.now = 1
        self.metrics.complete("a")
        self.metrics.set_enabled(True)
        self.assertEqual(self.metrics.snapshot()["state"], "idle")
        self.assertEqual(self.metrics.snapshot()["sample_count"], 0)


if __name__ == "__main__":
    unittest.main()

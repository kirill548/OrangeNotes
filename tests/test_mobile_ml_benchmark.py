import unittest
from tools.mobile_ml_benchmark import summarize, validate


def sample(**overrides):
    data = dict(model="Qwen/Qwen3-1.7B", backend="mlx-swift", device="synthetic-test-device", os_version="test", runtime_version="test", model_sha256="a" * 64, quantization="int4", accelerator="GPU", prompt_id="draft-1", memory_method="test-fixture", context_tokens=512, generated_tokens=101, device_ram_bytes=8_000_000_000, peak_memory_bytes=1_000_000_000, ttft_ms=100, decode_ms=1000, phase="warm")
    return dict(data, **{}) | overrides


class MobileBenchmarkTests(unittest.TestCase):
    def test_empty_report_is_blocked(self):
        self.assertEqual(summarize([])["status"], "blocked")

    def test_decode_excludes_first_token(self):
        self.assertEqual(validate(sample())["tokens_per_second"], 100)

    def test_percentiles(self):
        report = summarize([sample(ttft_ms=i) for i in range(1, 21)])
        self.assertEqual(report["groups"][0]["ttft_p50_ms"], 10.5)
        self.assertEqual(report["groups"][0]["ttft_p95_ms"], 19)
        self.assertIsNone(report["minimum_ram"])

    def test_distinct_backends_and_phases_are_not_pooled(self):
        report = summarize([sample(), sample(backend="coreml"), sample(phase="cold")])
        self.assertEqual(len(report["groups"]), 3)

    def test_bad_metrics_rejected(self):
        for overrides in ({"ttft_ms": float("nan")}, {"decode_ms": float("inf")}, {"context_tokens": True}, {"generated_tokens": 1}, {"peak_memory_bytes": -1}, {"model_sha256": "unknown"}, {"model": "Qwen/Qwen3-1.5B"}, {"backend": "unknown"}):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                validate(sample(**overrides))


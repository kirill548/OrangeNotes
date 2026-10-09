"""Validate and aggregate real mobile measurements; never fabricate timings."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import statistics

MODELS = ("Qwen/Qwen2.5-1.5B-Instruct", "Qwen/Qwen3-1.7B")
BACKENDS = ("executorch", "coreml", "mlx-swift")


def validate(record: dict) -> dict:
    if record.get("model") not in MODELS:
        raise ValueError("unsupported model (Qwen3 mobile candidate is 1.7B)")
    if record.get("backend") not in BACKENDS:
        raise ValueError("unsupported backend")
    for key in ("device", "os_version", "runtime_version", "model_sha256", "quantization", "accelerator", "prompt_id", "memory_method"):
        if not isinstance(record.get(key), str) or not record[key].strip():
            raise ValueError(f"missing {key}")
    digest = record["model_sha256"]
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ValueError("model_sha256 must be lowercase hex SHA256")
    for key in ("context_tokens", "generated_tokens", "device_ram_bytes", "peak_memory_bytes"):
        value = record.get(key)
        if type(value) is not int or value <= 0:
            raise ValueError(f"{key} must be a positive integer")
    for key in ("ttft_ms", "decode_ms"):
        value = record.get(key)
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"{key} must be positive and finite")
    if record["generated_tokens"] < 2:
        raise ValueError("decode throughput requires at least two generated tokens")
    if record.get("phase") not in ("cold", "warm"):
        raise ValueError("phase must be cold or warm")
    return dict(record, tokens_per_second=(record["generated_tokens"] - 1) * 1000 / record["decode_ms"])


def summarize(records: list[dict]) -> dict:
    if not records:
        return {"status": "blocked", "reason": "No real mobile runner/model measurements supplied", "samples": 0, "groups": [], "minimum_ram": None}
    groups = {}
    for raw in records:
        item = validate(raw)
        keys = ("model", "backend", "device", "os_version", "runtime_version", "model_sha256", "quantization", "accelerator", "context_tokens", "generated_tokens", "phase", "prompt_id", "memory_method", "device_ram_bytes")
        identity = tuple(item[k] for k in keys)
        groups.setdefault(identity, []).append(item)
    output = []
    for identity, items in groups.items():
        result = dict(zip(keys, identity))
        ordered = sorted(i["ttft_ms"] for i in items)
        result.update(samples=len(items), ttft_p50_ms=statistics.median(ordered), ttft_p95_ms=ordered[math.ceil(len(ordered) * .95) - 1], tokens_per_second_p50=statistics.median(i["tokens_per_second"] for i in items), peak_memory_bytes=max(i["peak_memory_bytes"] for i in items))
        output.append(result)
    return {"status": "measured", "samples": len(records), "groups": output, "minimum_ram": None, "note": "Observed peak memory alone does not establish minimum supported device RAM."}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", nargs="?", type=Path, help="JSON array of real on-device samples; omitted produces blocked template")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    data = json.loads(args.input.read_text(encoding="utf-8")) if args.input else []
    if not isinstance(data, list):
        parser.error("input must be a JSON array")
    args.output.write_text(json.dumps(summarize(data), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

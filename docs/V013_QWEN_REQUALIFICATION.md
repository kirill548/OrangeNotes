# v0.1.3: Qwen runtime requalification

> Исторический отчёт этапа разработки. Актуальный опубликованный релиз: [v0.1.3](../RELEASE_NOTES_v0.1.3.md); текущий план: [ROADMAP](../ROADMAP.md). Измерения ниже относятся к указанному в отчёте прогону.


Report date: 2026-10-11. Main benchmark and live quality measurements are from 2026-10-10; additional backend controls are from 2026-10-11. Original timestamps are retained.

## Decision

**Qwen2.5-1.5B-Instruct Q4_K_M is NOT_READY for Complete Edition.** The measured cold-start transport now completes, but the final production-prompt quality run passes only 3/21 cases: canonical refusals 0/10, independent refusal holdouts 0/5, full retrieval/stream/grounding pipeline 3/6. Do not silently select this candidate as the bundled default.

The Qwen3:4B diagnostic comparison passes canonical refusals 10/10 but only 3/6 pipeline cases. It is not a full replacement-model qualification.

## Reproducible evidence

See `work/v013-engine/requalification/benchmark_report.json` and `.md`, together with raw `cold.json`, `warm.json`, `final-gpu-quality.json`, `qwen3-control-quality.json` and `hardware_summary.json`. Original qualification artifacts outside `requalification` remain unchanged.

On Windows 11 / Ryzen 9 7845HX / RTX 4060 Laptop / Ollama 0.35.1, 60 cold and 60 warm requests complete without timeouts at a 30-second deadline. Cold TTFT p50/p95 is 8500/8781 ms and total latency 8820/9109 ms. Warm TTFT is 63/79 ms and latency 125/156 ms. Each run passes content checks in only 20/60 cases. Model unloading is verified before each cold request; OS file cache is retained.

The executed backend of the full benchmark is **Vulkan**, with 29/29 layers offloaded. The environment also contains disabled CUDA graphs, disabled flash attention and one parallel request. These combined settings cannot establish CUDA-graph causality for a Vulkan run. The controlled diagnostic pair requested CUDA with Vulkan disabled, but both runs actually used CPU fallback (zero GPU layers, no CUDA library loaded). Sequential timings of 5063 and 1500 ms are cache-order confounded and do not establish a CUDA graph effect. The separate Vulkan control again offloaded 29/29 layers to RTX 4060.

## Template and grounding

`tools/qwen_template.py` creates an explicit ChatML Modelfile: role markers, assistant prefix, end markers and stop parameters. Its CPU option is diagnostic. However, `/api/show` exposes the GGUF-native Jinja template and runtime logs report `peg-native`; execution of the declared Go template is not proven. There is no evidence that replacing the recipe alone fixes refusal behavior.

The original `_SYSTEM` prompt and production JSON schema remain in use. Live quality uses context 8192, temperature 0.2, maximum 1024 generated tokens, NDJSON through Qt Network, and the original 30-second deadline. Compact-prompt and narrow-schema experiments were rejected and reverted: improved isolated refusals accompanied false refusals of positive facts or still failed other negative cases. Quote, revision and streaming validators were not weakened.

## Resource interpretation

Cold sampled process-tree peak working set is 1,450,213,376 bytes and private commit 1,667,522,560 bytes; warm values are 1,358,553,088 and 1,584,984,064 bytes. Only request time windows of the final candidate benchmark are included. `/api/ps` records resident size and size_vram of 1,105,408,818 bytes at context 2048. Resident size is not absolute peak VRAM; working set is not commit. Shared pages can be counted more than once. A larger aggregate profiler peak includes Qwen3 and cannot be assigned to this candidate.

## Managed runtime integration

Runtime profiles are optional and allowlisted. They apply only to the application-owned managed Ollama child; the default preserves the inherited environment and existing external Ollama setup. Profile changes cannot silently reuse a live child created with a different launch profile: stop the owned runtime before restarting with another profile. Bundled manifest validation checks the profile before installation and preserves the selection in managed settings.

The allowlisted profiles are `default` and `vulkan-safe`. The latter sets only `OLLAMA_VULKAN=true`, `OLLAMA_FLASH_ATTENTION=false`, and `OLLAMA_NUM_PARALLEL=1`; it does not set a CUDA graph flag. To opt in, use `"runtime_profile":"vulkan-safe"` in the bundled manifest or managed AI settings. The default does not force this backend. Exact environment is specified by `app/services/runtime_profiles.py` and the final JSON report. Do not treat a diagnostic profile as an approved candidate-model configuration.

## Remaining qualification

No complete CPU, Linux/macOS, Q5_K_M or 3B performance matrix was run here. A single production-context cold probe completed at 15,719 ms but did not pass quality. Complete Edition requires passing both runtime and semantic gates; current candidate quality remains a release blocker. Final local pytest: **674 passed, 7 skipped, 158 subtests passed, zero failures/errors**, 51.63 seconds, one Qt deprecation warning. JUnit has 839 cases including subtests. Fresh QA dependencies include tzlocal; Segoe UI fonts are explicitly loaded because the sandbox Qt font database was empty. Existing UI assertions remain unchanged. Logs are `final-regression.log` and `final-regression-junit.xml` in the requalification directory. Backend control results are recorded separately and do not establish CUDA causality.

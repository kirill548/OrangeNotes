# Mobile AI: initial benchmark protocol

Status: **device execution blocked**. No Android/iOS device, converted model,
Android SDK runner or macOS/Xcode host was connected during this initial step.
Unit tests validate report arithmetic only; they are not device measurements.
No weights were downloaded and no paid service was used.

## Candidates and runtimes

- [Qwen2.5-1.5B-Instruct](https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct).
- [Qwen3-1.7B](https://huggingface.co/Qwen/Qwen3-1.7B): the official small model
  is 1.7B, not the requested name Qwen3-1.5B. Record thinking mode explicitly
  in prompt_id; use non-thinking mode for the initial responsive assistant.
- Android: [ExecuTorch LLM deployment](https://github.com/pytorch/executorch/blob/main/docs/source/llm/getting-started.md)
  exports a model to .pte before running through native bindings.
  [Android integration](https://docs.pytorch.org/executorch/stable/using-executorch-android.html)
  provides the AAR and Java bindings. Validate model export and backend operator
  support first; the presence of an Android runtime does not prove either Qwen
  variant can be exported with the selected version/quantization recipe.
- iOS: [MLX Swift examples](https://github.com/ml-explore/mlx-swift-examples)
  provide a Swift integration path. [MLX](https://mlx-framework.org/) targets
  CPU/GPU on Apple silicon; do not label it Apple Neural Engine acceleration.
- Core ML is a separate conversion and execution experiment.
  [Core ML compute units](https://apple.github.io/coremltools/docs-guides/source/model-prediction.html)
  allow hardware selection, but selection alone does not prove all LLM
  operators execute on ANE. Record actual accelerator evidence and conversion
  version; do not infer a mobile minimum RAM from parameter count.

## Reproducible device procedure

1. Pin runtime/exporter commit, original model revision, tokenizer and converted
   artifact SHA256. Begin with int4 and record group size and KV-cache dtype in
   quantization. Recheck model license before redistributing converted weights.
2. Run on physical devices in release mode, offline after provisioning. Record
   device/SoC, OS, RAM, power/thermal state and runtime version in a sidecar log.
3. Use synthetic notes, never personal data. Run three tasks: grounded summary,
   missing-fact refusal, note draft. Context sizes: 512, 2048, 4096 tokens;
   output limit: 128 tokens. Compare quality as well as speed with identical
   prompts. Cold load and warm generation remain separate groups.
4. Perform one warm-up, then at least 20 measured runs per combination. Define
   TTFT from request submission to the first generated token, excluding model
   loading for warm runs. Decode duration spans first to last generated token;
   throughput is (generated_tokens - 1) / decode duration.
5. Measure process peak memory and disclose instrumentation (Android profiler /
   platform process counters; Instruments physical footprint on iOS). Include
   model loading and KV cache. Measure temperature, background interruption,
   cancellation, 30-second deadline and repeated-session memory growth.
6. Determine minimum RAM only after lowest-RAM physical devices pass repeated
   runs without OOM or OS termination while the notes UI remains responsive.
   Peak process memory is not a RAM recommendation: OS/UI/cache overhead matters.

## Report contract and command

The dependency-free utility consumes a JSON array of measured records. Required
fields: model, backend (executorch/coreml/mlx-swift), device, os_version,
runtime_version, model_sha256, quantization, accelerator, prompt_id,
memory_method, context_tokens, generated_tokens, device_ram_bytes,
peak_memory_bytes, ttft_ms, decode_ms, phase (cold/warm).

`python tools/mobile_ml_benchmark.py work/mobile-measurements.json --output work/mobile-ai-results.json`

Without a device, generate the explicit blocked template on any Python host:

`python tools/mobile_ml_benchmark.py --output work/mobile-ai-results.json`

An empty array produces status=blocked with no invented metrics. Invalid and
non-finite metrics are rejected. Aggregation separates device, model, artifact,
backend, quantization, prompt, phase and context. p95 uses nearest rank; p50 is
the median. minimum_ram remains null until a separate device qualification.
This utility aggregates supplied evidence; it does not launch or validate the
authenticity of a mobile runner. Preserve raw on-device logs with each report.

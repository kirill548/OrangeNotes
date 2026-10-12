# Orange Notes: candidate qualification, 2026-10-11

> Исторический отчёт этапа разработки. Актуальный опубликованный релиз: [v0.1.3](../RELEASE_NOTES_v0.1.3.md); текущий план: [ROADMAP](../ROADMAP.md). Измерения ниже относятся к указанному в отчёте прогону.


**Decision: NO_QUALIFIED_CANDIDATE. No Complete Edition default is approved.**

| Candidate | Total | Canonical refusals | Holdouts | Pipeline | Transport errors | Full performance |
|---|---:|---:|---:|---:|---:|---|
| Qwen2.5-Coder-1.5B-Instruct Q4_K_M | 1/21 | 0/10 | 0/5 | 1/6 | 1 | NOT_RUN |
| Qwen2.5-3B-Instruct Q4_K_M | 3/21 | 0/10 | 0/5 | 3/6 | 1 | NOT_RUN |

Both final comparisons use Ollama 0.35.1 at the owned endpoint 127.0.0.1:11575 with the unchanged production prompt and JSON schema, context 8192, 1024 output tokens, temperature 0.2, NDJSON, and the original 30-second deadline. Each first cold quality request times out. Subsequent responses exist but do not pass strict refusal. The failed results are retained; the early server-unavailable 3B attempt is excluded from semantic comparison.

Requested flags are OLLAMA_VULKAN=true, OLLAMA_FLASH_ATTENTION=false, OLLAMA_NUM_PARALLEL=1, OLLAMA_NO_CLOUD=1 and GGML_CUDA_DISABLE_GRAPHS=1. Actual logs select **CUDA0 / RTX 4060 Laptop** for both final runners. An enable flag is not proof of Vulkan execution. Explicit ChatML recipes have no SYSTEM override; the executed format is native Jinja / peg-native, not proven Go-template execution.

No candidate passes the prerequisite 21/21 quality gate, so the full 60-cold / 60-warm performance benchmark and resource profiler were **not run**. TTFT, latency quantiles, sampled peak RSS and peak VRAM are null, not inferred from quality requests. The earlier Qwen3:4B 10/10 refusal control still has only 3/6 pipeline passes and is not a qualified default.

Qwen2.5-Coder-1.5B uses [Apache-2.0](https://huggingface.co/Qwen/Qwen2.5-Coder-1.5B-Instruct/blob/main/LICENSE). Qwen2.5-3B uses the [Qwen research license](https://huggingface.co/Qwen/Qwen2.5-3B-Instruct/blob/main/LICENSE), restricted to non-commercial research/evaluation. Free general-purpose distribution is not automatically authorized; bundling requires license review/permission independently of quality.

Evidence: qualification_report.json; candidate_setup.json (verified weights SHA256, bytes, model digests and sources); coder15b-quality.json; qwen25_3b-quality.json; quality-runtime-ready.json; quality-runtime-stderr.log. Retained initial attempts: coder15b-initial-quality.json and qwen25_3b-transport-failure.json. Manual audit is separate readiness_audit.json.

Recommendation: keep Complete Edition's model default unapproved, diagnose cold/backend selection separately, and qualify another permissively licensed candidate with the unchanged grounding and streaming gates. No Linux/macOS stability, minimum smartphone RAM, or production packaging readiness is claimed by this experiment.

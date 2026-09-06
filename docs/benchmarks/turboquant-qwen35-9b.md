# TurboQuant benchmark: Qwen3.5-9B-Q4_K_M

Consolidated results from the overnight KV cache quant sweep on a single
RTX 3060 12GB and a CPU-only Fedora LXC on the Orange Pi 5 Plus. Numbers
are from `llama-bench` on `TheTom/llama-cpp-turboquant` at
`tqp-v0.1.0 (eea498c42)`, built with CUDA 12.9.

Model: `qwen3.5-9b-q4_k_m.gguf` (9B parameters, Q4_K_M weights, ~5.2 GB)

## Flag combinations tested

| Label | `-ctk` | `-ctv` | Notes |
|---|---|---|---|
| f16/f16 | fp16 | fp16 | Baseline, no KV compression |
| Q8/T3 | q8_0 | turbo3 | Asymmetric, matches Ziskind's safe default |
| T3/T2 | turbo3 | turbo2 | Aggressive but still Qwen-safe per our needle runs |
| T2/T2 | turbo2 | turbo2 | Maximum compression, quality edge case |

## GPU results (RTX 3060 12GB, Debian 12 LXC)

KV cache memory footprint in MB, decode speed in tokens/second.

| Context | f16/f16 MB | Q8/T3 MB | T3/T2 MB | T2/T2 MB | Decode t/s |
|---:|---:|---:|---:|---:|---:|
| 4,096 | 178 | 96 | 74 | 62 | 52.4 |
| 32,768 | 1,074 | 422 | 243 | 211 | 56.3 |
| 65,536 | 2,098 | 794 | 437 | 373 | 56.3 |
| 131,072 | 4,146 | 1,538 | 824 | 696 | 56.3 |
| 262,144 | OOM | 2,998 | 1,598 | 1,342 | 61.4 |
| 524,288 | OOM | OOM | 3,146 | 2,634 | 61.2 |
| 786,432 | OOM | OOM | OOM | 3,926 | 61.6 |
| 1,048,576 | OOM | OOM | OOM | OOM | - |

**Ceiling: 786,432 tokens (768K)** on 12GB VRAM with T2/T2. 1M exhausted the
card even with the most aggressive quant. Decode speed is flat across the
entire context range, which is the expected behaviour for KV-bound decode
on a single GPU — the model forward pass dominates and the KV reads are
bandwidth-amortised.

## CPU results (Orange Pi 5 Plus, Fedora 43 LXC)

RK3588 CPU cores only, no NPU path for llama.cpp yet. Single-batch decode.

| Context | f16/f16 | Q8/T3 | T3/T2 | T2/T2 |
|---:|---:|---:|---:|---:|
| 4,096 | 4.1 t/s | 3.9 t/s | 3.7 t/s | 3.6 t/s |
| 32,768 | 3.2 t/s | 3.1 t/s | 2.9 t/s | 2.8 t/s |
| 65,536 | OOM | 2.4 t/s | 2.3 t/s | 2.2 t/s |

CPU-side KV quant pays for itself at 64K+ where f16 runs out of the
container's 8 GB RAM cap. Speed cost of quant is ~5-10% and mostly comes
from the dequant hot loop on ARM.

## Quality check (needle-in-haystack)

`scripts/kv_quant_validator.py` was run at 32K and 131K contexts on
Qwen2.5-1.5B (quality floor model) and Qwen3.5-9B. Results recorded in
`docs/kv-quant-validator.md`.

- **Q8/T3 on Qwen3.5-9B**: 100% recall at both depths
- **T3/T2 on Qwen3.5-9B**: 100% recall at 32K, 96% at 131K
- **T2/T2 on Qwen3.5-9B**: 92% recall at 32K, 84% at 131K
- **T3/T2 on Qwen2.5-1.5B without boundary layers**: total failure
  (gibberish output, matches NexusQuant report #21591)
- **T3/T2 on Qwen2.5-1.5B with `boundary=2`**: 88% recall at 32K

## Recommendation per-hardware

These are the defaults TAOS will ship once the deploy wizard reads
`/api/cluster/kv-quant-options`:

| Hardware | Default K | Default V | Boundary |
|---|---|---|---|
| 12GB+ GPU, Qwen3.5 family | q8_0 | turbo3 | 0 |
| 12GB+ GPU, Qwen2.5 family | q8_0 | turbo3 | 2 |
| 6-8GB GPU, any Qwen | q8_0 | fp16 | 0 |
| RK3588 CPU | fp16 | fp16 | 0 |
| Extreme context (>256K) on 12GB | turbo2 | turbo2 | 2 |

Users who deliberately want to push context past the recommended defaults
get a warning in the wizard pointing at the validator runbook.

## Reproducing

```bash
# Container: taos-debian-cuda (Debian 12 unprivileged, see docs/deploy/fedora-lxc-setup.md)
cd llama-cpp-turboquant
./scripts/spikes/ez-rknn-async/tq_ctx_bench.sh models/qwen3.5-9b-q4_k_m.gguf
```

Raw output is saved under `/tmp/tq_bench_<date>.log` in the container and
parsed into this table by `parse_bench.sh`. Source scripts live in
`scripts/spikes/ez-rknn-async/`.

## Known caveats

- Numbers are single-run. For release notes we want a triplicate pass with
  stddev, tracked under #226.
- T2/T2 is close enough to the quality floor that it should never be the
  scheduler's default — only an opt-in under the "push the context" flow.
- llama-bench decode t/s is a steady-state measurement. First-token
  latency (prefill) scales with context and is NOT captured here; that
  measurement lives under #198 once the guide is written.

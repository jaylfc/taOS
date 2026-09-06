# TurboQuant in TAOS

TurboQuant is a KV cache compression technique from Google
(arXiv:2504.19874, ICLR 2026). It quantises the key/value cache to 2-3
bits while preserving attention quality, and on our GPU workers it
extends the usable context window roughly 6x on the same VRAM.

TAOS integrates the `TheTom/llama-cpp-turboquant` fork
(`tqp-v0.1.0`) so every llama.cpp-compatible worker can opt in.

## Why use it

On a 12GB RTX 3060 running Qwen3.5-9B-Q4_K_M:

- **f16/f16** baseline tops out near 131K context before OOM
- **Q8/T3** (asymmetric) reaches 262K with no measurable quality loss
- **T2/T2** (aggressive) reaches 786K, with some recall drop past 131K

Decode speed stays flat at 52-62 t/s across the whole range — TurboQuant
is not a latency feature, it is a *capacity* feature.

Full numbers: [`docs/benchmarks/turboquant-qwen35-9b.md`](../benchmarks/turboquant-qwen35-9b.md).

## Asymmetric K/V is the important part

Alex Ziskind's empirical investigation and the NexusQuant report
(llama.cpp#21591) both confirm: **keys need more precision than values**.
The softmax in attention amplifies key quantisation noise, while values
are only linearly combined, so you get a much better quality/size
trade-off with:

```
-ctk q8_0 -ctv turbo3
```

Symmetric configs like `-ctk turbo3 -ctv turbo3` will produce gibberish
on Qwen2.5-family models without boundary-layer protection. TAOS
therefore exposes **separate K and V selectors** in the deploy wizard
and the cluster protocol, rather than a single shared value.

## The three deploy-wizard controls

When a worker advertises anything beyond `fp16/fp16`, the deploy wizard
shows three controls:

| Control | Field | Values |
|---|---|---|
| K cache quant | `kv_cache_quant_k` | `fp16`, `q8_0`, `turbo3`, `turbo2` |
| V cache quant | `kv_cache_quant_v` | `fp16`, `q8_0`, `turbo3`, `turbo2`, `turbo4` |
| Boundary layers | `kv_cache_quant_boundary_layers` | integer, 0 = off |

The dropdowns are populated from `/api/cluster/kv-quant-options`, which
unions what every online worker has advertised. If no worker supports a
given type, it does not appear — you cannot misconfigure an agent for a
capability the cluster does not actually have.

## Boundary layers: when to set it

Some model families (Qwen2.5, Llama 3.1 8B) break if the first and
last N attention layers are compressed. Setting `boundary_layers=2`
keeps those layers at fp16 while the middle layers use the selected
quant. Cost: a small fraction of the KV cache stays uncompressed.

Rule of thumb:

- Qwen3.5 family: `boundary=0` is safe
- Qwen2.5 family: `boundary=2` for T3/T2 or more aggressive
- Llama 3.1 family: `boundary=2` recommended
- Anything else: run the validator first

Run `scripts/kv_quant_validator.py` if you are unsure — it does a
needle-in-haystack pass and reports pass/fail. Details in
[`docs/kv-quant-validator.md`](../kv-quant-validator.md).

## Recommended defaults per hardware

| Hardware | K | V | Boundary |
|---|---|---|---|
| 12GB+ GPU, Qwen3.5 | `q8_0` | `turbo3` | 0 |
| 12GB+ GPU, Qwen2.5 | `q8_0` | `turbo3` | 2 |
| 6-8GB GPU | `q8_0` | `fp16` | 0 |
| RK3588 NPU | `fp16` | `fp16` | 0 |
| Max context (>256K) on 12GB | `turbo2` | `turbo2` | 2 |

RK3588 does not have TurboQuant yet — the rkllm SDK would need to
expose the equivalent flags. Tracked in #193.

## Which backends support it

- **llama-cpp (TheTom/llama-cpp-turboquant fork)** — full K/V selector
  exposed, this is what TAOS installs by default
- **vllm (0xSero/turboquant fork)** — stubbed; hybrid-mode no-op in
  vLLM 0.19.0 (see #224). When upstream merges we flip the flag.
- **ollama** — no TurboQuant support; falls back to `fp16` only
- **rkllama / rkllm** — no TurboQuant support; hardware path, not
  applicable to NPU backends today

The worker advertises what it actually supports via
`kv_cache_quant_k_support`, `kv_cache_quant_v_support`, and
`kv_cache_quant_boundary_layer_protect` on every heartbeat. The
controller unions these into the cluster-wide options list.

## Installing

`install-worker.sh` picks up the TurboQuant fork automatically when
building from source on any Linux host with CUDA or Metal. For Fedora
hosts, see the CUDA glibc workaround in
[`docs/deploy/fedora-lxc-setup.md`](../deploy/fedora-lxc-setup.md).

No manual config is needed — if the binary supports `-ctk turbo3`, the
worker probe will detect and advertise it on the next heartbeat, and
the deploy wizard will start showing the control.

## Troubleshooting

**"The dropdowns don't appear in the wizard."**
No online worker is advertising anything beyond fp16. Check
`/api/cluster/kv-quant-options` and confirm at least one worker's
latest heartbeat includes the split K/V support fields.

**"Deploy succeeded but the agent is producing gibberish."**
You've probably got a symmetric turbo3/turbo3 config on a model family
that needs boundary protection. Set `boundary_layers=2` and redeploy,
or drop to `q8_0/turbo3` which is quality-safe on nearly every model.

**"VRAM usage is the same as before."**
The worker is still on a stock llama.cpp build. Rebuild against
`TheTom/llama-cpp-turboquant` and restart the worker. The cluster page
will show the new support flags within one heartbeat interval.

**"Context-length limit is unchanged."**
Model's trained context is the ceiling. TurboQuant only helps when the
KV cache is the bottleneck; a 32K model stays a 32K model. Use rope
scaling for longer contexts on the same model.

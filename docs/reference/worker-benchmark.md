# Worker benchmark

Every TAOS worker runs a one-shot benchmark the first time it joins the
cluster. The result is cached and never re-run automatically — users
with custom models trigger additional runs manually from the Workers
page. This document explains what the benchmark measures, when it runs,
and how to interpret the output.

## When it runs

- **First join**: automatically, once, immediately after registration.
  The worker runs the benchmark as a background task so registration
  itself never blocks.
- **Manual re-run**: the Workers page exposes a "Run benchmark" button
  per worker. This is the only way to re-run on existing hardware.
- **Never auto-reruns**: benchmark results persist across worker
  restarts. Hardware does not change, so the numbers don't either.

If a user upgrades their hardware, they click the manual re-run button.
We do not try to detect hardware changes automatically.

## What it measures

For each detected backend, the benchmark records:

| Metric | Meaning |
|---|---|
| `prompt_tps` | Prompt processing (prefill) tokens/sec at 512-token input |
| `decode_tps` | Steady-state decode tokens/sec over a 256-token generation |
| `time_to_first_token_ms` | Latency from request to first streamed token |
| `max_context_tested` | Largest context the backend accepted without OOM |
| `kv_cache_quant_k` | K cache quant used during the run |
| `kv_cache_quant_v` | V cache quant used during the run |
| `kv_cache_quant_boundary_layers` | Boundary layer count used |

The benchmark uses **whatever model the worker's backend has loaded**
when it runs. There is no fixed benchmark model — the numbers describe
this worker on this model, not a cross-cluster standard.

This is deliberate: benchmarks that force a specific model artefact
either need to download it (wasting bandwidth and disk) or lie about
what the user will actually experience. The first-join flow measures
the real user path instead.

## Where the results are stored

Controller-side, under `data/worker_benchmarks.json` (runtime-generated;
the controller writes it when workers report benchmark results; `data/` is
runtime state and is not tracked in git):

```json
{
  "taos-debian-cuda": {
    "ran_at": "2026-04-11T22:14:03Z",
    "backend": "llama-cpp",
    "model": "qwen3.5-9b-q4_k_m",
    "prompt_tps": 912.3,
    "decode_tps": 56.3,
    "time_to_first_token_ms": 284,
    "max_context_tested": 131072,
    "kv_cache_quant_k": "q8_0",
    "kv_cache_quant_v": "turbo3",
    "kv_cache_quant_boundary_layers": 0
  }
}
```

The file is append-only per worker name. Re-running overwrites the
previous entry for that worker.

## How it shows in the UI

The Workers page displays:

- Decode t/s as the primary number
- Prompt t/s and TTFT as secondary
- Max context as a bar against the highest context in the cluster
- A KV quant chip showing `K=q8_0 V=turbo3 B=0` when non-default

A separate "Compare" button opens a side-by-side card against any other
worker in the cluster, useful for deciding where to route a given
workload.

## Report format for external consumption

Other tools can fetch `GET /api/cluster/workers/<name>/benchmark` to
receive the raw JSON above. The scheduler uses this to make placement
decisions for capability-aware dispatch. Third parties consuming this
should treat all fields as optional and default missing fields to null
rather than assume a fixed shape — we add metrics as the backend
catalog expands.

## Adding new metrics

When a new metric becomes worth measuring:

1. Extend the benchmark runner under `tinyagentos/benchmark/runner.py`
2. Add the field to `WorkerBenchmarkResult` in
   `tinyagentos/cluster/worker_protocol.py`
3. Update the serialiser + the Workers page column
4. Document the field in the table above

Never add a metric that requires downloading a specific model. Always
use whatever the worker has loaded.

## KV quant fields in the results schema

The benchmark `SuiteResult.details` dict carries the KV quant
configuration that was active during the run, under these keys:

| Key | Type | Example |
|---|---|---|
| `kv_cache_quant_k` | string | `"q8_0"` |
| `kv_cache_quant_v` | string | `"turbo3"` |
| `kv_cache_quant_boundary_layers` | integer | `0` |

Any caller reading benchmark results should default missing keys to
`fp16`, `fp16`, `0` respectively — legacy runs recorded before the
split (#189) do not have them. The runner writes the fields
automatically from the backend's advertised config at run time; there
is no code path that records a benchmark without them.

Tracked in #223.

## Manual re-run from the CLI

For headless worker hosts, trigger a re-run via:

```bash
curl -X POST http://<controller>:6969/api/cluster/workers/<name>/benchmark
```

The endpoint returns immediately with `{"status": "queued"}` and the
worker runs the benchmark in the background. Watch the Workers page
for the result to appear a few seconds later.

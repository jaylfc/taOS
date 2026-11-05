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
- **Manual re-run**: the Cluster app's worker panel has a "Re-run
  benchmarks" button, and `POST /api/workers/<id>/benchmark` does the
  same thing headlessly. This is the only way to re-run on existing
  hardware.
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

Controller-side, in `data/benchmarks.db` (`tinyagentos/benchmark/store.py`,
`data/` is runtime state and is not tracked in git). One row per
`(worker_id, capability, model, metric, measured_at)`:

```text
worker_id | capability | model | metric          | value | unit  | status | first_join | measured_at
pi4       | llm-chat   | qwen3 | tokens_per_sec  | 42.5  | tok/s | ok     | 1          | 1775000000.0
```

The table is **append-only**: a re-run inserts new rows and never updates or
deletes an earlier measurement, so the first-join baseline stays available for
comparison forever. `first_join` marks the one automatic run; every later run
carries `0`. Exactly one `first_join=1` row per worker can exist — a worker
that re-posts `first_join=true` has it coerced to a manual run.

`GET /api/workers/<id>/benchmark` returns `latest` (newest row per capability +
model), `history` (every recorded run, newest first) and `pending` (the queued
manual run, if one is waiting for the worker's next heartbeat).

## How it shows in the UI

The Cluster app's worker panel shows a **Benchmarks** card for the selected
worker:

- one line per capability + model: the metric, the newest value with its unit,
  and how long ago that run measured
- `first run` on the row that came from the automatic first-attach benchmark
- a "Re-run benchmarks" button, which queues a run and reports it as queued
  (the worker starts it on its next heartbeat — a few seconds later)
- a count of recorded measurements and when the last run landed

Rows whose `status` is not `ok` show the status (`skipped`, `timeout`, `error`)
in place of a number, so an unmeasurable backend is visible rather than blank.

## Report format for external consumption

Other tools can fetch `GET /api/workers/<id>/benchmark` to receive the raw
rows (`latest`, `history`, `pending`). The scheduler uses the same store for
placement decisions for capability-aware dispatch. Third parties consuming
this should treat all fields as optional and default missing fields to null
rather than assume a fixed shape — we add metrics as the backend catalog
expands.

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

For headless worker hosts, queue a re-run via:

```bash
# The endpoint is admin-only: present a signed-in session (browser/SPA) or the
# controller's host local token (TAOS_LOCAL_TOKEN).
curl -X POST http://<controller>:6969/api/workers/<worker-name>/benchmark \
  -H "Authorization: Bearer $TAOS_LOCAL_TOKEN"
```

The endpoint returns `202 {"status": "queued", ...}` immediately. The worker
learns about the run from its next heartbeat (about five seconds later), starts
the suite in the background, and posts the results back — echoing the queue
entry's id so the controller clears exactly that run. A request without
`{"force": true}` while a run is already queued answers `409`; pass
`-d '{"force": true}'` to replace the queued run.

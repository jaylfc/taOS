> **Status (2026-08-14): Not implemented.** The file paths referenced below are proposed, not present in the codebase.

# Unified GPU Work Queue (taOS #1864) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan slice-by-slice. Steps use checkbox (`- [ ]`) syntax for tracking.

**Spec:** `docs/design/2026-07-17-gpu-work-queue.md` (owner-approved, section 10a decisions are LOCKED).

**Goal:** Make the existing GpuArbiter the single front door and residency manager for all local GPU work: model loads and inference queue with visible position, idle-LRU models are actually unloaded to free VRAM, and every GPU op is traced through the existing audit layer.

**Architecture:** Three independently shippable phases behind `TAOS_GPU_QUEUE=off|shadow|on` (default `off`). Phase A routes model pulls through `GpuArbiter.submit_gpu` as `op="load"` tasks with event-driven admission, real pull cancellation, and gpu_op tracing. Phase B adds the ResidencyManager, the net-new unload primitive (ollama `keep_alive: 0`, rkllama `POST /unload_model`), the RK3588 memory probe, and idle-LRU eviction. Phase C inserts the fail-open streaming gateway on the inference path (LiteLLM api_base rewrite), per-model concurrency with a sub-millisecond resident fast path, the correlation header, the trace-to-BenchmarkStore rollup, and live queue-position UX.

**Tech Stack:** Python 3.11+/FastAPI/httpx/asyncio (controller), aiosqlite (trace + benchmark stores), LiteLLM proxy (`litellm[proxy]>=1.92.0`, `pyproject.toml:59`), React + zustand + vitest (desktop). Tests: pytest (`tests/`), vitest (`desktop/`).

## Global Constraints

Every slice's requirements implicitly include this section. The eight locked owner decisions (spec section 10a) are restated here verbatim in intent; do not relitigate or shortcut any of them.

1. **Gateway is the single GPU front door, permanently FAIL-OPEN.** Whenever the arbiter is unavailable, errors, or exceeds a tight admission budget, the gateway transparently passes the request straight through to the backend. Admission problems must never become 500s for inference. Shadow-mode soak is the rollout gate. Fail-open is a permanent resilience property, not a temporary crutch. (Decision 1)
2. **rkllama unload uses explicit `POST /unload_model`.** The arbiter owns LRU: backend TTLs are set generously long and unloads are driven explicitly by the arbiter, never left to the backend's internal timer. (Decision 2)
3. **Real end-to-end pull cancel.** A first-class `DownloadManager.cancel()` API stops the in-flight pull and reconciles state; the backend `DELETE /api/delete` reclaim is a backstop, not the mechanism. (Decision 3)
4. **Correlation header threaded through LiteLLM** so queue position is precise per-agent AND `gpu_op` traces join to `llm_call` traces. (Decision 4)
5. **The trace-to-BenchmarkStore aggregator ships in this feature.** Not deferred. (Decision 5)
6. **User-initiated pulls run at `Priority.INTERACTIVE_AGENT` (20)** (`tinyagentos/scheduler/types.py:28`). (Decision 6)
7. **Event-driven admission wakeup ships in Phase A.** A capacity release immediately wakes the drain; the 2 s poll tick (`gpu_arbiter.py:539`) is only the fallback. (Decision 7)
8. **RK3588 NPU/system-memory probe is in scope** so VRAM-pressure eviction actually fires on the Pi. (Decision 8)

Non-negotiables (structural, from the spec body):

- **Uncontended hot path is a near-zero-overhead passthrough:** target under 1 ms added in-process latency, zero subprocess spawns (no nvidia-smi) on the resident fast path, enforced by a perf test. The zero-VRAM reserve path already takes no lock (`tinyagentos/vram_reservation.py:109-115`); keep it that way.
- **Never-evict-active is structural, not a check:** eviction candidates are filtered on `active_ops == 0`, and every GPU op is an arbiter op, so an active model can never be selected. No separate in-flight signal exists or is added.
- **One VRAM ledger:** the shared `VramReservationManager` instance wired at `tinyagentos/app.py:1175-1176` remains the single authority. No slice may add a second ledger or shadow accounting that gates admission (the ResidencyManager tracks footprints for unload sizing only).
- **The audit hook is the existing `AgentTraceStore.record()`** (`tinyagentos/trace_store.py:264`) obtained from `TraceStoreRegistry.get(slug)` (`trace_store.py:528-559`), plus the existing `POST /api/trace` route (`tinyagentos/routes/trace.py:45`). No parallel telemetry system. Loads and unattributed ops use slug `_system_` (sentinel convention mirrors `_unknown_` at `litellm_callback.py:27`).
- **Feature flag:** every behavior change is gated on `TAOS_GPU_QUEUE` (default `off`); with the flag off, every code path is byte-for-byte today's behavior. Each phase ships independently and leaves the system working at every flag setting.
- **After Phase A in `on` mode, HTTP 503 on the pull routes means only "queue overflow"** (`max_queue_size=100`, `app.py:1187`), never "VRAM busy".
- **Loads serialize globally per backend** (one load at a time), preserving the Xid-62 rationale encoded in the one-admission-per-drain discipline (`gpu_arbiter.py:545-556`) and the cancel-await-then-release eviction ordering (`gpu_arbiter.py:497-534`).
- **Git identity:** author is `jaylfc <jaylfc25@gmail.com>`. No AI attribution of any kind in commits, PRs, or comments. No em dashes in any code comment, UI copy, doc text, commit message, or PR body produced for this feature. Never commit IPs, credentials, or environment-specific config (the Pi's address stays out of tests and docs).
- **Backward compatibility:** all new `submit_gpu` parameters are keyword-optional; existing arbiter tests (`tests/test_gpu_arbiter_894.py`, `tests/test_gpu_arbiter_toctou.py`) must keep passing unmodified in every slice.

## Riskiest Slices and Their Extra Verification

| Slice | Risk | Extra verification required |
|---|---|---|
| C1 + C2 (gateway on the inference hot path + LiteLLM traffic switch) | A controller bug now sits on every agent completion. | Byte-for-byte streaming test; fail-open tests for arbiter None / raise / budget overrun; TTFB overhead assertion in CI (< 25 ms guard) and in live shadow soak (< 5 ms p95 from shadow gpu_op traces over 48 h on Fedora RTX 3060 and the Pi) BEFORE `on` is ever set with C3 gating; rollback drill (flip flag, `LLMProxy.reload_config`, `llm_proxy.py:532`) exercised in a test. |
| B1 + B3 (unload primitive + idle-LRU eviction) | First code in the codebase that frees VRAM; a wrong candidate kills a live generation; Xid-62 regression risk. | rkllama `POST /unload_model` already verified live on the Pi (2026-07-17, spec section 3.3); re-verify the exact request body against the deployed fork during B5; unload only counted after `/api/ps` re-poll confirms; structural never-evict-active test under maximal pressure; on-hardware B5 gate: real 2-model eviction on RTX 3060, RK3588 eviction on the Pi, concurrent-load-during-eviction Xid-62 non-regression. |
| C4 (correlation header through LiteLLM) | Depends on two LiteLLM behaviors (`add_user_information_to_llm_headers` forwarding, `additional_headers` in hidden params) that vary by LiteLLM version. | Mandatory step 1 live verification against the pinned `litellm[proxy]>=1.92.0` with a header-echo mock backend BEFORE implementation; concrete in-repo fallback designs specified in the slice for each leg that fails verification; results recorded in the PR description. |

## Sequencing and Parallelism

```
A1 ──> A2 ──> A3 ──> A4          (same file, gpu_arbiter.py: strictly ordered)
        │                └─(A4 ∥ A5)
        ├──> A5 ──> A6           (A5 then A6: both touch arbiter hooks)
        ├──> A7 (needs A1,A2,A5) ──> A8 ──> A10
        └──> A9 (needs A2; cancel path needs A8)     A9 ∥ A10

B1 ∥ B2 ∥ B4                     (three independent modules, run in parallel)
(B1,B2) ──> B3 ──> B5 (gate; also needs B4)

C1 (needs Phase A) ──> C2 ∥ C3   (C2: litellm_config; C3: arbiter fast path)
(C1,C2) ──> C4
A5 ──> C5                        (C5 is independent of C1-C4; can start any time after Phase A)
(A6,C3) ──> C6
(C2,C3,C4) ──> C7 (gate)
```

- **Pure-unit slices (any fleet builder, no hardware):** A1-A10, B2, C1, C3, C5, C6, and the CI parts of B3, C2, C7.
- **Live-Pi check required:** B1 (confirm `/unload_model` request body against the deployed fork), B4 (real `/proc/meminfo` + device-tree values), B5 (hardware gate), C2/C7 (Pi shadow soak).
- **Live Fedora RTX 3060 check required:** B5 (real eviction + Xid-62 non-regression), C7 (soak).
- **Recommended first slice: A1** (flag + trace kind): smallest, zero behavior change, unblocks everything.

Branching: cut each slice branch from `origin/dev`, one PR per slice (CI + bot reviews per standing policy). Suggested branch names `feat/gpu-queue-a1-flag` ... `feat/gpu-queue-c7-soak`.

## File Map (who owns what)

| File | Slices | Responsibility |
|---|---|---|
| `tinyagentos/gpu_queue_flag.py` (new) | A1 | Read `TAOS_GPU_QUEUE`, expose one mode function |
| `tinyagentos/trace_store.py` | A1 | `gpu_op` in `VALID_KINDS` + envelope schema doc |
| `tinyagentos/scheduler/gpu_arbiter.py` | A2-A6, B3, C3 | Op shape, shadow dict, position, cancel, wakeup, aging, hooks, residency integration, fast path |
| `tinyagentos/scheduler/gpu_residency.py` (new) | B2 | ResidentModel + ResidencyManager |
| `tinyagentos/backend_unload.py` (new) | B1 | Per-backend unload adapters |
| `tinyagentos/system_stats.py`, `tinyagentos/vram_reservation.py` | B4 | RK3588 probe + probe chain |
| `tinyagentos/routes/models.py`, `tinyagentos/download_manager.py` | A7, A8 | Pull sites through arbiter; queued status; cancel API |
| `tinyagentos/routes/gpu_queue.py` (new) | A9 | GET/DELETE `/api/gpu/queue` |
| `tinyagentos/routes/gpu_gateway.py` (new) | C1, C4 | Fail-open streaming proxy `/gpu/{backend_name}/{path}` |
| `tinyagentos/litellm_config.py`, `tinyagentos/providers/__init__.py` | C2, C4 | api_base rewrite, gateway types, general_settings |
| `tinyagentos/litellm_callback.py` | C4 | gpu_op_id join on llm_call traces |
| `tinyagentos/benchmark/gpu_rollup.py` (new), `tinyagentos/routes/benchmarks.py` | C5 | Trace to BenchmarkStore aggregator |
| `tinyagentos/events/bus.py` | A6 | `notify=False` emit passthrough |
| `tinyagentos/app.py` | A5-A7, B3, C5 | Wiring only (hooks, rollup task) |
| `desktop/src/apps/ModelsApp.tsx` | A10, C6 | Queued card + position + cancel |
| `desktop/src/hooks/use-event-stream.ts`, `desktop/src/stores/gpu-queue-store.ts` (new) | C6 | SSE dispatch + shared queue state |
| `desktop/src/apps/MessagesApp.tsx`, `MessagesApp.stallWatch.ts` | C6 | Chat waiting indicator, stall suppression |

Run commands used throughout: `uv run pytest <path> -v` from the repo root (plain `pytest` if the venv is active), and `npm test -- <file>` inside `desktop/` (vitest, `desktop/package.json:10`).

---

# Phase A: loads through submit_gpu, shadow mode, event-driven wakeup, telemetry scaffolding

Phase A alone closes #1864 as originally filed. Exit criteria: with `TAOS_GPU_QUEUE=on`, a pull that would have 503'd queues with a visible position, can be cancelled end-to-end, admits immediately when capacity frees, and every load emits one `gpu_op` trace. With the flag off, zero behavior change.

### Slice A1: Feature flag + gpu_op trace kind

**Files:**
- Create: `tinyagentos/gpu_queue_flag.py`
- Modify: `tinyagentos/trace_store.py` (`VALID_KINDS` at :70-79, `ENVELOPE_V1_SCHEMA["kinds"]` at :91-111)
- Test: `tests/test_gpu_queue_flag.py` (new), `tests/test_trace_store.py` (extend)

**Interfaces:**
- Consumes: nothing.
- Produces (later slices import these exact names):

```python
# tinyagentos/gpu_queue_flag.py
GPU_QUEUE_MODES = ("off", "shadow", "on")

def gpu_queue_mode() -> str:
    """Current GPU queue rollout mode from TAOS_GPU_QUEUE.

    Returns "off" (default), "shadow", or "on". Unrecognized values
    log a warning once and coerce to "off" (fail to today's behavior).
    """
```
- Produces: `"gpu_op"` accepted by `AgentTraceStore.record(kind="gpu_op", ...)` and `POST /api/trace` (route validates against the same `VALID_KINDS`, `routes/trace.py:50`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_gpu_queue_flag.py
import pytest
from tinyagentos.gpu_queue_flag import gpu_queue_mode, GPU_QUEUE_MODES


def test_gpu_queue_mode_default_off(monkeypatch):
    monkeypatch.delenv("TAOS_GPU_QUEUE", raising=False)
    assert gpu_queue_mode() == "off"


@pytest.mark.parametrize("value", ["shadow", "on", "OFF", " Shadow "])
def test_gpu_queue_mode_env_values(monkeypatch, value):
    monkeypatch.setenv("TAOS_GPU_QUEUE", value)
    assert gpu_queue_mode() == value.strip().lower()


def test_gpu_queue_mode_invalid_coerces_off(monkeypatch):
    monkeypatch.setenv("TAOS_GPU_QUEUE", "bananas")
    assert gpu_queue_mode() == "off"


def test_modes_tuple_is_locked():
    assert GPU_QUEUE_MODES == ("off", "shadow", "on")
```

```python
# append to tests/test_trace_store.py
async def test_record_gpu_op_kind_accepted(tmp_path):
    store = AgentTraceStore(tmp_path, "_system_")
    env = await store.record(
        "gpu_op", model="qwen2.5:7b", backend_name="local-ollama",
        duration_ms=1200,
        payload={"op": "load", "outcome": "ok", "wait_ms": 0,
                 "queue_position_at_enqueue": 0, "queue_depth_at_admit": 0,
                 "required_vram_mb": 2048, "free_vram_mb_at_admit": 8192,
                 "reserved_vram_mb_at_admit": 0, "resident_models_at_admit": 0,
                 "evictions_triggered": [], "priority": 20,
                 "submitter": "_system_"},
    )
    assert env["kind"] == "gpu_op"
    rows = await store.list(kind="gpu_op")
    assert rows and rows[0]["payload"]["outcome"] == "ok"
    await store.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_gpu_queue_flag.py tests/test_trace_store.py -v -k "gpu"`
Expected: FAIL (`ModuleNotFoundError: tinyagentos.gpu_queue_flag`; `ValueError: unknown kind 'gpu_op'` raised by `_build_envelope`, `trace_store.py:152-153`).

- [ ] **Step 3: Implement**

```python
# tinyagentos/gpu_queue_flag.py
"""Rollout flag for the unified GPU work queue (taOS #1864).

off    - all queue behavior disabled; every path is pre-feature behavior.
shadow - instrument only: pull sites still 503 but record what would have
         queued; the gateway (Phase C) forwards without gating.
on     - full queue-and-wait behavior.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

GPU_QUEUE_MODES = ("off", "shadow", "on")
_warned = False


def gpu_queue_mode() -> str:
    global _warned
    raw = (os.environ.get("TAOS_GPU_QUEUE") or "off").strip().lower()
    if raw not in GPU_QUEUE_MODES:
        if not _warned:
            logger.warning("TAOS_GPU_QUEUE=%r not in %s; using 'off'", raw, GPU_QUEUE_MODES)
            _warned = True
        return "off"
    return raw
```

In `trace_store.py`, add `"gpu_op"` to `VALID_KINDS` (after `"governance"`) with a comment citing #1864, and add to `ENVELOPE_V1_SCHEMA["kinds"]`:

```python
        # GPU work queue per-op audit record (taOS #1864). One per op on
        # completion, plus one on eviction/cancel. model/backend_name/
        # duration_ms ride the first-class columns.
        "gpu_op": {
            "op": "inference|load|unload|evict",
            "outcome": "ok|error|cancelled|evicted|shadow_denied",
            "wait_ms": "int", "queue_position_at_enqueue": "int",
            "queue_depth_at_admit": "int", "required_vram_mb": "int",
            "free_vram_mb_at_admit": "int", "reserved_vram_mb_at_admit": "int",
            "resident_models_at_admit": "int",
            "evictions_triggered": "list[str]", "priority": "int",
            "submitter": "str",
        },
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_gpu_queue_flag.py tests/test_trace_store.py -v`
Expected: PASS, including all pre-existing trace-store tests.

- [ ] **Step 5: Commit**

```bash
git add tinyagentos/gpu_queue_flag.py tinyagentos/trace_store.py tests/test_gpu_queue_flag.py tests/test_trace_store.py
git commit -m "feat(gpu-queue): TAOS_GPU_QUEUE rollout flag and gpu_op trace kind (#1864)"
```

**Acceptance criteria:** flag defaults to `off`; invalid values coerce to `off` with a single warning; `gpu_op` records persist and round-trip through `store.list(kind="gpu_op")`; no other trace kind or route behavior changes.

---

### Slice A2: Arbiter op shape, shadow queue dict, position, non-destructive snapshot, cancel primitive

**Files:**
- Modify: `tinyagentos/scheduler/gpu_arbiter.py` (`_QueuedGpuTask` :48-57, `submit_gpu` :304-361, `_drain_queue` :545-610, `queue_snapshot` :634-650)
- Test: `tests/test_gpu_arbiter_queue_ops.py` (new)

**Interfaces:**
- Consumes: `Task`, `Priority` (`tinyagentos/scheduler/types.py:76-93,22-30`); existing `_arbiter_future` convention (`gpu_arbiter.py:350-358`).
- Produces (exact signatures later slices depend on):

```python
async def submit_gpu(
    self, task: Task, required_vram_mb: int = 0,
    evictable: bool = False, resource_id: str | None = None,
    required_gpu_arch: str | None = None,
    op: str = "inference",              # "load" | "inference"
    model: str | None = None,           # backend model name, e.g. "qwen2.5:7b"
    backend_name: str | None = None,    # config backend name, e.g. "local-ollama"
) -> object: ...

def queue_position(self, task_id: str) -> int | None:
    """1-based position of a queued op; None if not queued.
    Global ordering for op=='load'; per-model ordering for op=='inference'
    (only entries with the same model count, spec section 5.1)."""

async def cancel_op(self, task_id: str) -> bool:
    """Cancel a queued or running op. Queued: remove from queue + shadow
    dict and cancel the submitter's _arbiter_future. Running: delegate to
    _evict_task (cancel, await unwind, release reservation,
    gpu_arbiter.py:497-534). Returns False if unknown/finished."""

def queue_snapshot(self) -> list[dict]:
    # Non-destructive; each entry:
    # {task_id, capability, op, model, backend_name, submitter,
    #  priority, vram_mb, queued_seconds, position}
```

- `_QueuedGpuTask` gains `op: str = field(default="inference", compare=False)`, `model: str | None = field(default=None, compare=False)`, `backend_name: str | None = field(default=None, compare=False)`.
- New internal shadow dict `self._queued_entries: dict[str, _QueuedGpuTask]` plus `self._cancelled_ids: set[str]`. Maintained at every enqueue (`submit_gpu` :349 and the drain re-queue :603-605), every dequeue (`_drain_queue` :561), on drop (:606-610), and in `cancel_op`. Because `asyncio.PriorityQueue` has no remove, `cancel_op` on a queued entry pops it from `_queued_entries`, adds the id to `_cancelled_ids`, and cancels the future; `_drain_queue` discards any dequeued entry whose id is in `_cancelled_ids` (and removes it from the set) before admission. `queue_snapshot()` is reimplemented as a pure read of `_queued_entries` sorted by `(priority, seq)`; the drain-and-reinsert implementation (:634-650) is deleted.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_gpu_arbiter_queue_ops.py
import asyncio
import pytest
from tinyagentos.scheduler.gpu_arbiter import GpuArbiter
from tinyagentos.scheduler.types import Capability, Priority, Task
from tinyagentos.vram_reservation import VramReservationManager


def _mgr(free_mb: int, total_mb: int = 16384) -> VramReservationManager:
    return VramReservationManager(probe=lambda: (free_mb, total_mb))


def _task(priority=Priority.BACKGROUND, submitter="t"):
    async def payload(_res):
        await asyncio.sleep(0.05)
        return "ok"
    return Task(capability=Capability.LLM_CHAT, payload=payload,
                preferred_resources=[], priority=priority, submitter=submitter)


@pytest.mark.asyncio
async def test_submit_gpu_defaults_backward_compatible():
    arbiter = GpuArbiter(vram_reservation=_mgr(8192))
    result = await arbiter.submit_gpu(_task(), required_vram_mb=1024)
    assert result == "ok"          # old call shape, no new kwargs


@pytest.mark.asyncio
async def test_queue_position_global_for_loads():
    arbiter = GpuArbiter(vram_reservation=_mgr(0))   # everything queues
    t1, t2, t3 = _task(), _task(), _task()
    f1 = asyncio.ensure_future(arbiter.submit_gpu(
        t1, required_vram_mb=1024, op="load", model="a", backend_name="b1"))
    f2 = asyncio.ensure_future(arbiter.submit_gpu(
        t2, required_vram_mb=1024, op="load", model="b", backend_name="b1"))
    f3 = asyncio.ensure_future(arbiter.submit_gpu(
        t3, required_vram_mb=1024, op="load", model="c", backend_name="b1"))
    await asyncio.sleep(0.05)      # let them enqueue
    assert arbiter.queue_position(t1.id) == 1
    assert arbiter.queue_position(t2.id) == 2
    assert arbiter.queue_position(t3.id) == 3
    for f in (f1, f2, f3):
        f.cancel()


@pytest.mark.asyncio
async def test_queue_position_per_model_for_inference():
    arbiter = GpuArbiter(vram_reservation=_mgr(0))
    ta, tb, ta2 = _task(), _task(), _task()
    fs = [asyncio.ensure_future(arbiter.submit_gpu(
              t, required_vram_mb=1024, op="inference", model=m, backend_name="b1"))
          for t, m in ((ta, "m-a"), (tb, "m-b"), (ta2, "m-a"))]
    await asyncio.sleep(0.05)
    assert arbiter.queue_position(ta.id) == 1
    assert arbiter.queue_position(tb.id) == 1   # only m-b entries count
    assert arbiter.queue_position(ta2.id) == 2  # behind ta on m-a
    for f in fs:
        f.cancel()


@pytest.mark.asyncio
async def test_queue_snapshot_non_destructive_and_shaped():
    arbiter = GpuArbiter(vram_reservation=_mgr(0))
    t1 = _task(submitter="pull:x")
    f = asyncio.ensure_future(arbiter.submit_gpu(
        t1, required_vram_mb=1024, op="load", model="qwen", backend_name="b1"))
    await asyncio.sleep(0.05)
    snap1 = arbiter.queue_snapshot()
    snap2 = arbiter.queue_snapshot()
    entry = snap1[0]
    assert entry["op"] == "load" and entry["model"] == "qwen"
    assert entry["backend_name"] == "b1" and entry["submitter"] == "pull:x"
    assert entry["position"] == 1
    assert [e["task_id"] for e in snap1] == [e["task_id"] for e in snap2]
    stats = await arbiter.stats()
    assert stats["queue_depth"] == 1           # snapshot did not drain
    f.cancel()


@pytest.mark.asyncio
async def test_cancel_queued_op_removes_and_cancels_future():
    arbiter = GpuArbiter(vram_reservation=_mgr(0))
    t1 = _task()
    f = asyncio.ensure_future(arbiter.submit_gpu(
        t1, required_vram_mb=1024, op="load", model="m", backend_name="b1"))
    await asyncio.sleep(0.05)
    assert await arbiter.cancel_op(t1.id) is True
    with pytest.raises(asyncio.CancelledError):
        await f
    assert arbiter.queue_position(t1.id) is None
    assert await arbiter.cancel_op(t1.id) is False   # idempotent-ish: gone


@pytest.mark.asyncio
async def test_cancel_running_op_delegates_to_evict():
    mgr = _mgr(8192)
    arbiter = GpuArbiter(vram_reservation=mgr)
    started = asyncio.Event()

    async def payload(_res):
        started.set()
        await asyncio.sleep(30)

    t1 = Task(capability=Capability.LLM_CHAT, payload=payload,
              preferred_resources=[], priority=Priority.BACKGROUND, submitter="t")
    f = asyncio.ensure_future(arbiter.submit_gpu(t1, required_vram_mb=1024))
    await started.wait()
    assert await arbiter.cancel_op(t1.id) is True
    await asyncio.sleep(0.05)
    assert mgr.reserved_vram_mb == 0          # reservation released
    f.cancel()
```

Note: `queued_seconds` differs between snapshot calls, which is why the non-destructive assertion compares `task_id` lists rather than whole dicts.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_gpu_arbiter_queue_ops.py -v`
Expected: FAIL (`TypeError: submit_gpu() got an unexpected keyword argument 'op'`, `AttributeError: 'GpuArbiter' object has no attribute 'queue_position'`).

- [ ] **Step 3: Implement**

In `gpu_arbiter.py`:

1. Extend `_QueuedGpuTask` with the three `compare=False` fields above.
2. Extend `submit_gpu` signature; pass `op=op, model=model, backend_name=backend_name` into the `_QueuedGpuTask` constructor at :344-348; after `await self._queue.put(entry)` add `self._queued_entries[task.id] = entry`; in the `except asyncio.CancelledError` branch also `self._queued_entries.pop(task.id, None)`.
3. In `__init__` add `self._queued_entries: dict[str, _QueuedGpuTask] = {}` and `self._cancelled_ids: set[str] = set()`.
4. In `_drain_queue`: after `entry = self._queue.get_nowait()` insert

```python
            if entry.task.id in self._cancelled_ids:
                self._cancelled_ids.discard(entry.task.id)
                continue
            self._queued_entries.pop(entry.task.id, None)
```

   and in the retry re-queue loop (:603-605) re-add `self._queued_entries[entry.task.id] = entry` next to `put_nowait`; in the queue-full drop branch (:606-610) leave the entry out of the dict (it is already popped).
5. Add `queue_position`, `cancel_op`, and the new `queue_snapshot`:

```python
    def _ordered_queued(self) -> list[_QueuedGpuTask]:
        return sorted(self._queued_entries.values(), key=lambda e: (e.priority, e.seq))

    def queue_position(self, task_id: str) -> int | None:
        entry = self._queued_entries.get(task_id)
        if entry is None:
            return None
        ahead = 0
        for other in self._ordered_queued():
            if other.task.id == task_id:
                break
            if entry.op == "inference":
                if other.model == entry.model:
                    ahead += 1
            else:
                ahead += 1
        return ahead + 1

    async def cancel_op(self, task_id: str) -> bool:
        entry = self._queued_entries.pop(task_id, None)
        if entry is not None:
            self._cancelled_ids.add(task_id)
            future = getattr(entry.task, "_arbiter_future", None)
            if future is not None and not future.done():
                future.cancel()
            return True
        async with self._running_lock:
            running = task_id in self._running
        if running:
            return bool(await self._evict_task(task_id))
        return False

    def queue_snapshot(self) -> list[dict]:
        now = time.time()
        return [
            {"task_id": e.task.id, "capability": e.task.capability.value,
             "op": e.op, "model": e.model, "backend_name": e.backend_name,
             "submitter": e.task.submitter, "priority": e.priority,
             "vram_mb": e.required_vram_mb,
             "queued_seconds": now - e.queued_at,
             "position": self.queue_position(e.task.id)}
            for e in self._ordered_queued()
        ]
```

Keep the old `queue_snapshot` keys (`task_id, capability, priority, vram_mb, queued_seconds`) so any existing consumer keeps working; the new keys are additive.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_gpu_arbiter_queue_ops.py tests/test_gpu_arbiter_894.py tests/test_gpu_arbiter_toctou.py -v`
Expected: PASS, including both pre-existing arbiter suites unmodified.

- [ ] **Step 5: Commit**

```bash
git add tinyagentos/scheduler/gpu_arbiter.py tests/test_gpu_arbiter_queue_ops.py
git commit -m "feat(gpu-queue): op kinds, shadow queue dict, position, cancel_op on GpuArbiter (#1864)"
```

**Acceptance criteria:** old call shapes unchanged; position math matches spec section 5.1 (global for loads, per-model for inference); `queue_snapshot` is non-destructive and can no longer silently drop entries on re-insert (`gpu_arbiter.py:647-649` gap closed); `cancel_op` covers queued and running ops and always releases the reservation via the existing `_evict_task` ordering.

---

### Slice A3: Event-driven admission wakeup (locked decision 7)

**Files:**
- Modify: `tinyagentos/scheduler/gpu_arbiter.py` (`_process_queue` :536-543, `_release_reservation` :234-244, `_run_gpu_task` finally :434-448)
- Test: `tests/test_gpu_arbiter_wakeup.py` (new)

**Interfaces:**
- Consumes: A2's shadow dict (unchanged here).
- Produces: internal only. `GpuArbiter.__init__` gains `drain_tick_seconds: float = 2.0` (existing hardcoded `asyncio.sleep(2)` becomes the fallback timeout; tests shrink or grow it). New internal `self._wake: asyncio.Event` and

```python
    def _signal_capacity(self) -> None:
        """Wake the drain loop now: called on every reservation release and
        every op completion so a queued op admits immediately (decision 7)."""
        self._wake.set()
```

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_gpu_arbiter_wakeup.py
import asyncio
import time
import pytest
from tinyagentos.scheduler.gpu_arbiter import GpuArbiter
from tinyagentos.scheduler.types import Capability, Priority, Task
from tinyagentos.vram_reservation import VramReservationManager


@pytest.mark.asyncio
async def test_release_triggers_immediate_drain():
    # Fallback tick set absurdly long: only the event path can admit in time.
    mgr = VramReservationManager(probe=lambda: (1024, 16384))
    arbiter = GpuArbiter(vram_reservation=mgr, drain_tick_seconds=60.0)
    await arbiter.start()
    try:
        release_first = asyncio.Event()

        async def hold(_res):
            await release_first.wait()
            return "first"

        async def fast(_res):
            return "second"

        t1 = Task(capability=Capability.LLM_CHAT, payload=hold,
                  preferred_resources=[], priority=Priority.BACKGROUND, submitter="a")
        t2 = Task(capability=Capability.LLM_CHAT, payload=fast,
                  preferred_resources=[], priority=Priority.BACKGROUND, submitter="b")
        f1 = asyncio.ensure_future(arbiter.submit_gpu(t1, required_vram_mb=1024))
        await asyncio.sleep(0.05)
        f2 = asyncio.ensure_future(arbiter.submit_gpu(t2, required_vram_mb=1024))
        await asyncio.sleep(0.05)               # t2 is queued (VRAM exhausted)
        start = time.monotonic()
        release_first.set()                     # t1 finishes, releases 1024 MiB
        assert await asyncio.wait_for(f2, timeout=1.0) == "second"
        assert time.monotonic() - start < 0.5   # event path, not the 60 s tick
        assert await f1 == "first"
    finally:
        await arbiter.stop()


@pytest.mark.asyncio
async def test_poll_tick_still_drains_without_signal():
    # Capacity appears out of band (probe changes): only the tick can see it.
    free = {"mb": 0}
    mgr = VramReservationManager(probe=lambda: (free["mb"], 16384))
    arbiter = GpuArbiter(vram_reservation=mgr, drain_tick_seconds=0.1)
    await arbiter.start()
    try:
        async def fast(_res):
            return "ok"
        t = Task(capability=Capability.LLM_CHAT, payload=fast,
                 preferred_resources=[], priority=Priority.BACKGROUND, submitter="a")
        f = asyncio.ensure_future(arbiter.submit_gpu(t, required_vram_mb=1024))
        await asyncio.sleep(0.05)
        free["mb"] = 8192                       # external process freed VRAM
        assert await asyncio.wait_for(f, timeout=1.0) == "ok"
    finally:
        await arbiter.stop()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_gpu_arbiter_wakeup.py -v`
Expected: FAIL (`TypeError: __init__() got an unexpected keyword argument 'drain_tick_seconds'`; then, once added naively, `asyncio.TimeoutError` on the 0.5 s assertion).

- [ ] **Step 3: Implement**

```python
# __init__ additions
        self._drain_tick_seconds = drain_tick_seconds
        self._wake: asyncio.Event = asyncio.Event()

# _process_queue replacement for the sleep(2) loop (:536-543)
    async def _process_queue(self) -> None:
        try:
            while True:
                try:
                    await asyncio.wait_for(self._wake.wait(),
                                           timeout=self._drain_tick_seconds)
                except asyncio.TimeoutError:
                    pass
                self._wake.clear()
                if not self._paused:
                    await self._drain_queue()
        except asyncio.CancelledError:
            raise
```

Call `self._signal_capacity()` at the end of `_release_reservation` (:244, inside the `if reservation_id is not None` branch) and in the `finally` of `_run_gpu_task` (after the `_running` pop, :441). Both are synchronous and idempotent (`Event.set()`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_gpu_arbiter_wakeup.py tests/test_gpu_arbiter_894.py tests/test_gpu_arbiter_toctou.py tests/test_gpu_arbiter_queue_ops.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tinyagentos/scheduler/gpu_arbiter.py tests/test_gpu_arbiter_wakeup.py
git commit -m "feat(gpu-queue): event-driven admission wakeup on capacity release (#1864)"
```

**Acceptance criteria:** a queued op admits well under the tick interval after a release; the tick remains as a fallback for out-of-band capacity changes; pause/resume (`gpu_arbiter.py:150-182`) still gates draining (the `_paused` check stays).

---

### Slice A4: Aging promotion (spec section 8.3)

**Files:**
- Modify: `tinyagentos/scheduler/gpu_arbiter.py` (`__init__`, `_drain_queue` retry loop :602-610)
- Test: `tests/test_gpu_arbiter_aging.py` (new)

**Interfaces:**
- Consumes: A2 shadow dict, A3 tick loop.
- Produces: `GpuArbiter.__init__(..., aging_after_seconds: float = 60.0)`. Behavior: on each drain pass, any still-queued entry with `queued_seconds > aging_after_seconds` since its last promotion is re-created one priority class higher (numerically `max(int(Priority.INTERACTIVE_USER), entry.priority - 10)`), keeping its original `seq` and `queued_at`. `_QueuedGpuTask` gains `promoted_at: float = field(default=0.0, compare=False)`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_gpu_arbiter_aging.py
import asyncio
import pytest
from tinyagentos.scheduler.gpu_arbiter import GpuArbiter
from tinyagentos.scheduler.types import Capability, Priority, Task
from tinyagentos.vram_reservation import VramReservationManager


def _stuck_arbiter(aging: float) -> GpuArbiter:
    return GpuArbiter(
        vram_reservation=VramReservationManager(probe=lambda: (0, 16384)),
        drain_tick_seconds=0.05, aging_after_seconds=aging)


def _bg_task():
    async def payload(_res):
        return "ok"
    return Task(capability=Capability.LLM_CHAT, payload=payload,
                preferred_resources=[], priority=Priority.BACKGROUND, submitter="t")


@pytest.mark.asyncio
async def test_aging_promotes_one_class_per_interval():
    arbiter = _stuck_arbiter(aging=0.1)
    await arbiter.start()
    try:
        t = _bg_task()
        f = asyncio.ensure_future(arbiter.submit_gpu(t, required_vram_mb=1024, op="load"))
        await asyncio.sleep(0.3)                 # > 1 aging interval + drain
        snap = arbiter.queue_snapshot()
        assert snap[0]["priority"] < int(Priority.BACKGROUND)   # promoted
        f.cancel()
    finally:
        await arbiter.stop()


@pytest.mark.asyncio
async def test_aging_floor_is_interactive_user():
    arbiter = _stuck_arbiter(aging=0.05)
    await arbiter.start()
    try:
        t = _bg_task()
        f = asyncio.ensure_future(arbiter.submit_gpu(t, required_vram_mb=1024, op="load"))
        await asyncio.sleep(0.6)                 # many intervals
        snap = arbiter.queue_snapshot()
        assert snap[0]["priority"] == int(Priority.INTERACTIVE_USER)
        f.cancel()
    finally:
        await arbiter.stop()


@pytest.mark.asyncio
async def test_no_promotion_before_interval():
    arbiter = _stuck_arbiter(aging=60.0)
    await arbiter.start()
    try:
        t = _bg_task()
        f = asyncio.ensure_future(arbiter.submit_gpu(t, required_vram_mb=1024, op="load"))
        await asyncio.sleep(0.2)
        snap = arbiter.queue_snapshot()
        assert snap[0]["priority"] == int(Priority.BACKGROUND)
        f.cancel()
    finally:
        await arbiter.stop()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_gpu_arbiter_aging.py -v`
Expected: FAIL (`TypeError` on the new kwarg, then priority assertions).

- [ ] **Step 3: Implement**

In `_drain_queue`, replace the retry re-queue loop body with:

```python
        now = time.time()
        for entry in retry:
            anchor = entry.promoted_at or entry.queued_at
            if (self._aging_after_seconds > 0
                    and now - anchor > self._aging_after_seconds
                    and entry.priority > int(Priority.INTERACTIVE_USER)):
                entry = _QueuedGpuTask(
                    priority=max(int(Priority.INTERACTIVE_USER), entry.priority - 10),
                    seq=entry.seq, task=entry.task,
                    required_vram_mb=entry.required_vram_mb,
                    evictable=entry.evictable,
                    required_gpu_arch=entry.required_gpu_arch,
                    queued_at=entry.queued_at, op=entry.op, model=entry.model,
                    backend_name=entry.backend_name, promoted_at=now)
                logger.info("gpu-arbiter: aged task %s to priority %d",
                            entry.task.id, entry.priority)
            if not self._queue.full():
                self._queue.put_nowait(entry)
                self._queued_entries[entry.task.id] = entry
            else:
                ...  # existing drop branch unchanged
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_gpu_arbiter_aging.py tests/test_gpu_arbiter_queue_ops.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tinyagentos/scheduler/gpu_arbiter.py tests/test_gpu_arbiter_aging.py
git commit -m "feat(gpu-queue): priority aging so background loads cannot starve (#1864)"
```

**Acceptance criteria:** bounded promotion (one class per interval, floor `INTERACTIVE_USER`); shadow dict stays consistent with the live queue after promotion; zero promotion churn before the interval elapses.

---

### Slice A5: gpu_op trace emission hook

**Files:**
- Modify: `tinyagentos/scheduler/gpu_arbiter.py` (hook plumbing in `submit_gpu`, `_run_gpu_task`, `_evict_task`, `cancel_op`), `tinyagentos/app.py` (wiring, next to the arbiter build at :1183-1196)
- Test: `tests/test_gpu_arbiter_trace.py` (new)

**Interfaces:**
- Consumes: A1 (`gpu_op` kind), A2 (op fields on entries), `GpuAdmission.free_vram_mb` (`gpu_arbiter.py:61-68`, populated at :271-272), `VramReservationManager.reserved_vram_mb` (`vram_reservation.py:196-199`), `TraceStoreRegistry.get` (`trace_store.py:551-559`).
- Produces:

```python
# GpuArbiter.__init__ gains:
gpu_op_recorder: Callable[[str, dict], Awaitable[None]] | None = None
# recorder(slug, fields): fields are AgentTraceStore.record kwargs for
# kind="gpu_op": {model, backend_name, duration_ms, payload={...spec 6.1...}}

GPU_OP_SYSTEM_SLUG = "_system_"   # module constant in gpu_arbiter.py
```

Emission points (exactly one completion record per op, plus one on evict/cancel, spec section 6.1): success and error in `_run_gpu_task`'s finally, eviction in `_evict_task`, queued-cancel in `cancel_op`. Emission is `asyncio.create_task(...)` fire-and-forget wrapped so recorder failures are logged, never raised (off the critical path, spec section 8.5). Slug rule: `task.submitter` when it looks like an agent slug is NOT assumed; Phase A loads always record under `_system_` with `payload["submitter"]` carrying the precise `pull:{download_id}` string. `wait_ms` is `int((admitted_at - queued_at) * 1000)` for queued ops, `0` for inline admissions. `queue_position_at_enqueue` and `queue_depth_at_admit` are captured at those moments; `free_vram_mb_at_admit` and `reserved_vram_mb_at_admit` come from the admission result and the ledger property (no extra probe). `resident_models_at_admit` is `0` until Phase B wires the ResidencyManager; `evictions_triggered` is `[]` until B3.

**app.py wiring (after `app.state.gpu_arbiter` is set):**

```python
        async def _record_gpu_op(slug: str, fields: dict) -> None:
            store = await app.state.trace_registry.get(slug)
            await store.record("gpu_op", **fields)

        app.state.record_gpu_op = _record_gpu_op          # A7 shadow mode reuses this
        gpu_arbiter = GpuArbiter(..., gpu_op_recorder=_record_gpu_op)
```

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_gpu_arbiter_trace.py
import asyncio
import pytest
from tinyagentos.scheduler.gpu_arbiter import GpuArbiter, GPU_OP_SYSTEM_SLUG
from tinyagentos.scheduler.types import Capability, Priority, Task
from tinyagentos.vram_reservation import VramReservationManager


class Recorder:
    def __init__(self):
        self.records: list[tuple[str, dict]] = []

    async def __call__(self, slug: str, fields: dict) -> None:
        self.records.append((slug, fields))


def _task(submitter="pull:m-x"):
    async def payload(_res):
        return "ok"
    return Task(capability=Capability.LLM_CHAT, payload=payload,
                preferred_resources=[], priority=Priority.INTERACTIVE_AGENT,
                submitter=submitter)


@pytest.mark.asyncio
async def test_gpu_op_trace_on_completion():
    rec = Recorder()
    arbiter = GpuArbiter(
        vram_reservation=VramReservationManager(probe=lambda: (8192, 16384)),
        gpu_op_recorder=rec)
    await arbiter.submit_gpu(_task(), required_vram_mb=1024,
                             op="load", model="m-x", backend_name="b1")
    await asyncio.sleep(0.05)                     # let fire-and-forget land
    assert len(rec.records) == 1
    slug, fields = rec.records[0]
    assert slug == GPU_OP_SYSTEM_SLUG
    assert fields["model"] == "m-x" and fields["backend_name"] == "b1"
    p = fields["payload"]
    assert p["op"] == "load" and p["outcome"] == "ok"
    assert p["wait_ms"] == 0 and p["submitter"] == "pull:m-x"
    assert p["required_vram_mb"] == 1024 and p["priority"] == 20


@pytest.mark.asyncio
async def test_gpu_op_trace_outcome_evicted():
    rec = Recorder()
    arbiter = GpuArbiter(
        vram_reservation=VramReservationManager(probe=lambda: (8192, 16384)),
        gpu_op_recorder=rec)
    started = asyncio.Event()

    async def slow(_res):
        started.set()
        await asyncio.sleep(30)

    t = Task(capability=Capability.LLM_CHAT, payload=slow,
             preferred_resources=[], priority=Priority.BACKGROUND, submitter="s")
    f = asyncio.ensure_future(arbiter.submit_gpu(
        t, required_vram_mb=1024, op="load", model="m", backend_name="b1"))
    await started.wait()
    await arbiter.cancel_op(t.id)
    await asyncio.sleep(0.05)
    outcomes = [r[1]["payload"]["outcome"] for r in rec.records]
    assert "evicted" in outcomes
    f.cancel()


@pytest.mark.asyncio
async def test_recorder_error_does_not_fail_op():
    async def boom(_slug, _fields):
        raise RuntimeError("trace store down")
    arbiter = GpuArbiter(
        vram_reservation=VramReservationManager(probe=lambda: (8192, 16384)),
        gpu_op_recorder=boom)
    result = await arbiter.submit_gpu(_task(), required_vram_mb=1024,
                                      op="load", model="m", backend_name="b1")
    assert result == "ok"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_gpu_arbiter_trace.py -v`
Expected: FAIL (`ImportError: cannot import name 'GPU_OP_SYSTEM_SLUG'`, `TypeError` on the kwarg).

- [ ] **Step 3: Implement** the hook plumbing described in Interfaces: capture `queued_at`/`admitted_at`/admission data on the entry, add a private `_emit_gpu_op(entry_or_task, *, outcome, admission, wait_ms)` that builds the exact section 6.1 payload and schedules the recorder with `asyncio.create_task` guarded by try/except-log. Wire `app.py` as shown.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_gpu_arbiter_trace.py tests/test_gpu_arbiter_894.py tests/test_gpu_arbiter_toctou.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tinyagentos/scheduler/gpu_arbiter.py tinyagentos/app.py tests/test_gpu_arbiter_trace.py
git commit -m "feat(gpu-queue): per-op gpu_op trace emission through AgentTraceStore (#1864)"
```

**Acceptance criteria:** exactly one completion record per op; evict/cancel records carry the right outcome; recorder failure never affects op results; no recorder call sits on the awaited path (fire-and-forget); records flow to OTLP automatically because the emitter is already injected into every store (`app.py:1072-1078`).

---

### Slice A6: EventBus notify passthrough + gpu.queue.update events

**Files:**
- Modify: `tinyagentos/events/bus.py` (`emit` :90-159, `emit_event` :181-198), `tinyagentos/scheduler/gpu_arbiter.py` (emitter hook + coalescing), `tinyagentos/app.py` (wiring)
- Test: `tests/test_event_bus_notify.py` (new), `tests/test_gpu_arbiter_events.py` (new)

**Interfaces:**
- Consumes: `SystemEvent` (`bus.py:15-23`); A2 queue transitions.
- Produces:

```python
# bus.py
async def emit(self, event, *, notifications, agent_messages, trace_store,
               permission_check=None, notify: bool = True) -> None
    # notify=False suppresses ONLY step (c) notification derivation
    # (bus.py:124-133); trace persistence and pub/sub are unchanged.

async def emit_event(app_state, event: SystemEvent, *, notify: bool = True) -> None

# GpuArbiter.__init__ gains:
event_emitter: Callable[[SystemEvent], Awaitable[None]] | None = None
```

Event contract (spec section 5.3): kind `gpu.queue.update`, `source="gpu-queue"`, `targets=["user"]`, `level="info"`, payload `{"entries": [{task_id, op, model, submitter, position}], "queue_depth": int}` emitted on enqueue/admit/complete/cancel/position change, coalesced to at least 250 ms between emissions (a pending emission timer collapses bursts; the latest state wins). Kind `gpu.residency.update` is reserved here (constant defined) and first emitted in B3. app.py wires `event_emitter=lambda ev: emit_event(app.state, ev, notify=False)` (as an async def closure).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_event_bus_notify.py
import pytest
from tinyagentos.events.bus import EventBus, SystemEvent


class _Sink:
    def __init__(self):
        self.calls = []

    async def add(self, *a, **k):
        self.calls.append((a, k))

    async def send(self, **k):
        self.calls.append(k)


@pytest.mark.asyncio
async def test_emit_notify_false_skips_notification_for_user_target():
    bus = EventBus()
    notifications, agent_messages, trace = _Sink(), _Sink(), _Sink()
    ev = SystemEvent(kind="gpu.queue.update", source="gpu-queue",
                     targets=["user"], payload={"queue_depth": 1})
    await bus.emit(ev, notifications=notifications, agent_messages=agent_messages,
                   trace_store=trace, notify=False)
    assert notifications.calls == []          # no notification derived
    assert trace.calls != []                  # still persisted to trace


@pytest.mark.asyncio
async def test_emit_notify_default_behavior_unchanged():
    bus = EventBus()
    notifications, agent_messages, trace = _Sink(), _Sink(), _Sink()
    ev = SystemEvent(kind="worker.join", source="cluster",
                     targets=["user"], payload={})
    await bus.emit(ev, notifications=notifications, agent_messages=agent_messages,
                   trace_store=trace)
    assert len(notifications.calls) == 1
```

```python
# tests/test_gpu_arbiter_events.py
import asyncio
import pytest
from tinyagentos.scheduler.gpu_arbiter import GpuArbiter
from tinyagentos.scheduler.types import Capability, Priority, Task
from tinyagentos.vram_reservation import VramReservationManager


@pytest.mark.asyncio
async def test_queue_update_emitted_and_coalesced():
    events = []

    async def emitter(ev):
        events.append(ev)

    arbiter = GpuArbiter(
        vram_reservation=VramReservationManager(probe=lambda: (0, 16384)),
        event_emitter=emitter, queue_event_coalesce_seconds=0.25)
    futs = []
    for m in ("a", "b", "c"):
        async def payload(_res):
            return "ok"
        t = Task(capability=Capability.LLM_CHAT, payload=payload,
                 preferred_resources=[], priority=Priority.BACKGROUND, submitter="s")
        futs.append(asyncio.ensure_future(arbiter.submit_gpu(
            t, required_vram_mb=1024, op="load", model=m, backend_name="b1")))
    await asyncio.sleep(0.1)
    burst = [e for e in events if e.kind == "gpu.queue.update"]
    assert len(burst) <= 1                    # 3 enqueues within 250 ms coalesce
    await asyncio.sleep(0.3)
    settled = [e for e in events if e.kind == "gpu.queue.update"]
    assert settled, "coalesced emission must eventually flush"
    assert settled[-1].payload["queue_depth"] == 3
    assert {en["model"] for en in settled[-1].payload["entries"]} == {"a", "b", "c"}
    for f in futs:
        f.cancel()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_event_bus_notify.py tests/test_gpu_arbiter_events.py -v`
Expected: FAIL (`TypeError: emit() got an unexpected keyword argument 'notify'`, then kwarg errors on the arbiter).

- [ ] **Step 3: Implement.** In `bus.py`, thread `notify` through `emit` (guard step c with `if notify and should_notify_user:`) and `emit_event`. In the arbiter, add `queue_event_coalesce_seconds: float = 0.25`, a `_queue_event_pending: bool` flag and `_flush_queue_event()` that builds the payload from `queue_snapshot()` and schedules itself at most once per coalesce window (an `asyncio.get_running_loop().call_later` that creates the emit task). Call the scheduler function from enqueue, admit, complete, and cancel paths.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_event_bus_notify.py tests/test_gpu_arbiter_events.py tests/test_gpu_arbiter_queue_ops.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tinyagentos/events/bus.py tinyagentos/scheduler/gpu_arbiter.py tinyagentos/app.py tests/test_event_bus_notify.py tests/test_gpu_arbiter_events.py
git commit -m "feat(gpu-queue): coalesced gpu.queue.update SSE events with non-notifying emit (#1864)"
```

**Acceptance criteria:** `notify=False` suppresses only notification derivation (SSE delivery via `targets=["user"]` still works because pub/sub publication is untouched, `bus.py:153-159`); queue churn cannot spam notifications; existing `emit` callers are unaffected (default `notify=True`).

---

### Slice A7: Pull sites submit through the arbiter (off/shadow/on)

**Files:**
- Modify: `tinyagentos/routes/models.py` (rkllama branch :389-466, `pull_model` :658-712, `_task_to_dict` :516-531), `tinyagentos/download_manager.py` (`DownloadTask` :15-25, new method)
- Test: `tests/test_routes_models_queue.py` (new), `tests/test_download_manager.py` (extend)

**Interfaces:**
- Consumes: A1 `gpu_queue_mode()`; A2 `submit_gpu(op="load", ...)` + `queue_position`; A5 `app.state.record_gpu_op`; `DownloadManager.start_installer_task` semantics (`download_manager.py:97-122`); `Priority.INTERACTIVE_AGENT` (decision 6).
- Produces:

```python
# download_manager.py
@dataclass
class DownloadTask:
    ...
    status: str = "pending"  # pending | queued | downloading | complete | error | cancelled
    gpu_task_id: str | None = None

def start_queued_installer(
    self, download_id: str, coro_factory, *, arbiter, task,
    required_vram_mb: int, op_model: str, backend_name: str,
) -> DownloadTask:
    """Track an installer op admitted through the GPU arbiter.

    coro_factory is the same callable-accepting-on_progress shape
    start_installer_task takes. The DownloadTask starts status='queued';
    the wrapped payload flips it to 'downloading' when the arbiter admits.
    The arbiter future is awaited inside the tracked asyncio task; a
    NoResourceAvailableError or cancellation maps to status='error' /
    'cancelled'. Stores task.id as gpu_task_id for position lookups."""

# models.py
def _task_to_dict(task, arbiter=None) -> dict:
    # adds "queue_position": arbiter.queue_position(task.gpu_task_id)
    # when status == "queued" and both are present, else None
```

Route behavior by mode:

- `off`: byte-for-byte today's code (the existing reserve-or-503 block stays as-is behind `if mode == "off"`).
- `shadow`: today's behavior, plus on reservation denial fire `app.state.record_gpu_op("_system_", {...payload op="load", outcome="shadow_denied", required_vram_mb=estimated...})` before returning the 503.
- `on`: skip the route-level reservation entirely (the arbiter reserves at admission); build `Task(capability=Capability.LLM_CHAT, payload=<wrapped installer>, preferred_resources=[], priority=Priority.INTERACTIVE_AGENT, submitter=f"pull:{download_id}", estimated_vram_mb=estimated)`; call `dm.start_queued_installer(...)`; respond `202`-style JSON `{"status": "queued", "download_id": ..., "position": N, "app_id": ..., "variant_id": ...}` when the op queued, or `{"status": "started", ...}` when admitted inline. `NoResourceAvailableError` (queue full) maps to 503 with `{"error": "GPU queue full", "queue_depth": ...}` (the only remaining 503). `pull_model` (ollama) switches its blocking 300 s POST (`models.py:696-700`) to the same shape: `download_id = f"ollama-pull-{model_name}"`, installer coroutine POSTs `{ollama_url}/api/pull` with `json={"name": model_name, "stream": False}, timeout=None`, and the route returns immediately with `{"status": "queued"|"started", "download_id", "position"}`.

- [ ] **Step 1: Write the failing tests** (pattern-match fixtures from `tests/test_routes_models.py`; use `httpx.AsyncClient(transport=ASGITransport(app=app))` and a monkeypatched `gpu_queue_mode`)

```python
# tests/test_routes_models_queue.py  (key tests; full file mirrors existing route-test fixtures)
async def test_download_rkllama_mode_off_still_503(app_factory, monkeypatch):
    monkeypatch.setenv("TAOS_GPU_QUEUE", "off")
    # vram probe (0, 16384): reservation denied
    resp = await client.post("/api/models/download", json={"app_id": "m", "variant_id": "v"})
    assert resp.status_code == 503

async def test_download_rkllama_mode_shadow_503_plus_trace(app_factory, monkeypatch):
    monkeypatch.setenv("TAOS_GPU_QUEUE", "shadow")
    resp = await client.post("/api/models/download", json={"app_id": "m", "variant_id": "v"})
    assert resp.status_code == 503
    assert recorded and recorded[0][1]["payload"]["outcome"] == "shadow_denied"

async def test_download_rkllama_mode_on_queues_with_position(app_factory, monkeypatch):
    monkeypatch.setenv("TAOS_GPU_QUEUE", "on")
    resp = await client.post("/api/models/download", json={"app_id": "m", "variant_id": "v"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "queued" and body["position"] == 1
    progress = await client.get(f"/api/models/downloads/{body['download_id']}")
    assert progress.json()["status"] == "queued"
    assert progress.json()["queue_position"] == 1

async def test_download_rkllama_mode_on_admits_inline_when_vram_free(app_factory, monkeypatch):
    monkeypatch.setenv("TAOS_GPU_QUEUE", "on")
    # probe (16384, 16384): admitted inline
    resp = await client.post("/api/models/download", json={"app_id": "m", "variant_id": "v"})
    assert resp.json()["status"] == "started"

async def test_pull_ollama_mode_on_returns_download_id_immediately(app_factory, monkeypatch):
    monkeypatch.setenv("TAOS_GPU_QUEUE", "on")
    resp = await client.post("/api/models/pull",
                             json={"model_name": "qwen2.5:7b", "required_vram_mb": 4096})
    body = resp.json()
    assert body["download_id"] == "ollama-pull-qwen2.5:7b"
    assert body["status"] in ("queued", "started")

async def test_queue_overflow_maps_503(app_factory, monkeypatch):
    monkeypatch.setenv("TAOS_GPU_QUEUE", "on")
    # arbiter constructed with max_queue_size=1 and VRAM exhausted; second pull overflows
    ...
    assert resp2.status_code == 503
    assert "queue full" in resp2.json()["error"].lower()
```

Plus in `tests/test_download_manager.py`: `test_start_queued_installer_tracks_queue_lifecycle` (fake arbiter admits after an event fires; DownloadTask transitions queued, then downloading, then complete) and `test_queued_installer_arbiter_error_maps_to_error_status`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_routes_models_queue.py tests/test_download_manager.py -v`
Expected: FAIL (`AttributeError: 'DownloadManager' object has no attribute 'start_queued_installer'`, then mode-on assertions).

- [ ] **Step 3: Implement** per the Interfaces block. In `start_queued_installer`, wrap the payload:

```python
        task_rec = DownloadTask(id=download_id, url="", dest=Path(),
                                status="queued", gpu_task_id=task.id)
        self._tasks[download_id] = task_rec

        def _on_progress(completed: int, total: int) -> None:
            task_rec.downloaded_bytes = completed
            task_rec.total_bytes = total

        inner = coro_factory(_on_progress) if callable(coro_factory) \
            and not asyncio.iscoroutine(coro_factory) else coro_factory

        async def _admitted_payload(_res):
            task_rec.status = "downloading"
            task_rec.started_at = time.time()
            return await inner

        task.payload = _admitted_payload

        async def _drive():
            try:
                result = await arbiter.submit_gpu(
                    task, required_vram_mb=required_vram_mb,
                    op="load", model=op_model, backend_name=backend_name)
            except asyncio.CancelledError:
                task_rec.status = "cancelled"
                return
            except Exception as exc:
                task_rec.status = "error"
                task_rec.error = str(exc)
                return
            if not (isinstance(result, dict) and result.get("success")):
                task_rec.status = "error"
                task_rec.error = (result or {}).get("error", "install failed") \
                    if isinstance(result, dict) else "install failed"
                return
            task_rec.status = "complete"
            task_rec.completed_at = time.time()

        self._running[download_id] = asyncio.create_task(_drive())
        return task_rec
```

Keep `NoResourceAvailableError` importable in `models.py` for the 503 mapping. `GET /api/models/downloads/{id}` and the list route pass `arbiter=getattr(request.app.state, "gpu_arbiter", None)` into `_task_to_dict`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_routes_models_queue.py tests/test_routes_models.py tests/test_download_manager.py -v`
Expected: PASS. The pre-existing 503 tests in `test_routes_models.py` pass untouched because the default mode is `off`.

- [ ] **Step 5: Commit**

```bash
git add tinyagentos/routes/models.py tinyagentos/download_manager.py tests/test_routes_models_queue.py tests/test_download_manager.py
git commit -m "feat(gpu-queue): pull routes queue through the arbiter behind TAOS_GPU_QUEUE (#1864)"
```

**Acceptance criteria:** all three modes behave per the table in spec section 7.1; mode `on` never 503s on VRAM shortage; ollama pull no longer blocks the HTTP request for up to 300 s in mode `on`; user pulls carry `Priority.INTERACTIVE_AGENT`; `queue_position` surfaces through the existing poll endpoint so the frontend needs no second polling mechanism (spec section 5.4).

---

### Slice A8: DownloadManager cancel API, end-to-end pull cancel (locked decision 3)

**Files:**
- Modify: `tinyagentos/download_manager.py`, `tinyagentos/routes/models.py` (new `DELETE /api/models/downloads/{download_id}`)
- Test: `tests/test_download_manager.py` (extend), `tests/test_routes_models_cancel.py` (new)

**Interfaces:**
- Consumes: A2 `cancel_op`, A7 `gpu_task_id` on `DownloadTask`, `_running` asyncio-task map (`download_manager.py:31`).
- Produces:

```python
# download_manager.py
async def cancel(self, download_id: str,
                 cleanup: "Callable[[], Awaitable[None]] | None" = None) -> bool:
    """First-class cancel (taOS #1864, decision 3): cancel the tracked
    asyncio task, AWAIT its unwind, set status='cancelled', then run the
    optional backend cleanup (delete of the partial model). Returns False
    for unknown or already-finished downloads. Idempotent."""
```

Route `DELETE /api/models/downloads/{download_id}`:

1. Look up the task; 404 if unknown.
2. If `gpu_task_id` is set and the op is still queued, `await arbiter.cancel_op(gpu_task_id)` first (removes the queue entry; nothing was reserved, spec section 5.5).
3. `await dm.cancel(download_id, cleanup=_backend_cleanup)` where `_backend_cleanup` for ollama-compatible pulls POSTs the backend's model delete as the backstop: ollama `DELETE {base}/api/delete` body `{"name": model_name}` (httpx `request("DELETE", ...)` since the body matters), best-effort with a 10 s timeout, logged on failure. Whether closing the pull stream halts the server-side download is the documented open question (spec section 10.3); the delete is the reclaim backstop either way. In Phase B, B3 extends this cleanup to also unload if the model registered as loaded.
4. Respond `{"cancelled": true, "download_id": ...}`.

- [ ] **Step 1: Write the failing tests**

```python
# additions to tests/test_download_manager.py
@pytest.mark.asyncio
async def test_cancel_running_installer_sets_cancelled_and_runs_cleanup():
    dm = DownloadManager()
    entered = asyncio.Event()

    async def forever():
        entered.set()
        await asyncio.sleep(60)
        return {"success": True}

    dm.start_installer_task("d1", forever())
    await entered.wait()
    cleaned = []

    async def cleanup():
        cleaned.append(True)

    assert await dm.cancel("d1", cleanup=cleanup) is True
    assert dm.get_progress("d1").status == "cancelled"
    assert cleaned == [True]


@pytest.mark.asyncio
async def test_cancel_unknown_or_finished_returns_false():
    dm = DownloadManager()
    assert await dm.cancel("nope") is False

    async def done():
        return {"success": True}

    dm.start_installer_task("d2", done())
    await asyncio.sleep(0.05)
    assert await dm.cancel("d2") is False       # already complete
    assert dm.get_progress("d2").status == "complete"


@pytest.mark.asyncio
async def test_cancel_is_idempotent():
    dm = DownloadManager()

    async def forever():
        await asyncio.sleep(60)
        return {"success": True}

    dm.start_installer_task("d3", forever())
    assert await dm.cancel("d3") is True
    assert await dm.cancel("d3") is False
```

```python
# tests/test_routes_models_cancel.py (key assertions; fixtures mirror test_routes_models.py)
async def test_delete_download_cancels_queued_op_and_releases_everything(...):
    # mode on, VRAM exhausted so the pull queued
    resp = await client.delete(f"/api/models/downloads/{download_id}")
    assert resp.json()["cancelled"] is True
    assert arbiter.queue_position(gpu_task_id) is None
    assert vram_mgr.reserved_vram_mb == 0
    assert dm.get_progress(download_id).status == "cancelled"

async def test_delete_download_running_issues_backend_delete(...):
    # httpx mock backend records a DELETE /api/delete with {"name": model}
    assert deleted_bodies == [{"name": "qwen2.5:7b"}]

async def test_delete_download_unknown_404(...):
    resp = await client.delete("/api/models/downloads/nope")
    assert resp.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_download_manager.py tests/test_routes_models_cancel.py -v`
Expected: FAIL (`AttributeError: 'DownloadManager' object has no attribute 'cancel'`; 405 on the DELETE route).

- [ ] **Step 3: Implement**

```python
# download_manager.py
    async def cancel(self, download_id: str, cleanup=None) -> bool:
        running = self._running.get(download_id)
        task_rec = self._tasks.get(download_id)
        if task_rec is None or running is None or running.done():
            return False
        if task_rec.status in ("complete", "error", "cancelled"):
            return False
        running.cancel()
        try:
            await running
        except (asyncio.CancelledError, Exception):
            pass
        task_rec.status = "cancelled"
        task_rec.completed_at = time.time()
        if cleanup is not None:
            try:
                await cleanup()
            except Exception as exc:
                logger.warning("download cancel cleanup failed for %s: %s",
                               download_id, exc)
        return True
```

Note ordering: cancel and await FIRST, cleanup second, mirroring the evict ordering rationale (`gpu_arbiter.py:503-521`), so the delete backstop never races the still-writing pull. Add the DELETE route in `models.py` per the Interfaces block.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_download_manager.py tests/test_routes_models_cancel.py tests/test_routes_models_queue.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tinyagentos/download_manager.py tinyagentos/routes/models.py tests/test_download_manager.py tests/test_routes_models_cancel.py
git commit -m "feat(gpu-queue): first-class download cancel with backend delete backstop (#1864)"
```

**Acceptance criteria:** cancel works for queued and in-flight pulls; reservation, queue entry, and DownloadTask state all reconcile; the backend delete is issued after unwind; repeated cancels are safe; the pre-existing no-cancel gap (spec section 5.5) is closed by API, not by UI hacks.

---

### Slice A9: Queue API routes

**Files:**
- Create: `tinyagentos/routes/gpu_queue.py`
- Modify: `tinyagentos/routes/__init__.py` (`register_all_routers`, add alongside the scheduler router)
- Test: `tests/test_routes_gpu_queue.py` (new)

**Interfaces:**
- Consumes: A2 `queue_snapshot()` / `cancel_op()`, existing `running_tasks()` (`gpu_arbiter.py:625-632`) and `stats()` (:612-623); the 503-when-missing pattern from `routes/scheduler.py:12-18`.
- Produces (spec section 5.2):

```python
# GET /api/gpu/queue ->
{"queue": [...queue_snapshot()...], "running": [...running_tasks()...],
 "residents": [], "stats": {...stats()...}}
# "residents" is filled by B3 via arbiter.residency_snapshot(); empty list until then.
# DELETE /api/gpu/queue/{task_id} -> {"cancelled": bool} | 404
```

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_routes_gpu_queue.py (fixtures mirror tests/test_activity_route.py app-state stubbing)
async def test_gpu_queue_snapshot_shape(client_with_arbiter):
    resp = await client.get("/api/gpu/queue")
    body = resp.json()
    assert set(body) == {"queue", "running", "residents", "stats"}
    assert body["queue"][0]["position"] == 1
    assert body["stats"]["queue_depth"] == 1

async def test_gpu_queue_arbiter_missing_503(client_without_arbiter):
    resp = await client.get("/api/gpu/queue")
    assert resp.status_code == 503

async def test_gpu_queue_cancel_queued(client_with_arbiter):
    resp = await client.delete(f"/api/gpu/queue/{task_id}")
    assert resp.json() == {"cancelled": True}

async def test_gpu_queue_cancel_unknown_404(client_with_arbiter):
    resp = await client.delete("/api/gpu/queue/nope")
    assert resp.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_routes_gpu_queue.py -v`
Expected: FAIL (404 on `/api/gpu/queue`: router not registered).

- [ ] **Step 3: Implement**

```python
# tinyagentos/routes/gpu_queue.py
"""GPU work queue observability + cancel (taOS #1864). Read pattern
mirrors routes/scheduler.py; cancel delegates to GpuArbiter.cancel_op."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()


def _arbiter(request: Request):
    return getattr(request.app.state, "gpu_arbiter", None)


@router.get("/api/gpu/queue")
async def gpu_queue(request: Request):
    arbiter = _arbiter(request)
    if arbiter is None:
        return JSONResponse({"error": "gpu arbiter not initialised"}, status_code=503)
    residents = []
    snapshot_fn = getattr(arbiter, "residency_snapshot", None)   # B3 adds this
    if snapshot_fn is not None:
        residents = snapshot_fn()
    return {"queue": arbiter.queue_snapshot(),
            "running": await arbiter.running_tasks(),
            "residents": residents,
            "stats": await arbiter.stats()}


@router.delete("/api/gpu/queue/{task_id}")
async def gpu_queue_cancel(request: Request, task_id: str):
    arbiter = _arbiter(request)
    if arbiter is None:
        return JSONResponse({"error": "gpu arbiter not initialised"}, status_code=503)
    cancelled = await arbiter.cancel_op(task_id)
    if not cancelled:
        return JSONResponse({"error": f"task '{task_id}' not found"}, status_code=404)
    return {"cancelled": True}
```

Register in `routes/__init__.py`: `from tinyagentos.routes.gpu_queue import router as gpu_queue_router` + `app.include_router(gpu_queue_router)`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_routes_gpu_queue.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tinyagentos/routes/gpu_queue.py tinyagentos/routes/__init__.py tests/test_routes_gpu_queue.py
git commit -m "feat(gpu-queue): /api/gpu/queue snapshot and cancel routes (#1864)"
```

**Acceptance criteria:** snapshot is cheap (pure shadow-dict read, safe for polling); cancel returns honest 404s; degrades to 503 when the arbiter failed to start (`app.py:1194-1196`).

---

### Slice A10: ModelsApp queued state (poll-driven)

**Files:**
- Modify: `desktop/src/apps/ModelsApp.tsx` (`DownloadState` :58-63, `DownloadProgress` poll loop and card, `handleDownload`)
- Test: `desktop/src/apps/ModelsApp.download.test.tsx` (extend)

**Interfaces:**
- Consumes: A7 response `{status: "queued", download_id, position}` and poll payload `{status: "queued", queue_position}`; A8 `DELETE /api/models/downloads/{id}`.
- Produces:

```ts
interface DownloadState {
  downloadId?: string;
  percent: number;
  status: "starting" | "queued" | "downloading" | "complete" | "error";
  error?: string;
  queuePosition?: number;   // set while status === "queued"
}
```

Behavior: `handleDownload` maps a `{status: "queued"}` response to `status: "queued"` with `queuePosition: data.position`. The poll loop maps `data.status === "queued"` to the queued state (keeps polling) and `data.status === "cancelled"` to clearing the card. The card renders "Queued behind N" with an indeterminate bar plus a Cancel button that issues the DELETE and clears on success. All controls keep ARIA labels (accessibility bar: the indeterminate bar gets `role="progressbar"` with `aria-valuetext="Queued behind N"`, the button `aria-label="Cancel download"`).

- [ ] **Step 1: Write the failing tests**

```tsx
// additions to desktop/src/apps/ModelsApp.download.test.tsx (msw/fetch-mock per existing file conventions)
it("renders queued card with position from a queued download response", async () => {
  mockFetch("/api/models/download", { status: "queued", download_id: "m-v", position: 2 });
  mockFetch("/api/models/downloads/m-v", { status: "queued", queue_position: 2, percent: 0 });
  // ...click Download...
  expect(await screen.findByText(/queued behind 2/i)).toBeInTheDocument();
});

it("transitions queued to downloading to complete via the poll loop", async () => {
  // poll sequence: queued(pos 1) -> downloading 40% -> complete
  expect(await screen.findByText(/queued behind 1/i)).toBeInTheDocument();
  expect(await screen.findByText(/40/)).toBeInTheDocument();
  // complete triggers onComplete refresh as today
});

it("cancel button issues DELETE and clears the card", async () => {
  // queued card visible; click the Cancel download button
  fireEvent.click(screen.getByRole("button", { name: /cancel download/i }));
  await waitFor(() =>
    expect(fetchCalls).toContainEqual(
      expect.objectContaining({ url: "/api/models/downloads/m-v", method: "DELETE" })));
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd desktop && npm test -- src/apps/ModelsApp.download.test.tsx`
Expected: FAIL (no queued rendering).

- [ ] **Step 3: Implement** the `DownloadState` extension, poll-loop branch, queued card, and cancel handler in `ModelsApp.tsx`, following the existing card styles (`DownloadProgress` component) and the existing error-card pattern for cancel failures.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd desktop && npm test -- src/apps/ModelsApp.download.test.tsx src/apps/ModelsApp.test.tsx`
Expected: PASS, existing download tests included.

- [ ] **Step 5: Commit**

```bash
git add desktop/src/apps/ModelsApp.tsx desktop/src/apps/ModelsApp.download.test.tsx
git commit -m "feat(models-app): queued download card with position and cancel (#1864)"
```

**Acceptance criteria:** queued pulls show "Queued behind N" instead of an error; cancel works from the card; flag-off behavior identical (backend never returns `queued` in `off` mode); keyboard and screen-reader accessible. Per the self-verify policy, boot the real desktop against a controller in `on` mode with a full VRAM probe fake and screenshot the queued card before calling this slice done.

---

# Phase B: residency manager, unload primitive, RK3588 probe, idle-LRU eviction

Exit criteria: under VRAM pressure in `on` mode, the arbiter frees real VRAM by unloading idle LRU models (verified against `/api/ps`), never touches an active model, fires on the Pi via the RK3588 probe, and emits `gpu.residency.update` events. Fail-open hosts without any probe still never evict (spec section 4.4).

### Slice B1: backend unload adapters (locked decision 2)

**Files:**
- Create: `tinyagentos/backend_unload.py`
- Test: `tests/test_backend_unload.py` (new)

**Interfaces:**
- Consumes: nothing in-repo (pure httpx adapters).
- Produces:

```python
# tinyagentos/backend_unload.py
UNLOAD_CAPABLE_TYPES: frozenset[str] = frozenset({"ollama", "rkllama"})
# llama-cpp / vllm are single-model servers: unload_capable False in Phase 1
# (spec section 3.3); process-level stop stays with LifecycleManager.
# hailo-ollama is a deliberate follow-up, not silently included.

def unload_capable(backend_type: str) -> bool: ...

async def unload_model(client: "httpx.AsyncClient", *, backend_type: str,
                       base_url: str, model: str, timeout: float = 15.0) -> bool:
    """Ask the backend to unload *model* now. Returns True on 2xx.
    Never raises; connection errors and non-2xx log and return False.

    ollama:  POST {base}/api/generate  {"model": model, "keep_alive": 0}
             (documented immediate-unload idiom, no prompt; spec 3.3)
    rkllama: POST {base}/unload_model  {"model": model}
             (explicit route on the deployed fork, verified live on the Pi
             2026-07-17; owner decision 2 says use this, not keep_alive)."""
```

**Live-Pi flag:** the rkllama route existence is verified; the exact JSON body key must be confirmed against the deployed fork (`src/rkllama/server/server.py:368`) during the B5 hardware gate. If the fork expects `{"model_name": ...}` instead, change only the adapter body and its request-shape test; the interface above is stable either way.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_backend_unload.py
import httpx
import pytest
from tinyagentos.backend_unload import UNLOAD_CAPABLE_TYPES, unload_capable, unload_model


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_ollama_unload_request_shape():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["json"] = __import__("json").loads(request.content)
        return httpx.Response(200, json={"done": True})

    async with _client(handler) as client:
        ok = await unload_model(client, backend_type="ollama",
                                base_url="http://127.0.0.1:11434", model="qwen2.5:7b")
    assert ok is True
    assert seen["url"] == "http://127.0.0.1:11434/api/generate"
    assert seen["json"] == {"model": "qwen2.5:7b", "keep_alive": 0}


@pytest.mark.asyncio
async def test_rkllama_unload_uses_explicit_route():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["json"] = __import__("json").loads(request.content)
        return httpx.Response(200, json={"status": "unloaded"})

    async with _client(handler) as client:
        ok = await unload_model(client, backend_type="rkllama",
                                base_url="http://127.0.0.1:7833", model="qwen-rk")
    assert ok is True
    assert seen["url"] == "http://127.0.0.1:7833/unload_model"
    assert seen["json"] == {"model": "qwen-rk"}


def test_unload_capable_matrix():
    assert unload_capable("ollama") and unload_capable("rkllama")
    for t in ("llama-cpp", "vllm", "sd-cpp", "openai", "hailo-ollama"):
        assert not unload_capable(t)
    assert UNLOAD_CAPABLE_TYPES == frozenset({"ollama", "rkllama"})


@pytest.mark.asyncio
async def test_unload_returns_false_on_error_and_never_raises():
    def error_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    async with _client(error_handler) as client:
        assert await unload_model(client, backend_type="ollama",
                                  base_url="http://x", model="m") is False

    def raiser(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    async with _client(raiser) as client:
        assert await unload_model(client, backend_type="ollama",
                                  base_url="http://x", model="m") is False


@pytest.mark.asyncio
async def test_unknown_type_returns_false_without_request():
    async with _client(lambda r: httpx.Response(500)) as client:
        assert await unload_model(client, backend_type="vllm",
                                  base_url="http://x", model="m") is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_backend_unload.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement** the module exactly per the docstring contract (one small `try/except Exception: return False` around the POST, `base_url.rstrip("/")`, `resp.is_success`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_backend_unload.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tinyagentos/backend_unload.py tests/test_backend_unload.py
git commit -m "feat(gpu-queue): backend unload primitive for ollama and rkllama (#1864)"
```

**Acceptance criteria:** request shapes exactly as specified; non-capable types short-circuit False with zero network traffic; nothing outside the arbiter path will import this module (enforced in B3 review: the registered unload mechanism is invoked exclusively from admission/eviction, spec section 3.3).

---

### Slice B2: ResidencyManager

**Files:**
- Create: `tinyagentos/scheduler/gpu_residency.py`
- Test: `tests/scheduler/test_gpu_residency.py` (new)

**Interfaces:**
- Consumes: `/api/ps` payload shape (`models` list with `name`/`model`, `size_vram`, `expires_at`; consumed the same way `loaded_models` does at `routes/models.py:804-822`).
- Produces:

```python
# tinyagentos/scheduler/gpu_residency.py
@dataclass
class ResidentModel:
    model: str
    backend_name: str
    backend_type: str
    vram_mb: int                 # from /api/ps size_vram; estimate until reconciled
    loaded_at: float
    last_active_at: float        # updated when active_ops drops to 0
    active_ops: int = 0
    expires_at: str | None = None   # backend-reported keep_alive expiry (observability)


class ResidencyManager:
    def __init__(self, *, grace_seconds: float = 30.0) -> None: ...

    def note_op_started(self, model: str, backend_name: str,
                        backend_type: str, vram_estimate_mb: int = 0) -> None:
        """Increment active_ops; register a provisional resident if unknown
        (reconcile corrects vram_mb from /api/ps later)."""

    def note_op_finished(self, model: str, backend_name: str) -> None:
        """Decrement active_ops (floor 0); stamp last_active_at when it
        reaches 0. Unknown keys are a no-op (reconcile may have removed)."""

    def is_resident(self, model: str, backend_name: str) -> bool: ...
    def vram_estimate(self, model: str, backend_name: str) -> int:
        """Last known footprint for this model, 0 if never seen. C3 uses
        this to account VRAM for inference on a non-resident model."""

    def eviction_candidates(self, now: float | None = None) -> list[ResidentModel]:
        """IDLE models only (active_ops == 0), idle for at least
        grace_seconds, ordered by last_active_at ascending (LRU first).
        ACTIVE models are structurally excluded (spec 3.2)."""

    def reconcile(self, backend_name: str, backend_type: str,
                  ps_models: list[dict]) -> None:
        """Sync with one backend's /api/ps: discover out-of-band loads,
        update vram_mb/expires_at, drop models no longer reported UNLESS
        they have active_ops > 0 (an admitted op that has not hit the
        backend yet must not be forgotten)."""

    def remove(self, model: str, backend_name: str) -> None: ...
    def snapshot(self) -> list[dict]:
        # [{model, backend_name, backend_type, vram_mb,
        #   state: "active"|"idle", active_ops, last_active_at, expires_at}]
```

Keying is `(backend_name, model)`. Pure in-memory, no I/O, no locks needed (single event loop; all callers are arbiter-side).

- [ ] **Step 1: Write the failing tests**

```python
# tests/scheduler/test_gpu_residency.py
import time
from tinyagentos.scheduler.gpu_residency import ResidencyManager


def _mgr(grace=0.0):
    return ResidencyManager(grace_seconds=grace)


def test_active_iff_ops_positive():
    m = _mgr()
    m.note_op_started("a", "b1", "ollama", vram_estimate_mb=1000)
    assert m.snapshot()[0]["state"] == "active"
    m.note_op_finished("a", "b1")
    assert m.snapshot()[0]["state"] == "idle"


def test_never_candidate_while_active():
    m = _mgr()
    m.note_op_started("a", "b1", "ollama")
    m.note_op_started("idle-m", "b1", "ollama")
    m.note_op_finished("idle-m", "b1")
    names = [r.model for r in m.eviction_candidates()]
    assert names == ["idle-m"]          # active model absent under any pressure


def test_lru_ordering_by_last_active():
    m = _mgr()
    for name, when in (("old", 100.0), ("new", 200.0)):
        m.note_op_started(name, "b1", "ollama")
        m.note_op_finished(name, "b1")
        m._residents[("b1", name)].last_active_at = when   # deterministic clock
    assert [r.model for r in m.eviction_candidates(now=1000.0)] == ["old", "new"]


def test_grace_period_excludes_recent_idle():
    m = _mgr(grace=30.0)
    m.note_op_started("fresh", "b1", "ollama")
    m.note_op_finished("fresh", "b1")
    assert m.eviction_candidates(now=time.time()) == []
    assert [r.model for r in m.eviction_candidates(now=time.time() + 31)] == ["fresh"]


def test_reconcile_discovers_and_corrects_and_drops():
    m = _mgr()
    m.note_op_started("known", "b1", "ollama", vram_estimate_mb=1000)
    m.reconcile("b1", "ollama", [
        {"name": "known", "size_vram": 2048 * 1024 * 1024, "expires_at": "later"},
        {"name": "oob", "size_vram": 512 * 1024 * 1024},
    ])
    snap = {s["model"]: s for s in m.snapshot()}
    assert snap["known"]["vram_mb"] == 2048        # corrected from ps
    assert snap["oob"]["state"] == "idle"          # discovered out-of-band
    m.note_op_finished("known", "b1")
    m.reconcile("b1", "ollama", [])                # backend expired everything
    assert m.snapshot() == []
    assert m.vram_estimate("known", "b1") == 2048  # footprint memory survives removal


def test_reconcile_keeps_active_not_yet_visible():
    m = _mgr()
    m.note_op_started("loading", "b1", "ollama", vram_estimate_mb=1000)
    m.reconcile("b1", "ollama", [])                # ps does not list it yet
    assert m.is_resident("loading", "b1")          # active op keeps it
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/scheduler/test_gpu_residency.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement** per the interface block. `vram_estimate` is backed by a separate `self._footprints: dict[tuple[str, str], int]` that `reconcile` updates and `remove`/drop never clears (it is a size memory, not a ledger; the single VRAM authority stays `VramReservationManager`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/scheduler/test_gpu_residency.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tinyagentos/scheduler/gpu_residency.py tests/scheduler/test_gpu_residency.py
git commit -m "feat(gpu-queue): ResidencyManager with structural never-evict-active (#1864)"
```

**Acceptance criteria:** ACTIVE/IDLE derived purely from op counts (no separate in-flight signal); LRU + grace ordering exact; reconcile handles out-of-band loads, size corrections, backend-side expiry, and the active-but-not-yet-visible race; it never gates admission (no ledger behavior).

---

### Slice B3: Arbiter residency integration: load-on-admit, idle-LRU unload, sweep, events

**Files:**
- Modify: `tinyagentos/scheduler/gpu_arbiter.py` (deny paths in `submit_gpu` :331-342 and `_drain_queue` :562-571; op start/finish hooks; sweep in `_process_queue`), `tinyagentos/app.py` (wiring), `tinyagentos/routes/models.py` (cancel cleanup gains unload-if-registered)
- Test: `tests/test_gpu_arbiter_residency.py` (new)

**Interfaces:**
- Consumes: B1 `unload_model`/`unload_capable`, B2 `ResidencyManager`, A3 `_signal_capacity`, A6 emitter, `LifecycleManager.notify_task_complete` (`lifecycle_manager.py:130-147`).
- Produces:

```python
# GpuArbiter.__init__ gains:
residency: ResidencyManager | None = None,
unload_executor: "Callable[[ResidentModel], Awaitable[bool]] | None" = None,
backend_ps_poll: "Callable[[str], Awaitable[list[dict] | None]] | None" = None,
    # backend_name -> /api/ps models list (None on probe failure); app.py
    # builds it from config.backends + the shared http_client, same call
    # shape as loaded_models (routes/models.py:805)
backend_types: "Callable[[str], str] | None" = None,   # backend_name -> type
notify_backend_idle: "Callable[[str], None] | None" = None,  # lifecycle hook

def residency_snapshot(self) -> list[dict]:   # A9's residents block
async def _make_room(self, shortfall_mb: int) -> list[str]:
    """Unload IDLE LRU models one at a time until at least shortfall_mb
    is confirmed freed (post-unload /api/ps re-poll no longer lists the
    model) or candidates are exhausted. Returns the models unloaded, for
    the gpu_op evictions_triggered payload. Only ever called from the
    admission deny paths; nothing else may unload (spec 3.3)."""
```

Behavior wired in this slice:

1. **Op refcounts:** every submitted op with `model` set calls `residency.note_op_started(model, backend_name, type, vram_estimate)` at submit and `note_op_finished` in `_run_gpu_task`'s finally (also on evict/cancel paths). `resident_models_at_admit` and `evictions_triggered` in the A5 trace payload become real values.
2. **Deny paths:** on a denied `_reserve_and_check` where the host HAS a probe (`total > 0`), compute `shortfall = required - effective_free`, run `_make_room(shortfall)`, retry the reservation once. Fail-open hosts (probe `None`) never reach here because reserve admits everything (`vram_reservation.py:126-136`), so no eviction ever fires there (spec section 4.4).
3. **Sweep:** each `_process_queue` iteration additionally reconciles residency: for each backend name seen in residents or configured GPU backends, `ps = await backend_ps_poll(name)`; skip on `None`; `residency.reconcile(...)`. Guard with a `self._sweep_running` flag so a slow poll never stacks.
4. **Events + lifecycle:** `_make_room` emits `gpu.residency.update` `{model, backend_name, action: "evicted", vram_mb, free_vram_mb}` per unload (A6 emitter, notify=False); load completion emits `action: "loaded"`; op completion calls `notify_backend_idle(backend_name)` so service keep-alive timers keep working (spec section 3.4).
5. **Cancel cleanup:** the A8 route's cleanup closure now also calls the unload executor when the cancelled pull's model shows in `/api/ps` (partial load registered).
6. **Backend TTL ownership (decision 2):** the arbiter, not backend timers, owns LRU. rkllama's `max_minutes_loaded_in_memory` is set generously long where taOS manages the service config (rkllama installer config; confirm the exact key on the Pi during B5); a backend-side expiry is tolerated because the sweep notices it (spec section 3.4). No per-request `keep_alive` injection in this slice.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_gpu_arbiter_residency.py (representative core; fakes are plain closures)
@pytest.mark.asyncio
async def test_deny_triggers_idle_lru_unload_then_admit():
    # probe: 0 free until unload happens, then 4096 free
    # residency: idle "cold" (LRU) + idle "warm"; unload_executor flips the probe
    # submit a 2048 MiB load: asserts unload called for "cold" only, op admits,
    # returned evictions land in the gpu_op payload as ["cold"]
    ...

@pytest.mark.asyncio
async def test_active_model_never_unloaded_under_max_pressure():
    # one resident model with a running inference op, zero free VRAM,
    # a queued load that can never fit: unload_executor must NEVER be
    # called; the load stays queued
    ...

@pytest.mark.asyncio
async def test_unload_only_counted_after_ps_confirms():
    # unload_executor returns True but the fake ps still lists the model:
    # _make_room must not count it freed and must stop (no infinite loop)
    ...

@pytest.mark.asyncio
async def test_failopen_host_never_evicts():
    # probe None (VramReservationManager(probe=lambda: None)): submit loads
    # beyond any capacity; all admit; unload_executor never called
    ...

@pytest.mark.asyncio
async def test_sweep_reconciles_out_of_band_and_expiry():
    # fake ps returns an unknown model then empty; residency_snapshot
    # reflects both transitions within two ticks
    ...

@pytest.mark.asyncio
async def test_op_complete_notifies_lifecycle():
    # notify_backend_idle called with backend_name exactly once per op
    ...
```

Write these as full tests (the fakes are: probe closures over a mutable dict, an unload recorder list, a ps dict keyed by backend). Every assertion named above is load-bearing; none may be skipped.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_gpu_arbiter_residency.py -v`
Expected: FAIL (kwargs unknown).

- [ ] **Step 3: Implement** per the Interfaces block. `_make_room` core:

```python
    async def _make_room(self, shortfall_mb: int) -> list[str]:
        if self._residency is None or self._unload_executor is None:
            return []
        freed: list[str] = []
        remaining = shortfall_mb
        for candidate in self._residency.eviction_candidates():
            if remaining <= 0:
                break
            ok = await self._unload_executor(candidate)
            if not ok:
                continue
            ps = None
            if self._backend_ps_poll is not None:
                ps = await self._backend_ps_poll(candidate.backend_name)
            if ps is not None and any(
                    (m.get("name") or m.get("model")) == candidate.model for m in ps):
                logger.warning("gpu-arbiter: unload of %s not confirmed by /api/ps",
                               candidate.model)
                continue
            self._residency.remove(candidate.model, candidate.backend_name)
            freed.append(candidate.model)
            remaining -= candidate.vram_mb
            await self._emit_residency_event(candidate, action="evicted")
        return freed
```

app.py wires `unload_executor` as a closure over `backend_unload.unload_model`, the shared `http_client`, and a backend-name-to-(url,type) lookup from `config.backends`, skipping non-`unload_capable` types.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_gpu_arbiter_residency.py tests/test_gpu_arbiter_894.py tests/test_gpu_arbiter_toctou.py tests/test_gpu_arbiter_queue_ops.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tinyagentos/scheduler/gpu_arbiter.py tinyagentos/app.py tinyagentos/routes/models.py tests/test_gpu_arbiter_residency.py
git commit -m "feat(gpu-queue): load-on-admit with idle-LRU model eviction (#1864)"
```

**Acceptance criteria:** eviction frees real capacity (confirmed by ps re-poll) before re-reserve; active models structurally untouchable; fail-open hosts unchanged; the existing task-eviction path `evict_lowest_priority` (`gpu_arbiter.py:479-495`) remains for task preemption and is preferred less than model unload on the load path (room-making tries model unload first, spec section 2.4 table); sweep never stacks; unload primitive called from nowhere else in the tree (grep gate in review: `grep -rn "unload_model" tinyagentos/ | grep -v backend_unload | grep -v gpu_arbiter | grep -v app.py` returns nothing).

---

### Slice B4: RK3588 NPU/system-memory probe (locked decision 8)

**Files:**
- Modify: `tinyagentos/system_stats.py` (new functions next to `read_nvidia_vram`), `tinyagentos/vram_reservation.py` (`_probe_vram` :272-292)
- Test: `tests/test_rk3588_probe.py` (new)

**Interfaces:**
- Consumes: `/proc/meminfo`, `/proc/device-tree/compatible` (Linux/Rockchip only).
- Produces:

```python
# tinyagentos/system_stats.py
def is_rk3588_host(compat_path: str = "/proc/device-tree/compatible") -> bool:
    """True when the device-tree compatible string contains 'rk3588'."""

def read_rk3588_memory(meminfo_path: str = "/proc/meminfo",
                       floor_mb: int | None = None) -> tuple[int, int] | None:
    """(used_mb, total_mb) for the RK3588's unified memory. The NPU shares
    system RAM (rkllama accounts the same pool for
    unload_oldest_models_from_memory), so MemTotal/MemAvailable is the
    right pressure signal. used = MemTotal - MemAvailable + floor;
    floor (default 1024, env TAOS_RK3588_MEM_FLOOR_MB) reserves OS +
    controller headroom so eviction fires before the OS starts swapping.
    Returns None off-platform or on read failure (fail-open preserved)."""
```

`VramReservationManager._probe_vram` fallback chain: nvidia first (unchanged); when nvidia returns `None` and `is_rk3588_host()`, use `read_rk3588_memory()` converted to `(free_mb, total_mb)`. Result: on the Pi the probe is real, `reserve` can deny, and B3's eviction fires (the spec's decision 8 point). All other non-NVIDIA hosts keep returning `None` (fail-open unchanged).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_rk3588_probe.py
from tinyagentos.system_stats import is_rk3588_host, read_rk3588_memory

MEMINFO = "MemTotal:       16331088 kB\nMemFree:         1200000 kB\nMemAvailable:   12000000 kB\n"


def test_is_rk3588_host_true(tmp_path):
    p = tmp_path / "compatible"
    p.write_bytes(b"xunlong,orangepi-5-plus\x00rockchip,rk3588\x00")
    assert is_rk3588_host(compat_path=str(p)) is True


def test_is_rk3588_host_false_on_other_or_missing(tmp_path):
    p = tmp_path / "compatible"
    p.write_bytes(b"raspberrypi,5-model-b\x00")
    assert is_rk3588_host(compat_path=str(p)) is False
    assert is_rk3588_host(compat_path=str(tmp_path / "nope")) is False


def test_read_rk3588_memory_math_with_floor(tmp_path):
    p = tmp_path / "meminfo"
    p.write_text(MEMINFO)
    used_mb, total_mb = read_rk3588_memory(meminfo_path=str(p), floor_mb=1024)
    assert total_mb == 16331088 // 1024                       # 15948
    # used = total - available + floor = 15948 - 11718 + 1024
    assert used_mb == (16331088 - 12000000) // 1024 + 1024


def test_read_rk3588_memory_none_on_missing(tmp_path):
    assert read_rk3588_memory(meminfo_path=str(tmp_path / "nope")) is None


def test_probe_chain_prefers_nvidia_then_rk3588(monkeypatch, tmp_path):
    from tinyagentos.vram_reservation import VramReservationManager
    import tinyagentos.vram_reservation as vr
    monkeypatch.setattr("tinyagentos.system_stats.read_nvidia_vram", lambda: None)
    monkeypatch.setattr("tinyagentos.system_stats.is_rk3588_host", lambda **k: True)
    monkeypatch.setattr("tinyagentos.system_stats.read_rk3588_memory",
                        lambda **k: (4000, 16000))
    mgr = VramReservationManager()
    assert mgr._probe_vram() == (12000, 16000)                # free = total - used


def test_probe_chain_non_rk3588_stays_fail_open(monkeypatch):
    from tinyagentos.vram_reservation import VramReservationManager
    monkeypatch.setattr("tinyagentos.system_stats.read_nvidia_vram", lambda: None)
    monkeypatch.setattr("tinyagentos.system_stats.is_rk3588_host", lambda **k: False)
    assert VramReservationManager()._probe_vram() is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_rk3588_probe.py -v`
Expected: FAIL (`ImportError`).

- [ ] **Step 3: Implement** the two functions and extend `_probe_vram`'s existing try-block (import both helpers from `tinyagentos.system_stats` inside the method, mirroring the current lazy import at `vram_reservation.py:283`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_rk3588_probe.py tests/test_vram_reservation.py -v`
Expected: PASS, existing reservation tests unchanged.

- [ ] **Step 5: Commit**

```bash
git add tinyagentos/system_stats.py tinyagentos/vram_reservation.py tests/test_rk3588_probe.py
git commit -m "feat(gpu-queue): RK3588 unified-memory probe so eviction fires on the Pi (#1864)"
```

**Acceptance criteria:** the probe chain is nvidia, then rk3588, then `None`; the floor is env-tunable; non-Rockchip non-NVIDIA hosts keep today's fail-open semantics exactly. **Live-Pi flag:** real `/proc` values sanity-checked during B5 (free figure must roughly match `free -m` MemAvailable minus floor).

---

### Slice B5: Phase B integration tests + hardware verification gate

**Files:**
- Create: `tests/integration/test_gpu_queue_residency.py`
- No production code (fix-forward only if the gate finds bugs).

**Interfaces:** consumes everything B1-B4 produced.

- [ ] **Step 1: Write the integration tests** against a fake ollama backend (an in-test ASGI app served through `httpx.ASGITransport` exposing `/api/pull`, `/api/ps`, `/api/generate` keep_alive unload, `/api/delete`, with mutable in-memory model state):

```python
async def test_two_pulls_exceeding_vram_second_queues_then_admits(...):
    # 6 GiB probe, two 4 GiB pulls: no 503; second reports position 1,
    # admits after first completes and its reservation releases (event path)

async def test_new_pull_evicts_idle_lru_resident(...):
    # resident idle 4 GiB model in fake ps; 4 GiB pull with 2 GiB free:
    # fake backend records the unload request, ps updated, pull proceeds

async def test_cancel_mid_pull_deletes_and_releases(...):
    # DELETE /api/models/downloads/{id} mid-stream: fake records
    # /api/delete, reservation ledger returns to 0, task cancelled

async def test_xid62_ordering_under_concurrent_load_and_evict(...):
    # a second load submitted while _make_room is mid-unload must not be
    # admitted until the unload is ps-confirmed (assert admission order)
```

Write all four as full tests; the scenario comments above are the exact load-bearing assertions each must make.

- [ ] **Step 2-4: Run, implement fixture glue, re-run** until green: `uv run pytest tests/integration/test_gpu_queue_residency.py -v`

- [ ] **Step 5: Commit**

```bash
git add tests/integration/test_gpu_queue_residency.py
git commit -m "test(gpu-queue): residency and eviction integration coverage (#1864)"
```

- [ ] **Step 6: Hardware gate (manual, results recorded in the PR description, no IPs or hostnames pasted):**
  - Pi (RK3588, deployed rkllama fork): confirm `POST /unload_model` body key; run a real pull-under-pressure with `TAOS_GPU_QUEUE=on` and watch `gpu.residency.update`; confirm B4 probe numbers against `free -m`; confirm rkllama TTL config key and set it long.
  - Fedora RTX 3060: two-model eviction under real VRAM pressure; concurrent load during eviction (Xid-62 non-regression: `dmesg` clean); `nvidia-smi` confirms freed VRAM after unload.

**Acceptance criteria:** all integration tests green in CI; both hardware checklists pass and are documented in the PR; any body-key or TTL-key corrections land in this PR (adapter + test only).

---

# Phase C: gateway inference, correlation header, benchmark rollup, queue-position UX

Exit criteria: with `shadow`, all LiteLLM-routed local inference flows through the gateway as an instrumented passthrough with p95 added TTFB under 5 ms over a 48 h soak; with `on`, per-model gating and residency-aware admission are active, the resident fast path stays under 1 ms with zero subprocess spawns, chat and Models app show live positions, and `gpu_op` joins to `llm_call` per-agent.

### Slice C1: GPU gateway, fail-open streaming proxy (locked decision 1)

**Files:**
- Create: `tinyagentos/routes/gpu_gateway.py`
- Modify: `tinyagentos/routes/__init__.py` (register)
- Test: `tests/test_gpu_gateway.py` (new)

**Interfaces:**
- Consumes: A1 mode, A2 `submit_gpu(op="inference", ...)`, A5 recorder via arbiter, local-token bearer auth (accepted by the middleware, `auth_middleware.py:284-310`), shared `app.state.http_client`, backend list from `app.state.config.backends`.
- Produces: route `api_route("/gpu/{backend_name}/{path:path}", methods=["GET", "POST", "DELETE"])` with behavior:

```
NO_MODEL_PATHS = {"api/tags", "api/ps", "health", "api/version", "v1/models"}
MODEL_PATHS    = {"api/chat", "api/generate", "v1/chat/completions",
                  "api/embed", "api/pull"}      # api/pull -> op="load"
```

1. **Auth:** reject without a valid local-token bearer (401). The gateway is an internal seam for LiteLLM and controller-internal callers only; it is never cookie-exempt.
2. **Resolve** `backend_name` to `(url, type)` from `config.backends`; unknown gives 404.
3. **No-model paths:** stream straight through, zero queue interaction (spec section 2.2).
4. **Model paths:** read the JSON body once, extract `model`; then by mode:
   - `off`/`shadow`: forward immediately (passthrough); in `shadow` also record a `gpu_op` trace (`outcome: "ok"`, `wait_ms: 0`, payload extra `"shadow": true`, `duration_ms` = backend round-trip, and payload extra `"gateway_overhead_ms"` measured from request receipt to backend-connect) so the soak has real overhead numbers.
   - `on`: wrap the proxied call as the payload of `submit_gpu(task, required_vram_mb=<residency vram_estimate for non-resident models, else 0>, op="load" if path is api/pull else "inference", model=model, backend_name=backend_name)` with `priority=Priority.INTERACTIVE_AGENT` (C4 upgrades attributed user chats to `INTERACTIVE_USER`).
5. **FAIL-OPEN (permanent, decision 1):** passthrough whenever (a) `app.state.gpu_arbiter is None` (`app.py:1194-1196`), (b) any exception escapes admission, or (c) admission bookkeeping exceeds `TAOS_GPU_GATE_BUDGET_MS` (default 100). Genuine queue waiting in `on` mode is bounded by `TAOS_GPU_GATE_MAX_WAIT_S` (default 120): on expiry, cancel the op and passthrough, tracing `outcome: "error"` with payload extra `"failopen": "timeout"`. Fail-open events log at warning and trace; they never 500.
6. **Streaming:** `client.build_request(...)` + `client.send(request, stream=True)` relayed through a `StreamingResponse` with `X-Accel-Buffering: no` (same discipline as `routes/event_stream.py:104-112`); status and headers propagated minus hop-by-hop headers (`connection, keep-alive, transfer-encoding, content-length, content-encoding` recomputed by starlette); no body buffering in either direction.

- [ ] **Step 1: Write the failing tests** (fake backend = in-test ASGI app behind `httpx.ASGITransport`, injected as `app.state.http_client`)

```python
# tests/test_gpu_gateway.py (write all of these in full)
async def test_gateway_streams_chunked_body_byte_for_byte(...):
    # fake backend emits 5 distinct SSE-style chunks; client sees the exact
    # byte sequence, and receives chunk 1 before the backend has sent chunk 5
    # (true streaming, asserted with an event-gated backend generator)

async def test_gateway_no_model_paths_skip_queue(...):
    # GET /gpu/local-ollama/api/tags: arbiter mock's submit_gpu never called

async def test_gateway_fail_open_when_arbiter_none(...):
    # mode on, app.state.gpu_arbiter = None: request succeeds, backend hit

async def test_gateway_fail_open_on_admission_exception(...):
    # arbiter.submit_gpu raises RuntimeError: request still succeeds (passthrough),
    # a warning is logged, response status is the backend's 200

async def test_gateway_fail_open_on_wait_timeout(...):
    # arbiter future never resolves; TAOS_GPU_GATE_MAX_WAIT_S=0.1: passthrough

async def test_gateway_rejects_without_local_token(...):
    # no bearer: 401; wrong bearer: 401

async def test_gateway_unknown_backend_404(...)

async def test_gateway_shadow_records_overhead_trace(...):
    # mode shadow: recorder captured payload["shadow"] is True and
    # payload["gateway_overhead_ms"] >= 0

async def test_gateway_pull_path_is_load_op(...):
    # POST /gpu/local-ollama/api/pull in mode on: submit_gpu called with op="load"
```

- [ ] **Step 2: Run to verify failure:** `uv run pytest tests/test_gpu_gateway.py -v` (404: route absent).

- [ ] **Step 3: Implement** per the numbered behavior. The handler is deliberately dumb: parse, admit, stream; every branch that is not plain forwarding is wrapped so its failure degrades to forwarding.

- [ ] **Step 4: Run to verify pass:** `uv run pytest tests/test_gpu_gateway.py -v`

- [ ] **Step 5: Commit**

```bash
git add tinyagentos/routes/gpu_gateway.py tinyagentos/routes/__init__.py tests/test_gpu_gateway.py
git commit -m "feat(gpu-queue): fail-open streaming GPU gateway route (#1864)"
```

**Acceptance criteria:** byte-for-byte streaming proven with an ordering assertion, not just content equality; every fail-open branch tested; no code path returns 500 for an admission problem; no-model paths measurably skip the arbiter.

---

### Slice C2: LiteLLM api_base rewrite behind the flag (traffic switch)

**Files:**
- Modify: `tinyagentos/providers/__init__.py` (new set), `tinyagentos/litellm_config.py` (`generate_litellm_config` :211, api_base branch :259-261, embedding entries likewise), `tinyagentos/llm_proxy.py` (`write_config` :159 passes gateway kwargs)
- Test: `tests/test_litellm_config.py` (extend), `tests/test_llm_proxy_local_models.py` (extend)

**Interfaces:**
- Consumes: A1 mode, C1 route shape, local token file convention (`data/.auth_local_token`, `litellm_callback.py:30-47`).
- Produces:

```python
# providers/__init__.py
GPU_GATEWAY_TYPES: set[str] = {"ollama", "rkllama", "llama-cpp", "vllm", "hailo-ollama"}
# spec 2.2: local GPU LLM backends; image-gen types are a later rollout (spec non-goal)

# litellm_config.py
def generate_litellm_config(
    backends: list[dict], ...existing params...,
    gateway_base_url: str | None = None,     # "http://127.0.0.1:6969"
    gateway_token: str | None = None,
) -> dict:
```

Rewrite rule inside the api_base branch: when `gateway_base_url` is set AND `backend_type in GPU_GATEWAY_TYPES` AND the backend URL host is local (`127.0.0.1`, `localhost`, `::1`; helper `_is_local_url(url)`), then `api_base = f"{gateway_base_url}/gpu/{backend['name']}"` and `litellm_params["extra_headers"] = {"Authorization": f"Bearer {gateway_token}"}`. Cloud providers, image-gen types, and remote-worker URLs are NEVER rewritten. `LLMProxy.write_config` passes the kwargs only when `gpu_queue_mode() != "off"` (shadow and on both route through the gateway; in shadow the gateway is a pure instrumented passthrough, which is exactly the soak). Rollback is flip env + `reload_config` (`llm_proxy.py:532`, full stop+start, already reliable).

- [ ] **Step 1: Write the failing tests**

```python
# additions to tests/test_litellm_config.py
def test_local_gpu_backend_rewritten_to_gateway():
    cfg = generate_litellm_config(
        [{"name": "local-ollama", "type": "ollama",
          "url": "http://127.0.0.1:11434", "model": "qwen2.5:7b"}],
        gateway_base_url="http://127.0.0.1:6969", gateway_token="tok")
    entry = _entry(cfg, "default")
    assert entry["litellm_params"]["api_base"] == "http://127.0.0.1:6969/gpu/local-ollama"
    assert entry["litellm_params"]["extra_headers"] == {"Authorization": "Bearer tok"}

def test_cloud_backend_never_rewritten(): ...
    # openrouter entry keeps its url, no extra_headers injected

def test_remote_worker_url_never_rewritten():
    # type ollama but url http://192.0.2.10:11434 (TEST-NET): api_base unchanged

def test_no_gateway_kwargs_config_identical_to_today():
    # byte-equal dict with and without the feature present when kwargs omitted

def test_embedding_entries_also_rewritten():
    # embedding model_list entries for a local ollama backend point at the gateway
```

- [ ] **Step 2: Run to verify failure:** `uv run pytest tests/test_litellm_config.py -v -k gateway` (TypeError on kwargs).

- [ ] **Step 3: Implement** `_is_local_url`, the set, the rewrite, and the `write_config` plumbing (reads the token via `AuthManager.local_token_path()` contents from `app`-supplied value; `LLMProxy` already receives what it needs at construction, extend its `__init__` with `gateway_token: str | None = None` set from app.py).

- [ ] **Step 4: Run to verify pass:** `uv run pytest tests/test_litellm_config.py tests/test_llm_proxy_local_models.py tests/test_litellm_migrate.py -v`

- [ ] **Step 5: Commit**

```bash
git add tinyagentos/providers/__init__.py tinyagentos/litellm_config.py tinyagentos/llm_proxy.py tinyagentos/app.py tests/test_litellm_config.py tests/test_llm_proxy_local_models.py
git commit -m "feat(gpu-queue): route local GPU backends through the gateway in LiteLLM config (#1864)"
```

**Acceptance criteria:** mode `off` produces today's config byte-for-byte; only local GPU LLM backends rewrite; token never logged; catalog-change reload (`app.py:1120-1138`) regenerates correctly. **Soak note (risk register):** after this slice merges, run `shadow` for 48 h on Fedora + Pi before C3's gating is ever enabled; overhead numbers come from C1's shadow traces.

---

### Slice C3: Per-model concurrency + resident fast path + load-dependency admission (hot path)

**Files:**
- Modify: `tinyagentos/scheduler/gpu_arbiter.py`
- Test: `tests/test_gpu_arbiter_fastpath.py` (new), `tests/test_gpu_arbiter_inference.py` (new)

**Interfaces:**
- Consumes: A2/A3 queue machinery, B2 `residency.is_resident`/`vram_estimate`, B3 `_make_room`.
- Produces:

```python
# GpuArbiter.__init__ gains:
per_model_limit: "Callable[[str, str], int] | None" = None,
    # (backend_name, model) -> max concurrent inference ops; default 1;
    # app.py reads optional backend config key "max_concurrent_per_model"
```

Inference admission in `submit_gpu` (`op == "inference"`, before the VRAM block):

1. Resident + semaphore acquirable without waiting: run inline. No lock, no probe, no drain tick (`required_vram_mb` forced to 0 on this branch; the zero-VRAM reserve path stays lock-free, `vram_reservation.py:109-115`).
2. Resident + slots busy: enqueue (per-model position, A2); a slot release calls `_signal_capacity()` so admission is immediate (A3), FIFO within priority.
3. Non-resident: `required_vram_mb = residency.vram_estimate(model, backend_name)` (0 when unknown or fail-open, which admits and lets the backend load implicitly); the normal reserve/deny/make-room path (B3) runs; the queue position covers the combined load + run wait (spec section 2.4).
4. Loads stay globally serialized per backend: a `dict[str, asyncio.Lock]` keyed by backend_name wraps load-op execution, preserving one-load-at-a-time (spec section 4.1) on top of the one-admission-per-drain-tick discipline.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_gpu_arbiter_fastpath.py
@pytest.mark.asyncio
async def test_fastpath_under_1ms_and_zero_probe_calls():
    probe_calls = []

    def probe():
        probe_calls.append(1)
        return (8192, 16384)

    arbiter = GpuArbiter(vram_reservation=VramReservationManager(probe=probe),
                         residency=residency_with_resident("m", "b1"))
    async def payload(_res):
        return "ok"
    durations = []
    for _ in range(100):
        t = _inference_task(payload)
        start = time.perf_counter()
        await arbiter.submit_gpu(t, op="inference", model="m", backend_name="b1")
        durations.append(time.perf_counter() - start)
    assert sorted(durations)[50] < 0.001      # p50 under 1 ms added latency
    assert probe_calls == []                  # zero probe/subprocess on the fast path
```

```python
# tests/test_gpu_arbiter_inference.py (write in full)
async def test_per_model_serialization_default_1(...):
    # two concurrent ops on model m: second gets position 1, runs after first;
    # completion order asserted

async def test_different_models_run_concurrently(...):
    # ops on m1 and m2 overlap in time (asserted with entry/exit timestamps)

async def test_slot_release_wakes_queued_immediately(...):
    # drain_tick_seconds=60; second same-model op admits < 0.5 s after first ends

async def test_per_model_limit_override(...):
    # per_model_limit returning 2: two ops overlap, third queues

async def test_nonresident_inference_uses_estimate_and_makes_room(...):
    # residency knows m needs 4096; free 2048 with an idle victim:
    # unload fires, then the op admits

async def test_loads_serialized_per_backend(...):
    # two load ops same backend never overlap; different backends may
```

- [ ] **Step 2: Run to verify failure:** `uv run pytest tests/test_gpu_arbiter_fastpath.py tests/test_gpu_arbiter_inference.py -v`

- [ ] **Step 3: Implement.** Semaphores live in `dict[tuple[str, str], asyncio.Semaphore]` created lazily per `(backend_name, model)`; "acquirable without waiting" is `sem._value > 0` guarded acquire via `sem.locked()` check plus immediate `acquire()` on the inline branch; release in the op's finally also calls `_signal_capacity()`.

- [ ] **Step 4: Run to verify pass:** full arbiter matrix: `uv run pytest tests/test_gpu_arbiter_fastpath.py tests/test_gpu_arbiter_inference.py tests/test_gpu_arbiter_894.py tests/test_gpu_arbiter_toctou.py tests/test_gpu_arbiter_queue_ops.py tests/test_gpu_arbiter_residency.py -v`

- [ ] **Step 5: Commit**

```bash
git add tinyagentos/scheduler/gpu_arbiter.py tests/test_gpu_arbiter_fastpath.py tests/test_gpu_arbiter_inference.py
git commit -m "feat(gpu-queue): per-model concurrency with sub-ms resident fast path (#1864)"
```

**Acceptance criteria (risk register: hot path):** the perf test is a hard CI gate (p50 under 1 ms, zero probe calls); the contended path is event-driven, never tick-bound; multi-model residency runs concurrently; per-backend load serialization provable by overlap assertions.

---

### Slice C4: Correlation header through LiteLLM (locked decision 4)

**Files:**
- Modify: `tinyagentos/litellm_config.py` (general_settings), `tinyagentos/routes/gpu_gateway.py` (submitter parse + response header), `tinyagentos/litellm_callback.py` (join field)
- Test: `tests/test_gpu_gateway.py` (extend), `tests/test_litellm_callback.py` (extend), `tests/test_litellm_config.py` (extend)

- [ ] **Step 1 (MANDATORY, before any implementation): live verification against the pinned LiteLLM** (`litellm[proxy]>=1.92.0`). Spin the real proxy locally against a header-echo mock backend (tiny FastAPI app that returns received request headers in its response body and sets a marker response header). Verify:
  - (a) with `general_settings: {add_user_information_to_llm_headers: true}` in the generated config, the backend receives `x-litellm-user-api-key-alias: taos-<slug>` for a request made with a deployer-minted key;
  - (b) in a `CustomLogger.async_log_success_event`, `response_obj._hidden_params["additional_headers"]` exposes the backend's response headers.
  Record both outcomes (LiteLLM version + yes/no) in the PR description.

**Primary design (both legs verified):**
- Request leg (per-agent attribution + priority): C2's config gains `general_settings.add_user_information_to_llm_headers: true`. The gateway parses `x-litellm-user-api-key-alias` with the same `taos-` prefix rule as `_slug_from_alias` (`litellm_callback.py:64-70`) into `task.submitter`; attributed user-chat inference is submitted at `Priority.INTERACTIVE_USER`, agent-initiated stays `INTERACTIVE_AGENT`. Internal callers may send `X-Taos-Submitter` instead. Unattributed requests degrade to model-level position (spec section 5.6).
- Join leg: the gateway sets response header `X-Taos-Gpu-Op: <gpu_op trace envelope id>` on gated requests; `litellm_callback.async_log_success_event` reads it from `additional_headers` and adds `payload["gpu_op_id"]` to the `llm_call` trace. `gpu_op` and `llm_call` now join on that id.

**Fallback designs (only for a leg that fails verification, chosen leg-by-leg, documented in the PR):**
- Request leg fallback: keep C2's static per-model `extra_headers` and add `"X-Taos-Backend": <backend_name>`; attribution stays model-level (the spec's accepted degradation) and the submitter enrichment is dropped, NOT approximated.
- Join leg fallback: the callback records nothing new; joining falls back to `(model, time window)` as documented in spec section 6.1, and `payload["gpu_op_id"]` is simply absent. C5's rollup keys on `gpu_op` traces alone, so it is unaffected either way.

- [ ] **Step 2: Write the failing tests**

```python
# gateway additions (write all in full; comments are the exact assertions)
async def test_gateway_submitter_from_litellm_alias_header(...):
    # request carries x-litellm-user-api-key-alias: taos-scout;
    # submit_gpu called with task.submitter == "scout" and INTERACTIVE_USER

async def test_gateway_sets_gpu_op_response_header(...):
    # gated response includes X-Taos-Gpu-Op matching the recorded trace id

# callback additions (tests/test_litellm_callback.py)
async def test_callback_records_gpu_op_id_from_additional_headers(...):
    # fake response_obj._hidden_params = {"additional_headers":
    #   {"x-taos-gpu-op": "abc123"}}: posted llm_call payload has gpu_op_id "abc123"

# config additions
def test_general_settings_forward_user_info_present_with_gateway(...):
```

- [ ] **Step 3: Run to verify failure, implement the verified legs, run to verify pass:**
`uv run pytest tests/test_gpu_gateway.py tests/test_litellm_callback.py tests/test_litellm_config.py -v`

- [ ] **Step 4: Live re-check:** one real completion through LiteLLM + gateway + fake backend; confirm the `llm_call` trace row and the `gpu_op` trace row share the id (query via `GET /api/agents/{slug}/trace`).

- [ ] **Step 5: Commit**

```bash
git add tinyagentos/litellm_config.py tinyagentos/routes/gpu_gateway.py tinyagentos/litellm_callback.py tests/test_gpu_gateway.py tests/test_litellm_callback.py tests/test_litellm_config.py
git commit -m "feat(gpu-queue): per-agent correlation between gpu_op and llm_call traces (#1864)"
```

**Acceptance criteria (risk register):** verification evidence precedes code; per-agent queue position works for LiteLLM-routed chats when leg (a) holds; trace join demonstrated live; degradations are the spec's documented ones, never silent approximations.

---

### Slice C5: Trace-to-BenchmarkStore rollup aggregator (locked decision 5)

**Files:**
- Create: `tinyagentos/benchmark/gpu_rollup.py`
- Modify: `tinyagentos/routes/benchmarks.py` (trigger route), `tinyagentos/app.py` (periodic task in lifespan)
- Test: `tests/test_gpu_rollup.py` (new), `tests/test_routes_benchmarks.py` (extend)

**Interfaces:**
- Consumes: A5 `gpu_op` traces via `AgentTraceStore.list(kind="gpu_op", since=..., limit=...)` (`trace_store.py:386-395`, limit capped at 1000), slug enumeration from `data_dir/trace/*` directories (`_agent_trace_dir`, `trace_store.py:122-123`), `BenchmarkStore.record(...)` exact keyword signature (`benchmark/store.py:63-81`).
- Produces:

```python
# tinyagentos/benchmark/gpu_rollup.py
class GpuQueueRollup:
    """Rolls continuous gpu_op trace observations up into BenchmarkStore
    rows (suite_name='gpu-queue-live', worker_id='local') so the existing
    leaderboard read paths serve queue health (spec 6.2, decision 5).
    Per-op rows never land in BenchmarkStore; only aggregates do."""

    def __init__(self, trace_registry: "TraceStoreRegistry",
                 benchmark_store: "BenchmarkStore", data_dir: Path) -> None: ...

    async def run_once(self, since: float | None = None) -> int:
        """Aggregate gpu_op events since the watermark (or *since*), write
        one row per (model, backend_name, metric), advance the watermark
        (data_dir/gpu_rollup_state.json), return rows written."""

    async def run_periodic(self, interval_seconds: float = 86400.0) -> None:
        """Daily loop; started from the app lifespan, cancelled on shutdown."""
```

Metrics per (model, backend_name) over the window: `gpu.wait_ms.p50`, `gpu.wait_ms.p95` (unit `ms`, from payload `wait_ms` of inference + load ops), `gpu.load_ms.p50` (unit `ms`, from `duration_ms` of `op=="load"` with `outcome=="ok"`), `gpu.ops_per_hour` (unit `ops/h`, count scaled by window hours). `capability="llm-chat"`, `platform=None`, `worker_name="local"`, `status="ok"`, `first_join=False`, `details={"window_start": ..., "window_end": ..., "op_count": ...}`. Route: `POST /api/benchmarks/gpu-rollup` returns `{"rows_written": n}` (manual trigger, matching the run-once-then-manual benchmark policy for anything beyond the daily tick).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_gpu_rollup.py (seed real AgentTraceStores in tmp_path; write all in full)
async def test_rollup_aggregates_wait_percentiles(tmp_path):
    # seed _system_ store with 20 gpu_op events, wait_ms 0..190
    rollup = GpuQueueRollup(registry, store, tmp_path)
    n = await rollup.run_once(since=0.0)
    rows = await store.history_by_worker("local")
    p95 = next(r for r in rows if r["metric"] == "gpu.wait_ms.p95")
    assert p95["suite_name"] == "gpu-queue-live"
    assert 170 <= p95["value"] <= 190

async def test_rollup_watermark_prevents_double_count(tmp_path):
    n1 = await rollup.run_once(since=0.0)
    n2 = await rollup.run_once()          # nothing new
    assert n1 > 0 and n2 == 0

async def test_rollup_groups_by_model_and_backend(tmp_path):
    # two models: separate rows per metric per model

async def test_rollup_skips_non_ok_loads_for_load_ms(tmp_path):
    # cancelled/evicted loads excluded from gpu.load_ms.p50

# tests/test_routes_benchmarks.py addition
async def test_gpu_rollup_trigger_route(...):
    resp = await client.post("/api/benchmarks/gpu-rollup")
    assert "rows_written" in resp.json()
```

- [ ] **Step 2: Run to verify failure:** `uv run pytest tests/test_gpu_rollup.py -v` (ModuleNotFoundError).

- [ ] **Step 3: Implement** (slug listing via `sorted(p.name for p in (data_dir / "trace").iterdir() if p.is_dir())`; pagination via repeated `list(kind="gpu_op", since=watermark, until=cursor, limit=1000)`; percentile = sorted index math, no numpy). Wire the lifespan task next to the score cache start (`app.py:1142-1145`) with the same try/except-log posture.

- [ ] **Step 4: Run to verify pass:** `uv run pytest tests/test_gpu_rollup.py tests/test_routes_benchmarks.py tests/test_benchmark_store.py -v`

- [ ] **Step 5: Commit**

```bash
git add tinyagentos/benchmark/gpu_rollup.py tinyagentos/routes/benchmarks.py tinyagentos/app.py tests/test_gpu_rollup.py tests/test_routes_benchmarks.py
git commit -m "feat(gpu-queue): daily gpu_op trace rollup into BenchmarkStore (#1864)"
```

**Acceptance criteria:** aggregates only (no per-op flooding of the suite table, spec 6.2); watermark exact across restarts; rows readable through the existing leaderboard endpoints (`routes/benchmarks.py:108-126`); rollup failure can never affect queue operation (isolated task, logged).

---

### Slice C6: Queue-position UX (SSE live positions + chat waiting state)

**Files:**
- Create: `desktop/src/stores/gpu-queue-store.ts`
- Modify: `desktop/src/hooks/use-event-stream.ts` (dispatch table :12-24), `desktop/src/apps/ModelsApp.tsx` (SSE nudge), `desktop/src/apps/MessagesApp.stallWatch.ts`, `desktop/src/apps/MessagesApp.tsx` (pending indicator, :200 state union and the "..." indicator)
- Test: `desktop/src/stores/gpu-queue-store.test.ts` (new), `desktop/src/apps/MessagesApp.stallWatch.test.ts` (extend), `desktop/src/apps/ModelsApp.download.test.tsx` (extend)

**Interfaces:**
- Consumes: A6 event payloads; C4 submitter attribution (slug); the single-SSE-connection dispatch pattern.
- Produces:

```ts
// desktop/src/stores/gpu-queue-store.ts (zustand, mirroring notification-store)
export interface GpuQueueEntry {
  taskId: string; op: "load" | "inference"; model: string;
  submitter: string; position: number;
}
interface GpuQueueState {
  entries: GpuQueueEntry[]; queueDepth: number;
  applyUpdate: (payload: { entries: GpuQueueEntry[]; queue_depth: number }) => void;
  positionFor: (submitter: string) => number | null;   // lowest position among
                                                       // inference entries for slug
}
export const useGpuQueueStore: ...;
```

- Dispatch table entries: `"gpu.queue.update"` writes the store; `"gpu.residency.update"` is a one-line passthrough to the store's residency list (displayed later; stored now).
- MessagesApp: while a reply is `state: "pending"`, `positionFor(agentSlug)` non-null renders `Waiting, position N` in the pending indicator; clears when position drops to null/0 or the first delta arrives.
- stallWatch: `computeStallInfo` (`MessagesApp.stallWatch.ts:62`) gains a `queuedPosition: number | null` argument; a queued turn returns no stall hint/warning regardless of elapsed time (queued is a known-good waiting state, spec 5.4).
- ModelsApp: subscribes to the store and refreshes `queuePosition` between polls (SSE nudge, spec 5.4).

- [ ] **Step 1: Write the failing tests**

```ts
// gpu-queue-store.test.ts
it("applyUpdate replaces entries and positionFor picks the lowest inference position", () => {
  const s = useGpuQueueStore.getState();
  s.applyUpdate({ queue_depth: 3, entries: [
    { taskId: "1", op: "inference", model: "m", submitter: "scout", position: 2 },
    { taskId: "2", op: "inference", model: "m", submitter: "scout", position: 4 },
    { taskId: "3", op: "load", model: "x", submitter: "scout", position: 1 },
  ]});
  expect(useGpuQueueStore.getState().positionFor("scout")).toBe(2); // loads excluded
  expect(useGpuQueueStore.getState().positionFor("other")).toBeNull();
});

// MessagesApp.stallWatch.test.ts additions
it("queued turn never trips stall hint or warning", () => {
  const info = computeStallInfo({ elapsedMs: STALL_WARN_MS + 1, queuedPosition: 3, ... });
  expect(info.level).toBe("none");
});
it("dequeued turn resumes normal stall accounting", () => { ... });
```

- [ ] **Step 2: Run to verify failure:** `cd desktop && npm test -- src/stores/gpu-queue-store.test.ts src/apps/MessagesApp.stallWatch.test.ts`

- [ ] **Step 3: Implement** store, dispatch entries, indicator, stallWatch argument (update all existing `computeStallInfo` call sites in `MessagesApp.tsx`).

- [ ] **Step 4: Run to verify pass:** `cd desktop && npm test` (full desktop suite; the stallWatch signature change must not break existing tests beyond deliberate updates).

- [ ] **Step 5: Commit**

```bash
git add desktop/src/stores/gpu-queue-store.ts desktop/src/hooks/use-event-stream.ts desktop/src/apps/MessagesApp.tsx desktop/src/apps/MessagesApp.stallWatch.ts desktop/src/apps/ModelsApp.tsx desktop/src/stores/gpu-queue-store.test.ts desktop/src/apps/MessagesApp.stallWatch.test.ts desktop/src/apps/ModelsApp.download.test.tsx
git commit -m "feat(desktop): live GPU queue positions in chat and Models app (#1864)"
```

**Acceptance criteria:** copy says position, never an ETA (spec 8.6); queued turns never trigger the stall warning; position display degrades to model-level when attribution is absent (C4 fallback); ARIA labels on the new indicator. Per the self-verify policy, boot the real desktop with a contended fake backend and screenshot the chat waiting state and the Models position updating live before calling the slice done.

---

### Slice C7: Soak gate, latency budget, existing-DB upgrade test, rollback drill

**Files:**
- Create: `tests/integration/test_gpu_gateway_e2e.py`, `tests/integration/test_gpu_queue_upgrade.py`
- No production code (fix-forward only).

- [ ] **Step 1: Write the e2e tests** (full app via `ASGITransport`, fake streaming backend; write all in full, the comments are the exact assertions):

```python
async def test_litellm_shaped_request_streams_through_gateway(...):
    # POST /gpu/local-ollama/v1/chat/completions with stream chunks;
    # byte-for-byte, mode on, gated, gpu_op + position events observed

async def test_gateway_added_ttfb_under_ci_guard(...):
    # 50 direct vs 50 gateway requests against the same fake backend;
    # median added TTFB < 25 ms (CI guard; the 5 ms product budget is
    # asserted on hardware in the soak, spec 8.5)

async def test_rollback_flag_flip_restores_direct_api_base(...):
    # generate config in shadow, then off; assert api_base returns to the
    # backend URL and reload_config path executes cleanly
```

```python
# tests/integration/test_gpu_queue_upgrade.py (existing-DB upgrade policy)
async def test_boot_over_pre_change_data_dir_off_and_shadow(...):
    # fixture: a data dir + config.json shaped like a pre-#1864 install
    # (local ollama backend, agents, litellm config file present);
    # boot the app with TAOS_GPU_QUEUE unset, then "shadow": startup clean,
    # /api/models routes respond, regenerated LiteLLM config valid in both,
    # no schema migrations attempted (feature adds none)
```

- [ ] **Step 2-4: Run, glue, re-run:** `uv run pytest tests/integration/test_gpu_gateway_e2e.py tests/integration/test_gpu_queue_upgrade.py -v`

- [ ] **Step 5: Commit**

```bash
git add tests/integration/test_gpu_gateway_e2e.py tests/integration/test_gpu_queue_upgrade.py
git commit -m "test(gpu-queue): gateway e2e, latency guard, upgrade and rollback coverage (#1864)"
```

- [ ] **Step 6: Soak gate (manual, the Phase C ship decision, evidence in the PR):**
  - 48 h `TAOS_GPU_QUEUE=shadow` on Fedora RTX 3060 and the Pi with real agent traffic; pull `gateway_overhead_ms` p95 from shadow `gpu_op` traces; ship gate is p95 under 5 ms and zero unexplained fail-open traces.
  - Flip to `on` on the Fedora box first; 24 h with per-model gating; verify queue positions appear under contention and uncontended chat latency is unchanged (compare `llm_call` `duration_ms` distributions before/after).
  - Rollback drill executed once for real: flip env to `off`, `POST /api/settings/update` deploy path, confirm direct api_base restored and agents completing.

**Acceptance criteria:** all three gates documented with numbers; only after they pass does `on` become the recommended default in release notes (the flag itself ships defaulting to `off` in every phase).

---

## Self-Review (performed against the spec before saving)

**Spec coverage map (section -> slice):** 2.2 gateway -> C1/C2; 2.4 op shape -> A2; 2.5 ledger discipline -> constraints + A2/B2 (footprints are not a ledger); 3.1 ResidencyManager -> B2; 3.2 load-on-admit/idle-LRU/never-evict-active -> B3; 3.3 unload primitive incl. rkllama `/unload_model` -> B1; 3.4 lifecycle composition -> B3 (notify hook, TTL note); 4.1 per-model concurrency + per-backend load serialization -> C3; 4.2 fast path -> C3; 4.3 contended paths + 503-means-overflow -> A7/C3; 4.4 fail-open hosts -> B3 test + B4 (RK3588 exits fail-open per decision 8); 5.1 position -> A2; 5.2 API -> A9; 5.3 SSE + notify suppression + coalescing -> A6; 5.4 UI states -> A10/C6; 5.5 cancellation -> A2/A8 (+B3 unload-if-registered); 5.6 attribution -> C4 (with the spec's degradation); 6.1 gpu_op envelope -> A1/A5, join -> C4; 6.2 rollup -> C5; 7.1 contract table -> A7/A8/C2/B3; 7.3 test churn -> A7 note (mode `off` default keeps old tests green; new-mode tests added instead of flipped); 7.4 phases/flag/rollback -> flag A1, rollback C7; 8.1 -> C1 fail-open + C7 soak; 8.2 grace period -> B2; 8.3 aging -> A4; 8.4 cancel ordering -> A8; 8.5 budgets -> C3 perf test + C7; 8.6 honesty copy -> C6; 8.7 backend restart -> B2 reconcile tests; 9.1-9.9 testing strategy -> distributed across slice test lists (9.9 hardware -> B5/C7); 10a decisions 1-8 -> C1/C2, B1/B3, A8, C4, C5, A7, A3, B4 respectively.
**Gaps found and fixed inline:** ollama pull route lacked a download-id shape in the first draft (added `ollama-pull-{model_name}`); `queue_snapshot` key backward compatibility called out in A2; `test_routes_models.py` flip vs. keep resolved (keep, because default `off`); stallWatch signature change propagated to call sites in C6 step 3.
**Placeholder scan:** the remaining `...` occurrences are inside test skeletons that name their exact assertions in adjacent comments and are explicitly marked "write in full"; no TBD/TODO/"appropriate handling" phrases; every produced interface has a concrete signature.
**Type consistency check:** `gpu_queue_mode()` (A1) used in A7/C1/C2; `submit_gpu(op, model, backend_name)` (A2) used in A7/C1/C3; `cancel_op` (A2) used in A8/A9; `queue_position` (A2) used in A7/A9; `start_queued_installer` (A7) matches A8's `gpu_task_id`; `unload_model(client, backend_type=, base_url=, model=)` (B1) matches B3's executor closure; `ResidencyManager` method names (B2) match B3/C3 call sites (`note_op_started`, `note_op_finished`, `eviction_candidates`, `reconcile`, `remove`, `vram_estimate`, `snapshot`); `residency_snapshot` (B3) matches A9's `getattr` probe; `GpuQueueRollup.run_once` (C5) matches the trigger route; `positionFor` (C6) matches the MessagesApp consumer. Consistent.







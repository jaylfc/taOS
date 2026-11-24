"""Tests for per-class TTL idle eviction in the core-aware scheduler (#62).

RED-FIRST: written against issue #62's TTL table. On dev before the fix
these fail because CoreAwareModelScheduler has no TTL table, no idle
sweep, and neither BackendEntry nor the manifest auto-register path
carries a ttl_seconds override.

Covered:
  - the TTL table matches issue #62's defaults exactly
  - model classification (backend type + model-name heuristics)
  - a resident idle past its class TTL is evicted via the unload hook
  - a resident inside its TTL survives
  - pinned / always_resident residents are never idle-evicted
  - in-use residents are never idle-evicted
  - chat size classes: <=4B evicts at 10 min, >=7B not until 30 min
  - a catalog ttl override beats the class default (both directions)
  - a failed unload keeps the resident (the backend still holds it)
  - unclassified residents are never idle-evicted
  - the sweep loop ticks on its interval
  - BackendEntry carries ttl_seconds from the backend config dict
  - manifest lifecycle.ttl_seconds flows into the config backend entry
"""
from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path

import pytest

from tinyagentos.config import AppConfig, auto_register_from_manifest
from tinyagentos.scheduler.backend_catalog import BackendCatalog, _ttl_seconds_from
from tinyagentos.scheduler.core_aware_scheduler import CoreAwareModelScheduler
from tinyagentos.scheduler.loaded_model import (
    DEFAULT_MODEL_TTLS,
    LoadedModel,
    ModelClass,
    PriorityClass,
    classify_model,
    parse_ttl_seconds,
)
from tinyagentos.scheduler.resource_shape import make_cpu_shape

T0 = 1_000_000.0  # arbitrary fixed epoch for the injected clock

_BACKEND_TYPES = {
    "rkllama": "rkllama",
    "ollama": "ollama",
    "sd": "sd-cpp",
    "whisper-srv": "whisper",
    "paint": "iopaint",
}


def _type_lookup(name: str) -> str:
    return _BACKEND_TYPES.get(name, "")


class _FakeUnload:
    """Records unload calls; returns a configurable result."""

    def __init__(self, result: bool = True):
        self.result = result
        self.calls: list[LoadedModel] = []

    async def __call__(self, model: LoadedModel) -> bool:
        self.calls.append(model)
        return self.result


def _scheduler(unload=None, ttl_resolver=None, now: float = T0):
    clock = {"now": now}
    sched = CoreAwareModelScheduler(
        shape_lookup=lambda _t: make_cpu_shape(),
        idle_unload_fn=unload,
        ttl_resolver=ttl_resolver,
        backend_type_lookup=_type_lookup,
        clock=lambda: clock["now"],
    )
    return sched, clock


def _resident(model_id: str, backend: str, *, idle_for: float = 0.0,
              now: float = T0, **kw) -> LoadedModel:
    m = LoadedModel(model_id=model_id, backend=backend, memory_mb_used=128, **kw)
    m.loaded_at = now - idle_for
    m.last_used_at = now - idle_for
    return m


# ---------------------------------------------------------------------------
# The TTL table and classification
# ---------------------------------------------------------------------------

def test_ttl_table_matches_issue_62_defaults():
    """Issue #62's table, verbatim (seconds)."""
    assert DEFAULT_MODEL_TTLS == {
        ModelClass.IMAGE_GENERATION: 300.0,   # Image gen (SD/Flux/etc.) 5 min
        ModelClass.SPEECH_TO_TEXT: 120.0,     # STT / TTS 2 min
        ModelClass.TEXT_TO_SPEECH: 120.0,     # STT / TTS 2 min
        ModelClass.EMBEDDING: 30.0,           # Embedding 30 sec
        ModelClass.RERANKING: 15.0,           # Reranker 15 sec
        ModelClass.QUERY_EXPANSION: 15.0,     # Query expansion 15 sec
        ModelClass.CHAT_SMALL: 600.0,         # Chat (<=4B) 10 min
        ModelClass.CHAT_LARGE: 1800.0,        # Chat (>=7B) 30 min
        ModelClass.REASONING: 1800.0,         # Reasoning 30 min
    }


def test_classify_model_classes():
    assert classify_model("qwen3-embedding-0.6b", "rkllama") == ModelClass.EMBEDDING
    assert classify_model("qwen3-reranker-0.6b", "rkllama") == ModelClass.RERANKING
    assert classify_model("qmd-query-expansion", "rkllama") == ModelClass.QUERY_EXPANSION
    assert classify_model("qwen2.5-3b-instruct", "ollama") == ModelClass.CHAT_SMALL
    assert classify_model("llama-3.1-8b", "ollama") == ModelClass.CHAT_LARGE
    assert classify_model("deepseek-r1-7b", "ollama") == ModelClass.REASONING
    assert classify_model("dreamshaper-8", "sd-cpp") == ModelClass.IMAGE_GENERATION
    assert classify_model("whisper-large-v3", "whisper") == ModelClass.SPEECH_TO_TEXT
    # Image-editing backends are not in #62's table: unclassified, no TTL.
    assert classify_model("lama", "iopaint") == ""


# ---------------------------------------------------------------------------
# The idle sweep
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_idle_resident_past_ttl_is_evicted_via_unload():
    unload = _FakeUnload()
    sched, _clock = _scheduler(unload=unload)
    m = _resident("qwen3-embedding-0.6b", "rkllama", idle_for=31.0)  # TTL 30s
    sched.register_loaded(m)
    evicted = await sched.evict_idle()
    assert evicted == [m]
    assert unload.calls == [m]
    assert sched.get_resident("qwen3-embedding-0.6b") is None
    assert any(name == "model_evicted" for name, _ in sched.events)


@pytest.mark.asyncio
async def test_resident_within_ttl_survives():
    unload = _FakeUnload()
    sched, _clock = _scheduler(unload=unload)
    m = _resident("llama-3.1-8b", "ollama", idle_for=600.0)  # chat-large TTL 1800s
    sched.register_loaded(m)
    assert await sched.evict_idle() == []
    assert unload.calls == []
    assert sched.get_resident("llama-3.1-8b") is not None


@pytest.mark.asyncio
async def test_pinned_and_always_resident_survive_past_ttl():
    unload = _FakeUnload()
    sched, _clock = _scheduler(unload=unload)
    pinned = _resident("qwen3-embedding-0.6b", "rkllama", idle_for=3600.0, pinned=True)
    always = _resident(
        "qwen3-reranker-0.6b", "rkllama", idle_for=3600.0,
        priority=PriorityClass.ALWAYS_RESIDENT,
    )
    sched.register_loaded(pinned)
    sched.register_loaded(always)
    assert await sched.evict_idle() == []
    assert unload.calls == []
    assert sched.get_resident("qwen3-embedding-0.6b") is not None
    assert sched.get_resident("qwen3-reranker-0.6b") is not None


@pytest.mark.asyncio
async def test_in_use_resident_survives_past_ttl():
    """backend_unload's caller contract: never unload a model in flight."""
    unload = _FakeUnload()
    sched, _clock = _scheduler(unload=unload)
    m = _resident("qwen3-embedding-0.6b", "rkllama", idle_for=3600.0, in_use_count=1)
    sched.register_loaded(m)
    assert await sched.evict_idle() == []
    assert unload.calls == []
    assert sched.get_resident("qwen3-embedding-0.6b") is not None


@pytest.mark.asyncio
async def test_chat_size_classes_evict_on_their_own_ttls():
    unload = _FakeUnload()
    sched, clock = _scheduler(unload=unload)
    small = _resident("qwen2.5-3b-instruct", "ollama", idle_for=0.0)
    large = _resident("llama-3.1-8b", "ollama", idle_for=0.0)
    sched.register_loaded(small)
    sched.register_loaded(large)
    clock["now"] = T0 + 601  # past small's 600s, within large's 1800s
    assert await sched.evict_idle() == [small]
    clock["now"] = T0 + 1801
    assert await sched.evict_idle() == [large]


@pytest.mark.asyncio
async def test_failed_unload_keeps_resident():
    """If the backend refuses the unload it still holds the model; the
    registry must keep tracking it so a later sweep can retry."""
    unload = _FakeUnload(result=False)
    sched, _clock = _scheduler(unload=unload)
    m = _resident("qwen3-embedding-0.6b", "rkllama", idle_for=3600.0)
    sched.register_loaded(m)
    assert await sched.evict_idle() == []
    assert unload.calls == [m]
    assert sched.get_resident("qwen3-embedding-0.6b") is not None


@pytest.mark.asyncio
async def test_unclassified_resident_is_never_idle_evicted():
    unload = _FakeUnload()
    sched, _clock = _scheduler(unload=unload)
    m = _resident("lama", "paint", idle_for=86400.0)
    sched.register_loaded(m)
    assert await sched.evict_idle() == []
    assert unload.calls == []


@pytest.mark.asyncio
async def test_eviction_without_unload_hook_still_deregisters():
    """With no unload hook the sweep is registry-only, mirroring the
    pressure-eviction behaviour when evict_fn is None."""
    sched, _clock = _scheduler(unload=None)
    m = _resident("qwen3-embedding-0.6b", "rkllama", idle_for=3600.0)
    sched.register_loaded(m)
    assert await sched.evict_idle() == [m]
    assert sched.get_resident("qwen3-embedding-0.6b") is None


@pytest.mark.asyncio
async def test_idle_eviction_loop_ticks():
    unload = _FakeUnload()
    sched, _clock = _scheduler(unload=unload)
    m = _resident("qwen3-embedding-0.6b", "rkllama", idle_for=3600.0)
    sched.register_loaded(m)
    task = asyncio.create_task(sched.idle_eviction_loop(interval_seconds=0.01))
    try:
        await asyncio.sleep(0.1)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    assert sched.get_resident("qwen3-embedding-0.6b") is None
    assert unload.calls == [m]


# ---------------------------------------------------------------------------
# Catalog ttl override
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_catalog_ttl_override_beats_class_default():
    """Resolver stands in for the catalog: rkllama's entry carries
    ttl_seconds=5 (shorter than chat-large's 1800), ollama's carries
    7200 (longer than embedding's 30). The override wins both ways."""
    unload = _FakeUnload()

    def resolver(model: LoadedModel):
        return {"rkllama": 5.0, "ollama": 7200.0}.get(model.backend)

    sched, clock = _scheduler(unload=unload, ttl_resolver=resolver)
    fast = _resident("llama-3.1-8b", "rkllama", idle_for=0.0)
    slow = _resident("qwen3-embedding-0.6b", "ollama", idle_for=0.0)
    sched.register_loaded(fast)
    sched.register_loaded(slow)
    clock["now"] = T0 + 60
    assert await sched.evict_idle() == [fast]
    assert sched.get_resident("qwen3-embedding-0.6b") is not None


@pytest.mark.asyncio
async def test_backend_entry_carries_ttl_seconds():
    async def probe(backend: dict) -> dict:
        return {"status": "ok", "response_ms": 1, "models": []}

    backends = [
        {"name": "b1", "type": "rkllama", "url": "http://b1", "priority": 1,
         "enabled": True, "ttl_seconds": 42},
        {"name": "b2", "type": "rkllama", "url": "http://b2", "priority": 2,
         "enabled": True},
    ]
    catalog = BackendCatalog(backends=backends, probe_fn=probe, interval_seconds=3600)
    await catalog.start()
    await catalog.wait_initial_probe()
    try:
        entries = {e.name: e for e in catalog.backends_with_capability("llm-chat")}
        assert entries["b1"].ttl_seconds == 42.0
        assert entries["b1"].to_dict()["ttl_seconds"] == 42.0
        assert entries["b2"].ttl_seconds is None
    finally:
        await catalog.stop()


def test_auto_register_ttl_seconds(tmp_path: Path):
    """manifest lifecycle.ttl_seconds flows into the config backend entry,
    mirroring keep_alive_minutes."""
    manifest = tmp_path / "rkllama.yaml"
    manifest.write_text("""
id: rkllama
name: rkllama
type: rkllama
default_url: http://localhost:8080
lifecycle:
  auto_manage: true
  ttl_seconds: 120
""")
    config = AppConfig()
    assert auto_register_from_manifest(manifest, config) is True
    assert config.backends[0]["ttl_seconds"] == 120


# ---------------------------------------------------------------------------
# TTL override parsing (PR #4496 review): one shared guarded parser.
# Invalid or non-finite overrides fall back to the class TTL and must
# never abort the sweep.
# ---------------------------------------------------------------------------

def test_parse_ttl_seconds_accepts_valid_and_rejects_garbage():
    assert parse_ttl_seconds(45) == 45.0
    assert parse_ttl_seconds("45") == 45.0
    assert parse_ttl_seconds(0) == 0.0
    assert parse_ttl_seconds(None) is None
    assert parse_ttl_seconds("30s") is None
    assert parse_ttl_seconds("nan") is None
    assert parse_ttl_seconds(float("nan")) is None
    assert parse_ttl_seconds(float("inf")) is None
    assert parse_ttl_seconds(-5) is None
    assert parse_ttl_seconds(object()) is None


def test_catalog_ttl_seconds_from_rejects_non_finite():
    """float("nan") parses, so the catalog's old try/except let NaN
    through as a live override; the shared parser rejects it."""
    assert _ttl_seconds_from({"name": "x", "ttl_seconds": "nan"}) is None
    assert _ttl_seconds_from({"name": "x", "ttl_seconds": float("inf")}) is None
    assert _ttl_seconds_from({"name": "x", "ttl_seconds": "30s"}) is None
    assert _ttl_seconds_from({"name": "x", "ttl_seconds": 42}) == 42.0
    assert _ttl_seconds_from({"name": "x"}) is None


@pytest.mark.asyncio
async def test_invalid_ttl_override_falls_back_and_sweep_continues():
    """A resolver handing back a raw manifest value ("30s") must not
    abort the sweep: that resident falls back to its class TTL and
    residents after it are still considered."""
    unload = _FakeUnload()

    def resolver(model: LoadedModel):
        if model.backend == "rkllama":
            return "30s"  # raw manifest value, unparseable
        return None

    sched, _clock = _scheduler(unload=unload, ttl_resolver=resolver)
    bad = _resident("qwen3-embedding-0.6b", "rkllama", idle_for=3600.0)
    later = _resident("qwen3-reranker-0.6b", "ollama", idle_for=3600.0)
    sched.register_loaded(bad)
    sched.register_loaded(later)
    evicted = await sched.evict_idle()
    assert evicted == [bad, later]
    assert unload.calls == [bad, later]


@pytest.mark.asyncio
async def test_nan_ttl_override_does_not_evict_fresh_resident():
    """With a NaN TTL, idle_for < ttl is False even at idle_for == 0,
    so a just-used resident was evicted immediately. The parser
    rejects NaN; the class TTL applies instead."""
    unload = _FakeUnload()

    def resolver(model: LoadedModel):
        return float("nan")

    sched, clock = _scheduler(unload=unload, ttl_resolver=resolver)
    fresh = _resident("llama-3.1-8b", "ollama", idle_for=0.0)  # class TTL 1800s
    sched.register_loaded(fresh)
    assert await sched.evict_idle() == []
    assert unload.calls == []
    clock["now"] = T0 + 1801  # past the class TTL: now it goes
    assert await sched.evict_idle() == [fresh]


# ---------------------------------------------------------------------------
# Pending-eviction race (PR #4496 review): evict_idle awaits the unload
# hook; begin_use must not admit to the record being unloaded, removal
# must hit only the exact record, and a failed unload releases the
# pending state.
# ---------------------------------------------------------------------------

class _BlockingUnload:
    """Unload hook that parks inside the call until released."""

    def __init__(self, result: bool = True):
        self.result = result
        self.calls: list[LoadedModel] = []
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def __call__(self, model: LoadedModel) -> bool:
        self.calls.append(model)
        self.entered.set()
        await self.release.wait()
        return self.result


@pytest.mark.asyncio
async def test_begin_use_during_pending_unload_gets_fresh_record():
    unload = _BlockingUnload(result=True)
    sched, _clock = _scheduler(unload=unload)
    m = _resident("qwen3-embedding-0.6b", "rkllama", idle_for=3600.0)
    sched.register_loaded(m)
    sweep = asyncio.create_task(sched.evict_idle())
    await asyncio.wait_for(unload.entered.wait(), timeout=5)
    admitted = sched.begin_use("qwen3-embedding-0.6b", "rkllama", "rkllama")
    assert admitted is not m  # never the record whose unload is pending
    assert m.in_use_count == 0
    assert admitted.in_use_count == 1
    unload.release.set()
    await asyncio.wait_for(sweep, timeout=5)
    # The successful unload removed only the old record; the record
    # admitted during the await stays registered and balances.
    assert sched.get_resident("qwen3-embedding-0.6b", "rkllama") is admitted
    sched.end_use("qwen3-embedding-0.6b", "rkllama")
    assert admitted.in_use_count == 0


@pytest.mark.asyncio
async def test_replacement_registered_during_unload_survives():
    unload = _BlockingUnload(result=True)
    sched, _clock = _scheduler(unload=unload)
    m = _resident("qwen3-embedding-0.6b", "rkllama", idle_for=3600.0)
    sched.register_loaded(m)
    sweep = asyncio.create_task(sched.evict_idle())
    await asyncio.wait_for(unload.entered.wait(), timeout=5)
    replacement = _resident("qwen3-embedding-0.6b", "rkllama", idle_for=0.0)
    sched.register_loaded(replacement)
    unload.release.set()
    await asyncio.wait_for(sweep, timeout=5)
    assert sched.get_resident("qwen3-embedding-0.6b", "rkllama") is replacement


@pytest.mark.asyncio
async def test_failed_unload_releases_pending_state():
    unload = _BlockingUnload(result=False)
    sched, _clock = _scheduler(unload=unload)
    m = _resident("qwen3-embedding-0.6b", "rkllama", idle_for=3600.0)
    sched.register_loaded(m)
    sweep = asyncio.create_task(sched.evict_idle())
    await asyncio.wait_for(unload.entered.wait(), timeout=5)
    unload.release.set()
    assert await asyncio.wait_for(sweep, timeout=5) == []
    assert sched.get_resident("qwen3-embedding-0.6b", "rkllama") is m
    # Pending state released: the same record admits requests again.
    admitted = sched.begin_use("qwen3-embedding-0.6b", "rkllama", "rkllama")
    assert admitted is m
    sched.end_use("qwen3-embedding-0.6b", "rkllama")
    # And a later sweep can retry the eviction.
    m.last_used_at = T0 - 3600.0
    unload.result = True
    assert await sched.evict_idle() == [m]
    assert sched.get_resident("qwen3-embedding-0.6b", "rkllama") is None


@pytest.mark.asyncio
async def test_overlapping_begin_use_during_refused_unload_shares_replacement():
    # CodeRabbit round 2 on PR #4496: while an idle unload is pending,
    # a second begin_use() for the same key must reuse the replacement
    # record the first one built, not overwrite it with another fresh
    # record -- otherwise the two requests sit on separate records, and
    # when the refused unload clears the mark, the first end_use()
    # drops the registered record to zero while the second request
    # still runs, making the model idle-evictable mid-request.
    unload = _BlockingUnload(result=False)  # refused unload
    sched, _clock = _scheduler(unload=unload)
    m = _resident("qwen3-embedding-0.6b", "rkllama", idle_for=3600.0)
    sched.register_loaded(m)
    sweep = asyncio.create_task(sched.evict_idle())
    await asyncio.wait_for(unload.entered.wait(), timeout=5)
    first = sched.begin_use("qwen3-embedding-0.6b", "rkllama", "rkllama")
    second = sched.begin_use("qwen3-embedding-0.6b", "rkllama", "rkllama")
    assert first is not m and second is not m
    assert second is first  # one shared replacement record
    registered = sched.get_resident("qwen3-embedding-0.6b", "rkllama")
    assert registered is first
    assert registered.in_use_count == 2  # both requests counted
    unload.release.set()
    assert await asyncio.wait_for(sweep, timeout=5) == []
    sched.end_use("qwen3-embedding-0.6b", "rkllama")
    assert registered.in_use_count == 1  # second request still running
    sched.end_use("qwen3-embedding-0.6b", "rkllama")
    assert registered.in_use_count == 0


@pytest.mark.asyncio
async def test_sweep_skips_snapshot_record_replaced_before_its_turn():
    # CodeRabbit round 2 on PR #4496: the sweep iterates a snapshot.
    # If a resident is removed (pressure eviction) and a replacement
    # registered under the same key while an earlier record's unload
    # is awaited, the sweep must not call the unload hook for the
    # stale snapshot record -- the hook unloads by model id on the
    # backend, so it would unload the replacement while the
    # replacement serves a request.
    unload = _BlockingUnload(result=True)
    sched, _clock = _scheduler(unload=unload)
    blocker = _resident("blocker-embed-model", "rkllama", idle_for=3600.0)
    stale = _resident("qwen3-embedding-0.6b", "rkllama", idle_for=3600.0)
    sched.register_loaded(blocker)
    sched.register_loaded(stale)
    sweep = asyncio.create_task(sched.evict_idle())
    await asyncio.wait_for(unload.entered.wait(), timeout=5)
    # While the sweep is parked in blocker's unload: pressure-evict
    # the stale record, then a fresh request registers a replacement
    # under the same key and starts serving on it.
    sched.mark_unloaded("qwen3-embedding-0.6b", "rkllama")
    replacement = sched.begin_use("qwen3-embedding-0.6b", "rkllama", "rkllama")
    assert replacement is not stale
    unload.release.set()
    evicted = await asyncio.wait_for(sweep, timeout=5)
    assert unload.calls == [blocker]  # hook never called for the stale record
    assert evicted == [blocker]
    assert sched.get_resident("qwen3-embedding-0.6b", "rkllama") is replacement
    assert replacement.in_use_count == 1
    sched.end_use("qwen3-embedding-0.6b", "rkllama")

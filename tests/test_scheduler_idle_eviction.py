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
from tinyagentos.scheduler.backend_catalog import BackendCatalog
from tinyagentos.scheduler.core_aware_scheduler import CoreAwareModelScheduler
from tinyagentos.scheduler.loaded_model import (
    DEFAULT_MODEL_TTLS,
    LoadedModel,
    ModelClass,
    PriorityClass,
    classify_model,
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

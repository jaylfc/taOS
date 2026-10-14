"""Tests for TTL-based idle eviction (taOS #62).

Covers:
  - per-class TTL defaults from the issue table
  - per-model ttl_seconds override wins over the class default
  - ttl_seconds=0 disables idle eviction for that resident
  - pinned / always_resident models never expire
  - touch() resets the idle clock
  - sweep_idle() evicts only expired residents, oldest-idle first,
    emits model_evicted events, and invokes evict_fn
  - idle_expired() is a pure query (nothing evicted)
  - load_with_core_awareness stores model_class / ttl_seconds
"""
from __future__ import annotations

import pytest

from tinyagentos.scheduler.core_aware_scheduler import CoreAwareModelScheduler
from tinyagentos.scheduler.loaded_model import (
    DEFAULT_TTL_SECONDS,
    LoadedModel,
    ModelClass,
    PriorityClass,
)
from tinyagentos.scheduler.resource_shape import make_cpu_shape

BASE = 1_700_000_000.0  # fixed "now" for deterministic idle maths


def _resident(
    model_id: str,
    model_class: str = ModelClass.UNKNOWN,
    idle_seconds: float = 0,
    pinned: bool = False,
    priority: str = PriorityClass.INTERACTIVE,
    ttl_seconds: float | None = None,
) -> LoadedModel:
    m = LoadedModel(
        model_id=model_id,
        backend="llama-cpp",
        memory_mb_used=512,
        model_class=model_class,
        pinned=pinned,
        priority=priority,
        ttl_seconds=ttl_seconds,
    )
    m.last_used_at = BASE - idle_seconds
    return m


class TestDefaultTtls:
    def test_issue_table_defaults(self):
        assert DEFAULT_TTL_SECONDS[ModelClass.IMAGE_GEN] == 5 * 60
        assert DEFAULT_TTL_SECONDS[ModelClass.STT] == 2 * 60
        assert DEFAULT_TTL_SECONDS[ModelClass.TTS] == 2 * 60
        assert DEFAULT_TTL_SECONDS[ModelClass.EMBEDDING] == 30
        assert DEFAULT_TTL_SECONDS[ModelClass.RERANKER] == 15
        assert DEFAULT_TTL_SECONDS[ModelClass.QUERY_EXPANSION] == 15
        assert DEFAULT_TTL_SECONDS[ModelClass.CHAT_SMALL] == 10 * 60
        assert DEFAULT_TTL_SECONDS[ModelClass.CHAT_LARGE] == 30 * 60
        assert DEFAULT_TTL_SECONDS[ModelClass.REASONING] == 30 * 60

    def test_unknown_class_falls_back_to_chat_large(self):
        m = _resident("m", model_class="not-a-class")
        assert m.idle_ttl_seconds() == 30 * 60

    def test_override_wins_over_class_default(self):
        m = _resident("m", model_class=ModelClass.EMBEDDING, ttl_seconds=3600)
        assert m.idle_ttl_seconds() == 3600


class TestIsIdleExpired:
    def test_fresh_resident_not_expired(self):
        m = _resident("m", model_class=ModelClass.EMBEDDING, idle_seconds=10)
        assert m.is_idle_expired(BASE) is False

    def test_expired_at_exactly_ttl(self):
        m = _resident("m", model_class=ModelClass.EMBEDDING, idle_seconds=30)
        assert m.is_idle_expired(BASE) is True

    def test_pinned_never_expires(self):
        m = _resident("m", model_class=ModelClass.EMBEDDING, idle_seconds=10_000, pinned=True)
        assert m.is_idle_expired(BASE) is False

    def test_always_resident_priority_never_expires(self):
        m = _resident(
            "m",
            model_class=ModelClass.EMBEDDING,
            idle_seconds=10_000,
            priority=PriorityClass.ALWAYS_RESIDENT,
        )
        assert m.is_idle_expired(BASE) is False

    def test_ttl_zero_disables_expiry(self):
        m = _resident("m", model_class=ModelClass.EMBEDDING, idle_seconds=10_000, ttl_seconds=0)
        assert m.is_idle_expired(BASE) is False

    def test_clock_runs_from_last_used_not_loaded(self):
        m = _resident("m", model_class=ModelClass.EMBEDDING, idle_seconds=10)
        m.loaded_at = BASE - 10_000  # loaded long ago, used recently
        assert m.is_idle_expired(BASE) is False

    def test_touch_resets_idle_clock(self):
        m = _resident("m", model_class=ModelClass.EMBEDDING, idle_seconds=10_000)
        assert m.is_idle_expired(BASE) is True
        m.last_used_at = BASE  # simulate touch() at BASE
        assert m.is_idle_expired(BASE) is False


class TestSweepIdle:
    def _sched_with(self, *residents: LoadedModel) -> CoreAwareModelScheduler:
        sched = CoreAwareModelScheduler(shape_lookup=lambda _b: make_cpu_shape())
        for r in residents:
            sched.register_loaded(r)
        return sched

    @pytest.mark.asyncio
    async def test_sweep_evicts_only_expired(self):
        sched = self._sched_with(
            _resident("stale-embed", ModelClass.EMBEDDING, idle_seconds=60),
            _resident("fresh-embed", ModelClass.EMBEDDING, idle_seconds=5),
            _resident("stale-chat", ModelClass.CHAT_SMALL, idle_seconds=11 * 60),
            _resident("pinned-stale", ModelClass.EMBEDDING, idle_seconds=60, pinned=True),
        )
        evicted = await sched.sweep_idle(BASE)
        assert {m.model_id for m in evicted} == {"stale-embed", "stale-chat"}
        assert {m.model_id for m in sched.residents()} == {"fresh-embed", "pinned-stale"}

    @pytest.mark.asyncio
    async def test_sweep_evicts_oldest_idle_first(self):
        sched = self._sched_with(
            _resident("newer-stale", ModelClass.EMBEDDING, idle_seconds=60),
            _resident("older-stale", ModelClass.EMBEDDING, idle_seconds=600),
        )
        evicted = await sched.sweep_idle(BASE)
        assert [m.model_id for m in evicted] == ["older-stale", "newer-stale"]

    @pytest.mark.asyncio
    async def test_sweep_emits_events_and_calls_evict_fn(self):
        seen: list[str] = []
        sched = CoreAwareModelScheduler(
            shape_lookup=lambda _b: make_cpu_shape(),
            evict_fn=lambda m: seen.append(m.model_id),
        )
        sched.register_loaded(_resident("stale", ModelClass.RERANKER, idle_seconds=60))
        await sched.sweep_idle(BASE)
        assert seen == ["stale"]
        assert ("model_evicted", {"model_id": "stale", "priority": PriorityClass.INTERACTIVE, "tp_mode": "", "backend": "llama-cpp"}) in sched.events

    @pytest.mark.asyncio
    async def test_sweep_with_nothing_expired_is_noop(self):
        sched = self._sched_with(_resident("fresh", ModelClass.CHAT_LARGE, idle_seconds=60))
        assert await sched.sweep_idle(BASE) == []
        assert sched.get_resident("fresh") is not None

    def test_idle_expired_is_pure_query(self):
        sched = self._sched_with(
            _resident("stale", ModelClass.EMBEDDING, idle_seconds=60),
            _resident("fresh", ModelClass.EMBEDDING, idle_seconds=5),
        )
        assert [m.model_id for m in sched.idle_expired(BASE)] == ["stale"]
        # Nothing evicted by the query.
        assert sched.get_resident("stale") is not None


class TestLoadStoresTtl:
    @pytest.mark.asyncio
    async def test_load_with_core_awareness_stores_class_and_override(self):
        sched = CoreAwareModelScheduler(shape_lookup=lambda _b: make_cpu_shape())
        m = await sched.load_with_core_awareness(
            "img-1", "sd-cpp", model_class=ModelClass.IMAGE_GEN, ttl_seconds=42
        )
        assert m.model_class == ModelClass.IMAGE_GEN
        assert m.ttl_seconds == 42
        assert m.idle_ttl_seconds() == 42

    @pytest.mark.asyncio
    async def test_load_defaults_to_unknown_class(self):
        sched = CoreAwareModelScheduler(shape_lookup=lambda _b: make_cpu_shape())
        m = await sched.load_with_core_awareness("chat-1", "ollama")
        assert m.model_class == ModelClass.UNKNOWN
        assert m.ttl_seconds is None
        assert m.idle_ttl_seconds() == 30 * 60

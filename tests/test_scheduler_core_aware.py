"""Tests for the Phase 1.5 core-aware NPU scheduler extension.

Covers:
  - tp_mode=all when solo
  - Split 0,1 + 2 when two models load
  - Split 0 + 1 + 2 when three load
  - Four-model case triggers eviction
  - always_resident priority is never shrunk
  - Backend without cores falls back to memory-only check
  - Shrink-reload emits the right events
  - Evict-reload triggers on lower-priority resident
  - 503-equivalent (ResourceContention) when no lower-priority victim
"""
from __future__ import annotations

import asyncio
import pytest

from tinyagentos.scheduler.core_aware_scheduler import (
    CoreAwareModelScheduler,
    ResourceContention,
    _cores_to_tp_mode,
    _tp_mode_to_cores,
)
from tinyagentos.scheduler.loaded_model import LoadedModel, PriorityClass
from tinyagentos.scheduler.resource_shape import (
    BackendResourceShape,
    make_cpu_shape,
    make_cuda_shape,
    make_rk3588_npu_shape,
)


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------

def npu_shape_lookup(backend_type: str) -> BackendResourceShape:
    """Shape lookup that returns RK3588 NPU shape for 'rkllama'."""
    if backend_type == "rkllama":
        return make_rk3588_npu_shape()
    return make_cpu_shape()


def cpu_shape_lookup(backend_type: str) -> BackendResourceShape:
    return make_cpu_shape()


def cuda_shape_lookup(backend_type: str) -> BackendResourceShape:
    return make_cuda_shape(gpu_count=2)


def _resident(
    model_id: str,
    cores: list[int],
    priority: str = PriorityClass.INTERACTIVE,
    pinned: bool = False,
) -> LoadedModel:
    """Build a pre-loaded NPU resident for seeding the scheduler."""
    return LoadedModel(
        model_id=model_id,
        backend="rkllama",
        memory_mb_used=512,
        resource_holds={"cores": cores},
        tp_mode=_cores_to_tp_mode(cores),
        priority=priority,
        pinned=pinned,
    )


# ---------------------------------------------------------------------------
# tp_mode helper unit tests
# ---------------------------------------------------------------------------

class TestTpModeHelpers:
    def test_cores_to_tp_mode_all(self):
        assert _cores_to_tp_mode([0, 1, 2]) == "all"

    def test_cores_to_tp_mode_two(self):
        assert _cores_to_tp_mode([0, 1]) == "0,1"

    def test_cores_to_tp_mode_single(self):
        assert _cores_to_tp_mode([2]) == "2"

    def test_cores_to_tp_mode_empty(self):
        assert _cores_to_tp_mode([]) == ""

    def test_tp_mode_to_cores_all(self):
        assert _tp_mode_to_cores("all") == [0, 1, 2]

    def test_tp_mode_to_cores_two(self):
        assert _tp_mode_to_cores("0,1") == [0, 1]

    def test_tp_mode_to_cores_single(self):
        assert _tp_mode_to_cores("2") == [2]

    def test_tp_mode_to_cores_empty(self):
        assert _tp_mode_to_cores("") == []


# ---------------------------------------------------------------------------
# Solo load: tp_mode = all
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_solo_model_gets_all_cores():
    """When no model is resident, a new NPU load should get tp_mode='all'."""
    sched = CoreAwareModelScheduler(shape_lookup=npu_shape_lookup)
    result = await sched.load_with_core_awareness(
        model_id="dreamshaper",
        backend_name="rkllama",
    )
    assert result.tp_mode == "all"
    assert sorted(result.cores_held()) == [0, 1, 2]


# ---------------------------------------------------------------------------
# Two models: 0,1 + 2
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_two_models_split_cores():
    """Second model to load gets the remaining core after the first
    is shrunk from 'all' to '0,1'."""
    sched = CoreAwareModelScheduler(shape_lookup=npu_shape_lookup)

    # Seed the first model holding all three cores.
    first = _resident("model-a", [0, 1, 2])
    sched.register_loaded(first)

    # Load second model. The first should be shrunk to [0,1] and the
    # second gets [2].
    result = await sched.load_with_core_awareness(
        model_id="model-b",
        backend_name="rkllama",
        priority=PriorityClass.INTERACTIVE,
    )

    # Second model gets exactly one core.
    assert len(result.cores_held()) == 1
    # First model should now hold 2 cores.
    first_after = sched.get_resident("model-a")
    assert first_after is not None
    assert len(first_after.cores_held()) == 2
    # Together they cover all 3 cores.
    all_held = sorted(result.cores_held() + first_after.cores_held())
    assert all_held == [0, 1, 2]
    # Shrink event was emitted.
    shrink_events = [e for e in sched.events if e[0] == "model_shrunk"]
    assert len(shrink_events) == 1
    assert shrink_events[0][1]["model_id"] == "model-a"


# ---------------------------------------------------------------------------
# Three models: 0 + 1 + 2
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_three_models_each_get_one_core():
    """Third model should get one core, with existing models shrunk down."""
    sched = CoreAwareModelScheduler(shape_lookup=npu_shape_lookup)

    # Seed two models each holding their share.
    sched.register_loaded(_resident("model-a", [0, 1]))
    sched.register_loaded(_resident("model-b", [2]))

    result = await sched.load_with_core_awareness(
        model_id="model-c",
        backend_name="rkllama",
    )

    # model-a needs to shrink to give model-c a core.
    a_after = sched.get_resident("model-a")
    b_after = sched.get_resident("model-b")

    assert a_after is not None
    assert b_after is not None
    # model-c gets one core.
    assert len(result.cores_held()) == 1
    # All cores covered once.
    total = sorted(
        result.cores_held()
        + a_after.cores_held()
        + b_after.cores_held()
    )
    assert total == [0, 1, 2]


# ---------------------------------------------------------------------------
# Four models: triggers eviction first
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_four_models_triggers_eviction():
    """With three models each on one core, a fourth load must evict one."""
    sched = CoreAwareModelScheduler(shape_lookup=npu_shape_lookup)

    sched.register_loaded(_resident("model-a", [0], priority=PriorityClass.BACKGROUND))
    sched.register_loaded(_resident("model-b", [1]))
    sched.register_loaded(_resident("model-c", [2]))

    # Fourth model -- should evict the background model to claim core 0.
    result = await sched.load_with_core_awareness(
        model_id="model-d",
        backend_name="rkllama",
        priority=PriorityClass.INTERACTIVE,
    )

    # model-a (background) should have been evicted.
    assert sched.get_resident("model-a") is None
    evict_events = [e for e in sched.events if e[0] == "model_evicted"]
    assert any(e[1]["model_id"] == "model-a" for e in evict_events)
    # model-d got a core.
    assert len(result.cores_held()) == 1


# ---------------------------------------------------------------------------
# always_resident is never shrunk
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_always_resident_never_shrunk():
    """A pinned always_resident model should never be a shrink victim."""
    sched = CoreAwareModelScheduler(shape_lookup=npu_shape_lookup)

    # Seed a pinned model holding all cores.
    pinned = _resident("pinned-model", [0, 1, 2], pinned=True)
    sched.register_loaded(pinned)

    with pytest.raises(ResourceContention) as exc_info:
        await sched.load_with_core_awareness(
            model_id="new-model",
            backend_name="rkllama",
            priority=PriorityClass.INTERACTIVE,
        )

    assert "new-model" in str(exc_info.value)
    # The pinned model is untouched.
    assert sched.get_resident("pinned-model") is not None
    assert sched.get_resident("pinned-model").tp_mode == "all"
    # No shrink events.
    assert not any(e[0] == "model_shrunk" for e in sched.events)


# ---------------------------------------------------------------------------
# Backend without cores: memory-only fallback
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_core_backend_falls_back_to_memory_only():
    """A CPU or CUDA backend with no cores should return a LoadedModel
    with empty resource_holds and an empty tp_mode."""
    sched = CoreAwareModelScheduler(shape_lookup=cpu_shape_lookup)
    result = await sched.load_with_core_awareness(
        model_id="llm-7b",
        backend_name="llama-cpp",
    )
    assert result.tp_mode == ""
    assert result.cores_held() == []
    assert result.resource_holds == {}


@pytest.mark.asyncio
async def test_cuda_backend_no_core_pressure():
    """CUDA backends have gpu_ids but no NPU cores. The scheduler
    should return a model with empty resource_holds (no NPU tracking)."""
    sched = CoreAwareModelScheduler(shape_lookup=cuda_shape_lookup)
    result = await sched.load_with_core_awareness(
        model_id="mistral-7b",
        backend_name="vllm",
    )
    assert result.cores_held() == []
    assert result.tp_mode == ""


# ---------------------------------------------------------------------------
# Shrink-reload event verification
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_shrink_reload_emits_event():
    """When a lower-priority resident is shrunk, a model_shrunk event
    must be emitted with old_tp_mode and new_tp_mode."""
    sched = CoreAwareModelScheduler(shape_lookup=npu_shape_lookup)
    sched.register_loaded(_resident("solo-model", [0, 1, 2], priority=PriorityClass.INTERACTIVE))

    await sched.load_with_core_awareness(
        model_id="incoming-model",
        backend_name="rkllama",
        priority=PriorityClass.INTERACTIVE,
    )

    shrink_events = [e for e in sched.events if e[0] == "model_shrunk"]
    assert len(shrink_events) >= 1
    ev = shrink_events[0][1]
    assert "old_tp_mode" in ev
    assert "new_tp_mode" in ev
    assert ev["old_tp_mode"] != ev["new_tp_mode"]


@pytest.mark.asyncio
async def test_shrink_reload_calls_reload_fn():
    """reload_fn is invoked during shrink-reload."""
    calls: list[tuple] = []

    async def fake_reload(model: LoadedModel, new_tp: str) -> None:
        calls.append((model.model_id, new_tp))

    sched = CoreAwareModelScheduler(
        shape_lookup=npu_shape_lookup,
        reload_fn=fake_reload,
    )
    sched.register_loaded(_resident("resident", [0, 1, 2]))

    await sched.load_with_core_awareness(
        model_id="new-model",
        backend_name="rkllama",
    )

    assert len(calls) == 1
    assert calls[0][0] == "resident"


# ---------------------------------------------------------------------------
# Evict-reload on lower-priority resident
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_evict_reload_lower_priority():
    """Background model should be evicted when an interactive load needs
    its core and cannot shrink it further (already on 1 core)."""
    sched = CoreAwareModelScheduler(shape_lookup=npu_shape_lookup)

    sched.register_loaded(_resident("bg-model", [0], priority=PriorityClass.BACKGROUND))
    sched.register_loaded(_resident("model-b", [1]))
    sched.register_loaded(_resident("model-c", [2]))

    result = await sched.load_with_core_awareness(
        model_id="interactive-model",
        backend_name="rkllama",
        priority=PriorityClass.INTERACTIVE,
    )

    # bg-model evicted.
    assert sched.get_resident("bg-model") is None
    evict_events = [e for e in sched.events if e[0] == "model_evicted"]
    assert any(e[1]["model_id"] == "bg-model" for e in evict_events)
    assert len(result.cores_held()) == 1


@pytest.mark.asyncio
async def test_evict_reload_calls_evict_fn():
    """evict_fn is invoked during evict-reload."""
    calls: list[str] = []

    async def fake_evict(model: LoadedModel) -> None:
        calls.append(model.model_id)

    sched = CoreAwareModelScheduler(
        shape_lookup=npu_shape_lookup,
        evict_fn=fake_evict,
    )

    sched.register_loaded(_resident("bg", [0], priority=PriorityClass.BACKGROUND))
    sched.register_loaded(_resident("m1", [1]))
    sched.register_loaded(_resident("m2", [2]))

    await sched.load_with_core_awareness(
        model_id="incoming",
        backend_name="rkllama",
        priority=PriorityClass.INTERACTIVE,
    )

    assert "bg" in calls


# ---------------------------------------------------------------------------
# 503 equivalent: no lower-priority victim
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resource_contention_no_evictable_victim():
    """If all residents are always_resident and cores are full, the
    scheduler raises ResourceContention (HTTP 503)."""
    sched = CoreAwareModelScheduler(shape_lookup=npu_shape_lookup)

    sched.register_loaded(_resident("pinned-a", [0, 1], pinned=True))
    sched.register_loaded(_resident("pinned-b", [2], pinned=True))

    with pytest.raises(ResourceContention):
        await sched.load_with_core_awareness(
            model_id="rejected-model",
            backend_name="rkllama",
            priority=PriorityClass.INTERACTIVE,
        )


@pytest.mark.asyncio
async def test_resource_contention_equal_priority_holder():
    """Interactive can not evict another interactive at full allocation
    when no background victims exist."""
    sched = CoreAwareModelScheduler(shape_lookup=npu_shape_lookup)

    # Fill all 3 cores with interactive pinned models.
    sched.register_loaded(_resident("i-a", [0], pinned=True))
    sched.register_loaded(_resident("i-b", [1], pinned=True))
    sched.register_loaded(_resident("i-c", [2], pinned=True))

    with pytest.raises(ResourceContention):
        await sched.load_with_core_awareness(
            model_id="new",
            backend_name="rkllama",
            priority=PriorityClass.INTERACTIVE,
        )


# ---------------------------------------------------------------------------
# Registry helpers
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_register_and_mark_unloaded():
    """register_loaded and mark_unloaded maintain the registry correctly."""
    sched = CoreAwareModelScheduler(shape_lookup=npu_shape_lookup)
    model = _resident("m1", [0])
    sched.register_loaded(model)
    assert sched.get_resident("m1") is model

    removed = sched.mark_unloaded("m1")
    assert removed is model
    assert sched.get_resident("m1") is None

    # Unloading a non-existent model returns None without error.
    assert sched.mark_unloaded("ghost") is None


@pytest.mark.asyncio
async def test_residents_snapshot():
    sched = CoreAwareModelScheduler(shape_lookup=npu_shape_lookup)
    sched.register_loaded(_resident("a", [0]))
    sched.register_loaded(_resident("b", [1]))
    ids = {m.model_id for m in sched.residents()}
    assert ids == {"a", "b"}


# ---------------------------------------------------------------------------
# BackendEntry.get_resource_shape integration
# ---------------------------------------------------------------------------

def test_backend_entry_get_resource_shape():
    """BackendEntry.get_resource_shape returns the right shape for rkllama."""
    from tinyagentos.scheduler.backend_catalog import BackendEntry

    entry = BackendEntry(
        name="npu-rkllama",
        type="rkllama",
        url="http://localhost:8080",
        status="ok",
        capabilities={"llm-chat"},
        models=[],
        priority=1,
    )
    shape = entry.get_resource_shape()
    assert shape.has_cores()
    assert shape.cores == [0, 1, 2]


def test_backend_entry_cpu_shape_no_cores():
    """BackendEntry.get_resource_shape returns a no-core shape for llama-cpp."""
    from tinyagentos.scheduler.backend_catalog import BackendEntry

    entry = BackendEntry(
        name="cpu-llama",
        type="llama-cpp",
        url="http://localhost:8080",
        status="ok",
        capabilities={"llm-chat"},
        models=[],
        priority=10,
    )
    shape = entry.get_resource_shape()
    assert not shape.has_cores()


# ---------------------------------------------------------------------------
# resource_shape module unit tests
# ---------------------------------------------------------------------------

class TestResourceShapeFactories:
    def test_rk3588_shape_has_three_cores(self):
        s = make_rk3588_npu_shape()
        assert s.has_cores()
        assert s.available_core_count() == 3
        assert s.cores == [0, 1, 2]

    def test_cuda_shape_no_npu_cores(self):
        s = make_cuda_shape(gpu_count=2)
        assert not s.has_cores()
        assert s.has_gpu_ids()
        assert s.gpu_ids == [0, 1]

    def test_cpu_shape_no_cores_no_gpus(self):
        s = make_cpu_shape()
        assert not s.has_cores()
        assert not s.has_gpu_ids()

    def test_to_dict(self):
        s = make_rk3588_npu_shape(memory_mb=6000)
        d = s.to_dict()
        assert d["cores"] == [0, 1, 2]
        assert d["memory_mb"] == 6000
        assert d["gpu_ids"] is None


# ---------------------------------------------------------------------------
# loaded_model module unit tests
# ---------------------------------------------------------------------------

class TestLoadedModel:
    def test_cores_held(self):
        m = LoadedModel("m", "rkllama", 512, resource_holds={"cores": [0, 1]}, tp_mode="0,1")
        assert m.cores_held() == [0, 1]

    def test_effective_priority_rank_always_resident(self):
        m = LoadedModel("m", "rkllama", 512, pinned=True)
        assert m.effective_priority_rank() == -1

    def test_effective_priority_rank_background(self):
        m = LoadedModel("m", "rkllama", 512, priority=PriorityClass.BACKGROUND)
        assert m.effective_priority_rank() == 2

    def test_touch_updates_last_used(self):
        import time
        m = LoadedModel("m", "rkllama", 512)
        old = m.last_used_at
        time.sleep(0.01)
        m.touch()
        assert m.last_used_at > old

    def test_is_always_resident_via_field(self):
        m = LoadedModel("m", "rkllama", 512, priority=PriorityClass.ALWAYS_RESIDENT)
        assert m.is_always_resident()

    def test_to_dict_shape(self):
        m = LoadedModel("m", "rkllama", 1024, resource_holds={"cores": [0]}, tp_mode="0")
        d = m.to_dict()
        assert d["model_id"] == "m"
        assert d["tp_mode"] == "0"
        assert d["resource_holds"] == {"cores": [0]}

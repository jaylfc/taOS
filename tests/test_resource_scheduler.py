"""Unit tests for the resource scheduler Phase 1 core.

Covers:
- Task type + payload contract
- Resource admission (capability, signature, concurrency, memory)
- Scheduler dispatch order (preferred resources walked in sequence)
- Fallback routing when the primary resource can't admit
- NoResourceAvailableError when nothing matches
- Task record + stats observability
- BackendCatalog caching + capability lookup
"""
from __future__ import annotations

import asyncio

import pytest

from tinyagentos.scheduler import (
    BackendCatalog,
    Capability,
    NoResourceAvailableError,
    Priority,
    Resource,
    ResourceRef,
    ResourceSignature,
    Scheduler,
    Task,
)
from tinyagentos.scheduler.resource import Tier


def _make_resource(
    name: str,
    *,
    capabilities: set[str],
    concurrency: int = 1,
    platform: str = "test",
    runtime: str = "fake",
    runtime_version: str = "1.0",
    memory_mb: int = 999_999,
    backend_url: str = "http://backend",
    tier: int = Tier.CPU,
    potential_capabilities: set[str] | None = None,
    score_lookup=None,
) -> Resource:
    return Resource(
        name=name,
        signature=ResourceSignature(platform, runtime, runtime_version),
        concurrency=concurrency,
        tier=tier,
        potential_capabilities=potential_capabilities,
        get_capabilities=lambda caps=capabilities: set(caps),
        backend_lookup=lambda cap, url=backend_url: url,
        score_lookup=score_lookup,
        memory_probe=lambda mb=memory_mb: mb,
    )


def _make_task(
    capability: Capability,
    payload,
    *,
    preferred: list[str],
    memory_mb: int = 0,
    priority: Priority = Priority.INTERACTIVE_AGENT,
    required_signatures: list[ResourceSignature] | None = None,
) -> Task:
    return Task(
        capability=capability,
        payload=payload,
        preferred_resources=[ResourceRef(name) for name in preferred],
        priority=priority,
        estimated_memory_mb=memory_mb,
        required_signatures=required_signatures or [],
        submitter="test",
    )


@pytest.mark.asyncio
async def test_scheduler_dispatches_to_first_admitted_resource():
    """Task runs on the first preferred resource that passes admission."""
    calls: list[str] = []

    async def payload(resource: Resource) -> str:
        calls.append(resource.name)
        return "ok"

    sched = Scheduler()
    sched.register(_make_resource("npu", capabilities={"image-generation"}))
    sched.register(_make_resource("cpu", capabilities={"image-generation"}))

    task = _make_task(Capability.IMAGE_GENERATION, payload, preferred=["npu", "cpu"])
    result = await sched.submit(task)

    assert result == "ok"
    assert calls == ["npu"]  # first preference took it
    stats = sched.stats()
    assert stats["submitted"] == 1
    assert stats["completed"] == 1
    assert stats["errors"] == 0
    assert stats["rejected"] == 0


@pytest.mark.asyncio
async def test_scheduler_falls_back_when_capability_missing_on_primary():
    """If the primary resource doesn't serve the capability, the next one does."""
    calls: list[str] = []

    async def payload(resource: Resource) -> str:
        calls.append(resource.name)
        return "ok"

    sched = Scheduler()
    # npu only does embeddings, cpu does image-gen — task asks for image-gen
    sched.register(_make_resource("npu", capabilities={"embedding"}))
    sched.register(_make_resource("cpu", capabilities={"image-generation"}))

    task = _make_task(Capability.IMAGE_GENERATION, payload, preferred=["npu", "cpu"])
    await sched.submit(task)

    assert calls == ["cpu"]


@pytest.mark.asyncio
async def test_scheduler_required_signature_routes_to_matching_resource():
    """A task with a required signature lands on the resource that matches."""
    calls: list[str] = []

    async def payload(resource: Resource) -> str:
        calls.append(resource.name)
        return "ok"

    sched = Scheduler()
    sched.register(
        _make_resource(
            "npu-old",
            capabilities={"image-generation"},
            platform="rk3588",
            runtime="librknnrt",
            runtime_version="2.2.0",
        )
    )
    sched.register(
        _make_resource(
            "npu-new",
            capabilities={"image-generation"},
            platform="rk3588",
            runtime="librknnrt",
            runtime_version="2.3.0",
        )
    )

    # Model compiled for 2.3.x
    task = _make_task(
        Capability.IMAGE_GENERATION,
        payload,
        preferred=["npu-old", "npu-new"],
        required_signatures=[
            ResourceSignature("rk3588", "librknnrt", "2.3"),
        ],
    )
    await sched.submit(task)

    # npu-old (2.2.0) rejected; npu-new (2.3.0) matches "2.3" prefix
    assert calls == ["npu-new"]


@pytest.mark.asyncio
async def test_scheduler_falls_back_when_primary_is_full():
    """Concurrency cap on the primary resource forces fallback."""
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_payload(resource: Resource) -> str:
        started.set()
        await release.wait()
        return "slow"

    async def fast_payload(resource: Resource) -> str:
        return f"fast:{resource.name}"

    sched = Scheduler()
    sched.register(_make_resource("npu", capabilities={"image-generation"}, concurrency=1))
    sched.register(_make_resource("cpu", capabilities={"image-generation"}, concurrency=2))

    slow_task = _make_task(Capability.IMAGE_GENERATION, slow_payload, preferred=["npu", "cpu"])
    slow_future = asyncio.create_task(sched.submit(slow_task))
    await started.wait()

    fast_task = _make_task(Capability.IMAGE_GENERATION, fast_payload, preferred=["npu", "cpu"])
    fast_result = await sched.submit(fast_task)
    assert fast_result == "fast:cpu"  # NPU was full, fell to CPU

    release.set()
    slow_result = await slow_future
    assert slow_result == "slow"


@pytest.mark.asyncio
async def test_scheduler_raises_when_no_resource_can_admit():
    """With no matching resource, NoResourceAvailableError is raised."""
    sched = Scheduler()
    sched.register(_make_resource("cpu", capabilities={"embedding"}))

    async def payload(resource: Resource) -> str:  # pragma: no cover - not reached
        return "impossible"

    task = _make_task(Capability.IMAGE_GENERATION, payload, preferred=["cpu"])
    with pytest.raises(NoResourceAvailableError):
        await sched.submit(task)

    stats = sched.stats()
    assert stats["rejected"] == 1
    assert stats["completed"] == 0


@pytest.mark.asyncio
async def test_scheduler_records_task_history():
    """Completed and rejected tasks both land in the history with full context."""
    sched = Scheduler()
    sched.register(_make_resource("cpu", capabilities={"image-generation"}))

    async def ok_payload(resource: Resource) -> str:
        return "done"

    task = _make_task(Capability.IMAGE_GENERATION, ok_payload, preferred=["cpu"])
    await sched.submit(task)

    history = sched.history(limit=10)
    assert len(history) == 1
    rec = history[0]
    assert rec.capability == "image-generation"
    assert rec.resource == "cpu"
    assert rec.status.value == "complete"
    assert rec.elapsed_seconds is not None
    assert rec.elapsed_seconds >= 0


@pytest.mark.asyncio
async def test_scheduler_propagates_payload_exceptions():
    """Exceptions from the payload reach the caller and are recorded as errors."""
    sched = Scheduler()
    sched.register(_make_resource("cpu", capabilities={"image-generation"}))

    async def boom(resource: Resource):
        raise RuntimeError("boom")

    task = _make_task(Capability.IMAGE_GENERATION, boom, preferred=["cpu"])
    with pytest.raises(RuntimeError, match="boom"):
        await sched.submit(task)

    stats = sched.stats()
    assert stats["errors"] == 1
    history = sched.history(limit=1)
    assert history[0].status.value == "error"
    assert "boom" in history[0].error


@pytest.mark.asyncio
async def test_auto_route_prefers_lower_tier():
    """With no explicit preferred_resources, scheduler picks by tier."""
    calls: list[str] = []

    async def payload(resource: Resource) -> str:
        calls.append(resource.name)
        return "ok"

    sched = Scheduler()
    sched.register(
        _make_resource("cpu", capabilities={"image-generation"}, tier=Tier.CPU)
    )
    sched.register(
        _make_resource("npu", capabilities={"image-generation"}, tier=Tier.NPU)
    )
    sched.register(
        _make_resource("gpu", capabilities={"image-generation"}, tier=Tier.GPU)
    )

    # No preferred_resources → scheduler auto-routes by tier
    task = Task(
        capability=Capability.IMAGE_GENERATION,
        payload=payload,
        preferred_resources=[],  # auto-route
        priority=Priority.INTERACTIVE_USER,
        submitter="test",
    )
    await sched.submit(task)

    assert calls == ["gpu"]  # tier 0 wins over NPU (1) and CPU (2)


@pytest.mark.asyncio
async def test_auto_route_uses_benchmark_score_as_tiebreaker():
    """Within a tier, higher benchmark score wins."""
    calls: list[str] = []

    async def payload(resource: Resource) -> str:
        calls.append(resource.name)
        return "ok"

    # Both resources are CPU tier, but one has a higher benchmark score
    def fast_scorer(capability: str, model):
        return 100.0  # fast

    def slow_scorer(capability: str, model):
        return 10.0  # slow

    sched = Scheduler()
    sched.register(
        _make_resource(
            "cpu-pi4",
            capabilities={"embedding"},
            tier=Tier.CPU,
            score_lookup=slow_scorer,
        )
    )
    sched.register(
        _make_resource(
            "cpu-fedora",
            capabilities={"embedding"},
            tier=Tier.CPU,
            score_lookup=fast_scorer,
        )
    )

    task = Task(
        capability=Capability.EMBEDDING,
        payload=payload,
        preferred_resources=[],
        priority=Priority.INTERACTIVE_USER,
        submitter="test",
    )
    await sched.submit(task)
    assert calls == ["cpu-fedora"]  # higher score wins the tie


@pytest.mark.asyncio
async def test_explicit_preferred_resources_override_auto_route():
    """An explicit preferred_resources list ignores the tier ranking."""
    calls: list[str] = []

    async def payload(resource: Resource) -> str:
        calls.append(resource.name)
        return "ok"

    sched = Scheduler()
    sched.register(
        _make_resource("gpu", capabilities={"image-generation"}, tier=Tier.GPU)
    )
    sched.register(
        _make_resource("cpu", capabilities={"image-generation"}, tier=Tier.CPU)
    )

    # Caller wants CPU specifically — auto-route would pick GPU
    task = Task(
        capability=Capability.IMAGE_GENERATION,
        payload=payload,
        preferred_resources=[ResourceRef("cpu")],
        priority=Priority.INTERACTIVE_USER,
        submitter="test",
    )
    await sched.submit(task)
    assert calls == ["cpu"]


def test_resource_potential_capabilities():
    """Resource exposes both current and potential capability sets."""
    r = _make_resource(
        "cpu",
        capabilities={"image-generation"},
        potential_capabilities={"llm-chat", "embedding", "image-generation", "speech-to-text"},
    )
    # Current is what the backend catalog says right now
    assert r.capabilities == {"image-generation"}
    # Potential is the static hardware-class set, unioned with current
    assert "llm-chat" in r.potential_capabilities
    assert "embedding" in r.potential_capabilities
    assert "speech-to-text" in r.potential_capabilities
    assert "image-generation" in r.potential_capabilities


@pytest.mark.asyncio
async def test_backend_catalog_capability_routing():
    """Catalog correctly surfaces capabilities from live probe results."""
    backends = [
        {"name": "a", "type": "sd-cpp", "url": "http://a", "priority": 4},
        {"name": "b", "type": "sd-cpp", "url": "http://b", "priority": 5},
    ]

    async def probe(backend: dict) -> dict:
        return {
            "status": "ok",
            "response_ms": 1,
            "models": [
                {"name": "dreamshaper-8-lcm-q4" if backend["name"] == "a"
                    else "dreamshaper-8-lcm-iq4_nl-gguf"}
            ],
        }

    catalog = BackendCatalog(backends=backends, probe_fn=probe, interval_seconds=3600)
    await catalog.start()
    try:
        entries = catalog.backends_with_capability("image-generation")
        assert [e.name for e in entries] == ["a", "b"]  # priority order

        # Fuzzy model lookup
        npu = catalog.find_backend_for_model("image-generation", "dreamshaper-8-lcm-q4")
        assert npu is not None
        assert npu.type == "sd-cpp"

        cpu = catalog.find_backend_for_model("image-generation", "dreamshaper-8-lcm")
        assert cpu is not None
        assert cpu.type == "sd-cpp"

        # Unknown model still returns the highest-priority capable backend
        any_bg = catalog.find_backend_for_model("image-generation", "unknown-model")
        assert any_bg is not None  # falls back to capability-only
    finally:
        await catalog.stop()


@pytest.mark.asyncio
async def test_backend_catalog_marks_unhealthy_stale():
    """A backend that starts healthy then fails is marked stale, not dropped."""
    state = {"healthy": True}
    backends = [{"name": "a", "type": "sd-cpp", "url": "http://a", "priority": 1}]

    async def probe(backend: dict) -> dict:
        if state["healthy"]:
            return {"status": "ok", "response_ms": 1, "models": [{"name": "m1"}]}
        raise RuntimeError("down")

    catalog = BackendCatalog(
        backends=backends,
        probe_fn=probe,
        interval_seconds=3600,
        stale_after_seconds=3600,  # long grace period
    )
    await catalog.start()
    try:
        assert catalog.backends_with_capability("image-generation")
        state["healthy"] = False
        await catalog.refresh()
        entries = catalog.backends()
        assert len(entries) == 1
        assert entries[0].status == "stale"
        # Models preserved from last-known state
        assert entries[0].models == [{"name": "m1"}]
    finally:
        await catalog.stop()

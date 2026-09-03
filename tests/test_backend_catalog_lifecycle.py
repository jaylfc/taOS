from __future__ import annotations
import asyncio
import pytest
from tinyagentos.scheduler.backend_catalog import (
    BACKEND_CAPABILITIES,
    BackendCatalog,
    BackendEntry,
)


def test_hailo_ollama_capability_entry_exists():
    """Hailo-10H backend claims llm-chat only in v1 (design slice S1)."""
    assert "hailo-ollama" in BACKEND_CAPABILITIES
    assert BACKEND_CAPABILITIES["hailo-ollama"] == {"llm-chat"}


@pytest.mark.asyncio
async def test_disabled_backend_excluded_from_routing():
    """A backend with enabled=False must not appear in backends_with_capability."""
    async def probe(backend: dict) -> dict:
        return {"status": "ok", "response_ms": 1, "models": []}

    backends = [
        {"name": "b1", "type": "rkllama", "url": "http://b1", "priority": 1, "enabled": False},
        {"name": "b2", "type": "rkllama", "url": "http://b2", "priority": 2, "enabled": True},
    ]
    catalog = BackendCatalog(backends=backends, probe_fn=probe, interval_seconds=3600)
    await catalog.start()
    await catalog.wait_initial_probe()
    try:
        results = catalog.backends_with_capability("llm-chat")
        assert len(results) == 1
        assert results[0].name == "b2"
    finally:
        await catalog.stop()


@pytest.mark.asyncio
async def test_lifecycle_state_in_to_dict():
    """BackendEntry.to_dict() must include lifecycle fields."""
    async def probe(backend: dict) -> dict:
        return {"status": "ok", "response_ms": 5, "models": []}

    backends = [
        {
            "name": "b1", "type": "rkllama", "url": "http://b1", "priority": 1,
            "enabled": True, "auto_manage": True, "keep_alive_minutes": 10,
        }
    ]
    catalog = BackendCatalog(backends=backends, probe_fn=probe, interval_seconds=3600)
    await catalog.start()
    await catalog.wait_initial_probe()
    try:
        entries = catalog.backends()
        assert len(entries) == 1
        d = entries[0].to_dict()
        assert d["lifecycle_state"] == "running"
        assert d["auto_manage"] is True
        assert d["keep_alive_minutes"] == 10
        assert d["enabled"] is True
    finally:
        await catalog.stop()


@pytest.mark.asyncio
async def test_stopped_backend_in_backends_startable():
    """A stopped+auto_manage backend appears in backends_startable_for_capability."""
    async def probe(backend: dict) -> dict:
        return {"status": "error", "response_ms": 0, "models": []}

    backends = [
        {
            "name": "b1", "type": "sd-cpp", "url": "http://b1", "priority": 1,
            "enabled": True, "auto_manage": True, "keep_alive_minutes": 10,
        }
    ]
    catalog = BackendCatalog(backends=backends, probe_fn=probe, interval_seconds=3600)
    catalog._lifecycle_states["b1"] = "stopped"
    await catalog.start()
    await catalog.wait_initial_probe()
    try:
        startable = catalog.backends_startable_for_capability("image-generation")
        assert len(startable) == 1
        assert startable[0].name == "b1"
    finally:
        await catalog.stop()


@pytest.mark.asyncio
async def test_backends_startable_cold_start():
    """A stopped+auto_manage backend with no probe entry (cold start) must still
    appear in backends_startable_for_capability via a synthetic BackendEntry."""
    probed = []

    async def probe(backend: dict) -> dict:
        probed.append(backend["name"])
        # Simulate a backend that has never been reachable — no entry will
        # exist in _entries after the first poll.
        return {"status": "error", "response_ms": 0, "models": []}

    backends = [
        {
            "name": "cold-sd", "type": "sd-cpp", "url": "http://cold-sd",
            "priority": 1, "enabled": True, "auto_manage": True, "keep_alive_minutes": 5,
        }
    ]
    catalog = BackendCatalog(backends=backends, probe_fn=probe, interval_seconds=3600)
    # Mark stopped BEFORE start so the poll sees the state but the probe fails
    catalog._lifecycle_states["cold-sd"] = "stopped"
    await catalog.start()
    await catalog.wait_initial_probe()
    try:
        startable = catalog.backends_startable_for_capability("image-generation")
        assert len(startable) == 1
        entry = startable[0]
        assert entry.name == "cold-sd"
        assert entry.lifecycle_state == "stopped"
        assert entry.auto_manage is True
        assert entry.keep_alive_minutes == 5
    finally:
        await catalog.stop()


@pytest.mark.asyncio
async def test_set_and_get_lifecycle_state():
    """set_lifecycle_state and get_lifecycle_state round-trip correctly."""
    async def probe(backend: dict) -> dict:
        return {"status": "ok", "response_ms": 1, "models": []}

    backends = [{"name": "b1", "type": "rkllama", "url": "http://b1", "priority": 1}]
    catalog = BackendCatalog(backends=backends, probe_fn=probe, interval_seconds=3600)
    await catalog.start()
    await catalog.wait_initial_probe()
    try:
        assert catalog.get_lifecycle_state("b1") == "running"
        catalog.set_lifecycle_state("b1", "stopped")
        assert catalog.get_lifecycle_state("b1") == "stopped"
    finally:
        await catalog.stop()


@pytest.mark.asyncio
async def test_start_does_not_block_on_unreachable_backends():
    """tsk-xjwolt: BackendCatalog.start() must return promptly even when
    every configured backend's probe is slow/unreachable. The previous
    implementation awaited the first probe pass with a 15s cap, which
    meant a single :6969 boot could take the full per-backend connect
    timeout (measured at 100s+ on a Pi 4 with three unreachable local
    model-backend URLs). After the fix, the main app serves requests
    while the catalog reconciles in the background.
    """
    import time

    # Each probe sleeps for a very long time. If start() blocked on the
    # initial probe, this would take the same wall-clock to return. The
    # bound is generous on purpose (1.0s) to avoid flakiness while still
    # being a few orders of magnitude smaller than the old 100s+ boot.
    SLOW_PROBE_SECONDS = 30.0
    TIGHT_BOUND_SECONDS = 1.0

    async def slow_probe(backend: dict) -> dict:
        await asyncio.sleep(SLOW_PROBE_SECONDS)
        return {"status": "ok", "response_ms": int(SLOW_PROBE_SECONDS * 1000), "models": []}

    backends = [
        {"name": f"slow-{i}", "type": "ollama", "url": f"http://10.255.255.{i+1}:11434",
         "priority": i + 1, "enabled": True}
        for i in range(3)
    ]
    catalog = BackendCatalog(
        backends=backends, probe_fn=slow_probe, interval_seconds=3600,
    )

    t0 = time.monotonic()
    await catalog.start()
    elapsed = time.monotonic() - t0
    assert elapsed < TIGHT_BOUND_SECONDS, (
        f"start() took {elapsed:.2f}s with 3 unreachable backends; "
        f"expected < {TIGHT_BOUND_SECONDS}s (tsk-xjwolt)"
    )
    # First probe hasn't completed yet, so backends() is empty.
    assert catalog.backends() == []
    # The poll task is still running in the background.
    assert catalog._task is not None
    await catalog.stop()

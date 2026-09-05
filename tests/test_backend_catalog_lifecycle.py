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


@pytest.mark.asyncio
async def test_probe_pass_is_concurrent_not_sequential():
    """tsk-ez4cjm: one probe pass must fan the backends out in parallel.

    With N unreachable backends each burning their full connect timeout, a
    sequential pass costs N x timeout. ``_probe_all()`` gathers them, so the
    pass costs ~one timeout regardless of N. Asserted on wall clock with a
    bound only a sequential implementation can miss.
    """
    import time

    PROBE_SECONDS = 0.4
    N_BACKENDS = 6
    # Sequential would be N * PROBE_SECONDS = 2.4s; concurrent ~0.4s.
    SEQUENTIAL_FLOOR = N_BACKENDS * PROBE_SECONDS

    async def slow_probe(backend: dict) -> dict:
        await asyncio.sleep(PROBE_SECONDS)
        return {"status": "ok", "response_ms": 1, "models": []}

    backends = [
        {"name": f"b{i}", "type": "ollama", "url": f"http://b{i}", "priority": i}
        for i in range(N_BACKENDS)
    ]
    catalog = BackendCatalog(
        backends=backends, probe_fn=slow_probe, interval_seconds=3600
    )
    t0 = time.monotonic()
    await catalog.refresh()
    elapsed = time.monotonic() - t0
    assert len(catalog.backends()) == N_BACKENDS
    assert elapsed < SEQUENTIAL_FLOOR / 2, (
        f"probe pass over {N_BACKENDS} x {PROBE_SECONDS}s backends took "
        f"{elapsed:.2f}s; a concurrent pass should cost ~{PROBE_SECONDS}s, "
        f"a sequential one ~{SEQUENTIAL_FLOOR:.1f}s"
    )


@pytest.mark.asyncio
async def test_stop_releases_in_flight_initial_probe_waiter():
    """``stop()`` must release callers parked in ``wait_initial_probe()``.

    The poll task that would set the barrier is cancelled by ``stop()``, so
    a waiter holding the pre-stop Event can never be woken by a probe.
    ``stop()`` sets that Event before installing a fresh one; without that
    the waiter hangs for its whole timeout (forever, when untimed).
    """
    probe_entered = asyncio.Event()

    async def never_finishing_probe(backend: dict) -> dict:
        probe_entered.set()
        await asyncio.sleep(3600)
        return {"status": "ok", "response_ms": 1, "models": []}

    backends = [{"name": "b1", "type": "ollama", "url": "http://b1", "priority": 1}]
    catalog = BackendCatalog(
        backends=backends, probe_fn=never_finishing_probe, interval_seconds=3600
    )
    await catalog.start()
    waiter = asyncio.create_task(catalog.wait_initial_probe())
    await asyncio.wait_for(probe_entered.wait(), timeout=5.0)
    assert not waiter.done()

    await catalog.stop()
    # Released promptly rather than left hanging on the discarded Event.
    await asyncio.wait_for(waiter, timeout=5.0)
    # A fresh barrier is installed so the next start() waits on a real probe.
    assert not catalog._initial_probe_done.is_set()


@pytest.mark.asyncio
async def test_subscriber_registered_before_start_sees_first_probe():
    """The app registers its lifecycle reconcile subscriber BEFORE start().

    start() no longer blocks on the first probe, so the reconcile that used
    to run inline after start() is now a one-shot subscriber. Registering it
    after start() would race the first probe pass; registering it before
    must deliver that pass. Mirrors the create_app() lifespan wiring.
    """
    async def probe(backend: dict) -> dict:
        # Unreachable auto-managed backend — exactly the boot-time case.
        return {"status": "error", "error": "connect refused", "models": []}

    backends = [
        {
            "name": "auto1",
            "type": "sd-cpp",
            "url": "http://auto1",
            "priority": 1,
            "auto_manage": True,
        }
    ]
    catalog = BackendCatalog(backends=backends, probe_fn=probe, interval_seconds=3600)

    reconciled = {"done": False}

    async def reconcile() -> None:
        if reconciled["done"]:
            return
        reconciled["done"] = True
        for entry in catalog.backends():
            if entry.auto_manage and entry.status != "ok":
                catalog.set_lifecycle_state(entry.name, "stopped")

    catalog.subscribe(reconcile)
    await catalog.start()
    try:
        await catalog.wait_initial_probe(timeout=5.0)
        # Subscribers fire inside the probe pass, before the barrier is set.
        assert reconciled["done"] is True
        assert catalog.get_lifecycle_state("auto1") == "stopped"
    finally:
        await catalog.stop()

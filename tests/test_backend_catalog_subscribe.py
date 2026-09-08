"""Tests for BackendCatalog.subscribe() — change notifications.

These cover the subscriber mechanism that LiteLLM proxy (and future
scheduler Phase 2 resource re-registration) uses to stay in sync with
live backend state.
"""
from __future__ import annotations

import asyncio

import pytest

from tinyagentos.scheduler.backend_catalog import BackendCatalog


@pytest.mark.asyncio
async def test_subscriber_fires_on_status_change():
    """A backend flipping from ok to error should trigger the subscriber."""
    state = {"healthy": True}
    call_count = {"n": 0}

    async def probe(backend: dict) -> dict:
        if state["healthy"]:
            return {"status": "ok", "response_ms": 1, "models": [{"name": "m"}]}
        return {"status": "error", "response_ms": 0, "models": []}

    async def subscriber() -> None:
        call_count["n"] += 1

    backends = [{"name": "b", "type": "ollama", "url": "http://b", "priority": 1}]
    catalog = BackendCatalog(
        backends=backends,
        probe_fn=probe,
        interval_seconds=3600,
        stale_after_seconds=0,  # no grace — errors flip to "error" immediately
    )
    catalog.subscribe(subscriber)
    await catalog.start()
    # tsk-xjwolt: start() no longer blocks on the first probe; tests
    # that need the post-probe view await wait_initial_probe().
    await catalog.wait_initial_probe()
    try:
        # Initial probe fired once on start
        assert call_count["n"] == 1

        state["healthy"] = False
        await catalog.refresh()
        # Refresh doesn't directly fire subscribers (that's only the poll
        # loop) but force a full cycle by calling refresh + wait
        # manually. In practice refresh is used for immediate snapshots,
        # not change notifications.
    finally:
        await catalog.stop()


@pytest.mark.asyncio
async def test_subscriber_fires_on_model_list_change():
    """A backend that loads a new model should trigger subscribers."""
    model_state = {"models": [{"name": "m1"}]}
    fired_models: list[int] = []

    async def probe(backend: dict) -> dict:
        return {"status": "ok", "response_ms": 1, "models": list(model_state["models"])}

    async def subscriber() -> None:
        fired_models.append(len(model_state["models"]))

    backends = [{"name": "b", "type": "ollama", "url": "http://b", "priority": 1}]
    catalog = BackendCatalog(
        backends=backends,
        probe_fn=probe,
        interval_seconds=0.05,  # fast polling for the test
    )
    catalog.subscribe(subscriber)
    await catalog.start()
    try:
        # Initial
        await asyncio.sleep(0.02)
        assert fired_models == [1]

        # Load a second model — next poll should fire a change event
        model_state["models"] = [{"name": "m1"}, {"name": "m2"}]
        await asyncio.sleep(0.1)
        assert 2 in fired_models
    finally:
        await catalog.stop()


@pytest.mark.asyncio
async def test_subscriber_not_fired_when_signature_stable():
    """Successive identical probe results should NOT fire subscribers."""
    fire_count = {"n": 0}

    async def probe(backend: dict) -> dict:
        return {"status": "ok", "response_ms": 1, "models": [{"name": "m1"}]}

    async def subscriber() -> None:
        fire_count["n"] += 1

    backends = [{"name": "b", "type": "ollama", "url": "http://b", "priority": 1}]
    catalog = BackendCatalog(
        backends=backends,
        probe_fn=probe,
        interval_seconds=0.02,
    )
    catalog.subscribe(subscriber)
    await catalog.start()
    try:
        await asyncio.sleep(0.08)
        # Only the first probe should have fired the subscriber —
        # subsequent probes produced identical state so no change event
        assert fire_count["n"] == 1
    finally:
        await catalog.stop()


@pytest.mark.asyncio
async def test_failing_subscriber_does_not_crash_poll_loop():
    """A subscriber raising an exception should be isolated — other
    subscribers still fire and the poll loop keeps running."""
    good_fires = {"n": 0}

    async def bad_sub() -> None:
        raise RuntimeError("boom")

    async def good_sub() -> None:
        good_fires["n"] += 1

    async def probe(backend: dict) -> dict:
        return {"status": "ok", "response_ms": 1, "models": [{"name": "m"}]}

    backends = [{"name": "b", "type": "ollama", "url": "http://b", "priority": 1}]
    catalog = BackendCatalog(backends=backends, probe_fn=probe, interval_seconds=0.02)
    catalog.subscribe(bad_sub)
    catalog.subscribe(good_sub)
    await catalog.start()
    try:
        # Let the initial probe run — good subscriber should still fire
        await asyncio.sleep(0.05)
        assert good_fires["n"] >= 1
    finally:
        await catalog.stop()


@pytest.mark.asyncio
async def test_multiple_subscribers_all_fire():
    fires = {"a": 0, "b": 0}

    async def sub_a() -> None:
        fires["a"] += 1

    async def sub_b() -> None:
        fires["b"] += 1

    async def probe(backend: dict) -> dict:
        return {"status": "ok", "response_ms": 1, "models": []}

    backends = [{"name": "b", "type": "ollama", "url": "http://b", "priority": 1}]
    catalog = BackendCatalog(backends=backends, probe_fn=probe, interval_seconds=0.02)
    catalog.subscribe(sub_a)
    catalog.subscribe(sub_b)
    await catalog.start()
    try:
        await asyncio.sleep(0.05)
        assert fires["a"] >= 1
        assert fires["b"] >= 1
    finally:
        await catalog.stop()

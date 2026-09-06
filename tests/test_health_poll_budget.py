"""Tests that the health monitor's poll loop has a hard time budget.

Issue #323: a single hung backend probe could lock the event loop. Each call
already has its own timeout, but `_poll_once` runs many of them sequentially
plus a qmd probe and metrics writes. The wait_for budget is the last line of
defence.
"""
from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from tinyagentos.health import HealthMonitor


class _CapturingHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


def _make_monitor(poll_interval: int = 30) -> HealthMonitor:
    config = MagicMock()
    config.metrics = {"poll_interval": poll_interval}
    config.backends = []
    config.agents = []
    metrics = MagicMock()
    metrics.insert = AsyncMock()
    metrics.cleanup = AsyncMock(return_value=0)
    qmd = MagicMock()
    qmd.health = AsyncMock(return_value={"status": "ok", "response_ms": 5})
    return HealthMonitor(config, metrics, qmd, http_client=MagicMock())


@pytest.mark.asyncio
async def test_poll_loop_aborts_when_poll_once_hangs():
    """A wedged _poll_once must not block the loop forever — wait_for kicks in."""
    monitor = _make_monitor()
    monitor._poll_budget_seconds = 0.05

    async def hang_forever() -> None:
        await asyncio.sleep(3600)

    monitor._poll_once = hang_forever  # type: ignore[method-assign]

    handler = _CapturingHandler()
    health_logger = logging.getLogger("tinyagentos.health")
    health_logger.addHandler(handler)
    health_logger.setLevel(logging.DEBUG)
    try:
        task = asyncio.create_task(monitor._poll_loop())
        await asyncio.sleep(0.2)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    finally:
        health_logger.removeHandler(handler)

    assert any(
        "Health poll exceeded" in m and "budget" in m for m in handler.messages
    ), f"Expected timeout abort log; got: {handler.messages}"


@pytest.mark.asyncio
async def test_poll_loop_continues_after_timeout_abort():
    """After a TimeoutError, the loop must keep running (not exit)."""
    monitor = _make_monitor(poll_interval=0)  # no inter-cycle delay
    monitor._poll_budget_seconds = 0.05
    call_count = {"n": 0}

    async def slow_then_ok() -> None:
        call_count["n"] += 1
        if call_count["n"] == 1:
            await asyncio.sleep(3600)

    monitor._poll_once = slow_then_ok  # type: ignore[method-assign]

    task = asyncio.create_task(monitor._poll_loop())
    await asyncio.sleep(0.2)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert call_count["n"] >= 2, "loop did not iterate past the timeout"

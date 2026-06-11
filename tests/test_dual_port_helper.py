"""Tests for _serve_until_first_exit helper in tinyagentos.__main__."""
from __future__ import annotations

import asyncio

import pytest


class _StubServer:
    """Minimal stand-in for uvicorn.Server."""

    def __init__(self, *, started: bool = True, raise_on_serve: Exception | None = None):
        self.started = started
        self._raise = raise_on_serve
        self._cancelled = False

    async def serve(self) -> None:
        await asyncio.sleep(0)  # yield once to let the other task run
        if self._raise is not None:
            raise self._raise


class _HangingServer:
    """Server whose serve() never returns (simulates a running server)."""

    def __init__(self, *, started: bool = True):
        self.started = started
        self._task: asyncio.Task | None = None

    async def serve(self) -> None:
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            raise


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_main_fails_to_start_returns_false():
    """When main server exits with started=False, helper returns False.

    The proxy stub's serve task must be cancelled.
    """
    from tinyagentos.__main__ import _serve_until_first_exit

    main_server = _StubServer(started=False)
    proxy_server = _HangingServer(started=True)

    result = await _serve_until_first_exit(main_server, proxy_server)

    assert result is False


@pytest.mark.asyncio
async def test_main_starts_and_proxy_exits_returns_true():
    """When proxy exits first but main started=True, helper returns True."""
    from tinyagentos.__main__ import _serve_until_first_exit

    main_server = _HangingServer(started=True)
    proxy_server = _StubServer(started=True)

    result = await _serve_until_first_exit(main_server, proxy_server)

    assert result is True


@pytest.mark.asyncio
async def test_both_exit_main_started_returns_true():
    """Both servers exit; main.started=True -> returns True."""
    from tinyagentos.__main__ import _serve_until_first_exit

    main_server = _StubServer(started=True)
    proxy_server = _StubServer(started=True)

    result = await _serve_until_first_exit(main_server, proxy_server)

    assert result is True


@pytest.mark.asyncio
async def test_serve_exception_is_reraised():
    """An exception raised inside serve() propagates out of the helper."""
    from tinyagentos.__main__ import _serve_until_first_exit

    boom = RuntimeError("lifespan crash")
    main_server = _StubServer(started=False, raise_on_serve=boom)
    proxy_server = _HangingServer(started=True)

    with pytest.raises(RuntimeError, match="lifespan crash"):
        await _serve_until_first_exit(main_server, proxy_server)

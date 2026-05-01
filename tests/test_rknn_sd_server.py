"""Tests for rknn_sd_server lifecycle: idle-unload bookkeeping and /admin/unload.

These tests do NOT import the real RKNN runtime, diffusers, or transformers.
_pipe is injected as a plain sentinel object so we can verify the lifecycle
state machine around it without touching NPU hardware.
"""
import asyncio
import time
import pytest
import pytest_asyncio
from unittest.mock import MagicMock, patch
from httpx import AsyncClient, ASGITransport

import tinyagentos.services.rknn_sd_server as srv


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reset_server_state(pipe_value=None, last_activity=0.0, load_error=None):
    """Reset module-level globals to a known state for each test."""
    srv._pipe = pipe_value
    srv._last_activity_ts = last_activity
    srv._load_error = load_error


# ---------------------------------------------------------------------------
# Unit tests — _ensure_pipeline_sync activity timestamp
# ---------------------------------------------------------------------------

class TestEnsurePipelineSyncTimestamp:
    def test_loading_updates_last_activity_ts(self, monkeypatch):
        """When _ensure_pipeline_sync loads the pipeline, _last_activity_ts is set."""
        _reset_server_state(pipe_value=None, last_activity=0.0)

        fake_pipe = object()
        before = time.monotonic()

        def _fake_build():
            return fake_pipe

        monkeypatch.setattr(srv, "USE_LEGACY_WRAPPER", False)
        monkeypatch.setattr(srv, "_build_pipeline_ez", _fake_build)

        srv._ensure_pipeline_sync()

        after = time.monotonic()
        assert srv._pipe is fake_pipe
        assert srv._last_activity_ts >= before
        assert srv._last_activity_ts <= after

    def test_already_loaded_is_idempotent(self, monkeypatch):
        """If _pipe is already set, _ensure_pipeline_sync returns immediately."""
        sentinel = object()
        _reset_server_state(pipe_value=sentinel, last_activity=0.0)

        called = []
        monkeypatch.setattr(srv, "_build_pipeline_ez", lambda: called.append(1) or object())

        srv._ensure_pipeline_sync()

        assert srv._pipe is sentinel
        assert called == []
        assert srv._last_activity_ts == 0.0  # not touched


# ---------------------------------------------------------------------------
# Unit tests — _unload_pipeline
# ---------------------------------------------------------------------------

class TestUnloadPipeline:
    def test_clears_pipe(self):
        """_unload_pipeline sets _pipe to None."""
        _reset_server_state(pipe_value=object())

        srv._unload_pipeline()

        assert srv._pipe is None

    def test_no_op_when_already_unloaded(self):
        """_unload_pipeline is safe to call when nothing is loaded."""
        _reset_server_state(pipe_value=None)
        srv._unload_pipeline()  # must not raise
        assert srv._pipe is None

    def test_calls_release_on_pipe(self):
        """_unload_pipeline calls release() on the pipe object if it exists."""
        fake_pipe = MagicMock()
        _reset_server_state(pipe_value=fake_pipe)

        srv._unload_pipeline()

        fake_pipe.release.assert_called_once()

    def test_survives_release_raising(self):
        """_unload_pipeline survives a wrapper that throws on release()."""
        fake_pipe = MagicMock()
        fake_pipe.release.side_effect = RuntimeError("GPU exploded")
        _reset_server_state(pipe_value=fake_pipe)

        srv._unload_pipeline()  # must not raise

        assert srv._pipe is None

    def test_calls_close_when_no_release(self):
        """_unload_pipeline falls back to close() when release is absent."""
        fake_pipe = MagicMock(spec=["close"])  # no release attribute
        _reset_server_state(pipe_value=fake_pipe)

        srv._unload_pipeline()

        fake_pipe.close.assert_called_once()
        assert srv._pipe is None


# ---------------------------------------------------------------------------
# Unit tests — idle-unload background loop logic
# ---------------------------------------------------------------------------

class TestIdleUnloadLoop:
    @pytest.mark.asyncio
    async def test_idle_loop_unloads_after_threshold(self, monkeypatch):
        """Background loop unloads pipeline once idle time exceeds threshold."""
        fake_pipe = object()
        _reset_server_state(pipe_value=fake_pipe, last_activity=0.0)

        # Patch monotonic so the pipe looks like it's been idle for 1000s.
        # We set _last_activity_ts to 0 and make monotonic return 1000.
        monkeypatch.setattr(srv, "IDLE_UNLOAD_THRESHOLD_S", 600.0)
        monkeypatch.setattr(srv, "IDLE_UNLOAD_INTERVAL_S", 0.001)  # near-instant check

        # Fake time: always returns a value well past the threshold
        monkeypatch.setattr(time, "monotonic", lambda: 1000.0)
        srv._last_activity_ts = 1.0  # idle = 1000 - 1 = 999 >= 600

        sleep_calls = []

        async def _fast_sleep(s):
            sleep_calls.append(s)
            # Only sleep once, then let the loop unload and we cancel.
            if len(sleep_calls) >= 2:
                raise asyncio.CancelledError()

        monkeypatch.setattr(asyncio, "sleep", _fast_sleep)

        try:
            await srv._idle_unload_loop()
        except asyncio.CancelledError:
            pass

        assert srv._pipe is None  # was unloaded

    @pytest.mark.asyncio
    async def test_idle_loop_does_not_unload_before_threshold(self, monkeypatch):
        """Background loop does not unload if idle time is below threshold."""
        fake_pipe = object()
        _reset_server_state(pipe_value=fake_pipe, last_activity=0.0)

        monkeypatch.setattr(srv, "IDLE_UNLOAD_THRESHOLD_S", 600.0)
        monkeypatch.setattr(srv, "IDLE_UNLOAD_INTERVAL_S", 0.001)

        # Idle = 1000 - 500 = 500 < 600 — should NOT unload
        monkeypatch.setattr(time, "monotonic", lambda: 1000.0)
        srv._last_activity_ts = 500.0

        sleep_count = [0]

        async def _fast_sleep(s):
            sleep_count[0] += 1
            if sleep_count[0] >= 2:
                raise asyncio.CancelledError()

        monkeypatch.setattr(asyncio, "sleep", _fast_sleep)

        try:
            await srv._idle_unload_loop()
        except asyncio.CancelledError:
            pass

        assert srv._pipe is fake_pipe  # NOT unloaded

    @pytest.mark.asyncio
    async def test_idle_loop_disabled_when_threshold_none(self, monkeypatch):
        """When IDLE_UNLOAD_THRESHOLD_S is None the loop exits immediately."""
        fake_pipe = object()
        _reset_server_state(pipe_value=fake_pipe)

        monkeypatch.setattr(srv, "IDLE_UNLOAD_THRESHOLD_S", None)

        # Loop must return without sleeping or unloading
        await srv._idle_unload_loop()

        assert srv._pipe is fake_pipe  # NOT touched


# ---------------------------------------------------------------------------
# Integration tests — HTTP endpoints via ASGI
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def rknn_sd_client():
    """Async HTTP client wired to the rknn_sd_server ASGI app."""
    transport = ASGITransport(app=srv.app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.mark.asyncio
class TestHealthEndpoint:
    async def test_health_pipeline_not_loaded(self, rknn_sd_client):
        """Health returns pipeline_loaded=false and null idle_seconds when nothing loaded."""
        _reset_server_state(pipe_value=None, last_activity=0.0)

        resp = await rknn_sd_client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["pipeline_loaded"] is False
        assert data["idle_seconds"] is None

    async def test_health_pipeline_loaded(self, rknn_sd_client):
        """Health returns pipeline_loaded=true and non-null idle_seconds when loaded."""
        fake_pipe = object()
        _reset_server_state(pipe_value=fake_pipe, last_activity=time.monotonic() - 30)

        resp = await rknn_sd_client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["pipeline_loaded"] is True
        assert data["idle_seconds"] is not None
        assert data["idle_seconds"] >= 29  # at least ~30s idle

    async def test_health_exposes_idle_threshold(self, rknn_sd_client, monkeypatch):
        """Health exposes idle_unload_threshold_s matching the configured value."""
        monkeypatch.setattr(srv, "IDLE_UNLOAD_THRESHOLD_S", 300.0)
        _reset_server_state()

        resp = await rknn_sd_client.get("/health")
        assert resp.json()["idle_unload_threshold_s"] == 300.0

    async def test_health_threshold_null_when_disabled(self, rknn_sd_client, monkeypatch):
        """Health shows null threshold when idle-unload is disabled."""
        monkeypatch.setattr(srv, "IDLE_UNLOAD_THRESHOLD_S", None)
        _reset_server_state()

        resp = await rknn_sd_client.get("/health")
        assert resp.json()["idle_unload_threshold_s"] is None


@pytest.mark.asyncio
class TestAdminUnload:
    async def test_unload_when_nothing_loaded(self, rknn_sd_client):
        """POST /admin/unload returns was_loaded=false when no pipeline is loaded."""
        _reset_server_state(pipe_value=None)

        resp = await rknn_sd_client.post("/admin/unload")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["was_loaded"] is False

    async def test_unload_when_pipeline_loaded(self, rknn_sd_client):
        """POST /admin/unload returns was_loaded=true and clears the pipeline."""
        fake_pipe = MagicMock()
        _reset_server_state(pipe_value=fake_pipe)

        resp = await rknn_sd_client.post("/admin/unload")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["was_loaded"] is True
        assert srv._pipe is None

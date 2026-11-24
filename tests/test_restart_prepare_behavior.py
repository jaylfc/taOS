"""App-level tests for update restart behavior (tsk-kzbxia).

Verifies that POST /api/system/restart/prepare does not trigger a graceful
shutdown (no "Graceful shutdown started" notification, no resume notes).
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from tinyagentos.app import create_app
from tinyagentos.auth_middleware import AuthMiddleware


class _StubAuth:
    def is_configured(self) -> bool:
        return True

    def validate_local_token(self, token: str) -> bool:
        return token == "test-token"

    def validate_session(self, token: str):
        return None

    def get_primary_user(self):
        return {"id": "user1", "username": "admin", "role": "admin"}

    def get_user_by_id(self, user_id: str):
        return {"id": user_id, "username": "admin", "role": "admin"}

    def get_local_token(self) -> str:
        return "test-token"


@pytest.mark.asyncio
async def test_restart_prepare_does_not_trigger_graceful_shutdown(tmp_path: Path, monkeypatch) -> None:
    """POST /api/system/restart/prepare must not call orchestrator.prepare
    with reason='system-shutdown', must not post 'Graceful shutdown started'
    notification, and must not create resume_note.json files."""
    config = {
        "server": {"host": "0.0.0.0", "port": 6969},
        "backends": [],
        "qmd": {"url": "http://localhost:7832"},
        "agents": [
            {
                "name": "test-agent",
                "host": "127.0.0.1",
                "port": 8080,
                "container_name": "taos-agent-test-agent",
            }
        ],
        "metrics": {"poll_interval": 30, "retention_days": 30},
    }
    config_path = tmp_path / "config.yaml"
    import yaml

    config_path.write_text(yaml.dump(config))
    (tmp_path / ".setup_complete").touch()

    # Set TAOS_DATA_DIR to tmp_path to avoid mutating repo data dir
    monkeypatch.setenv("TAOS_DATA_DIR", str(tmp_path))

    # Mock only LLMProxy like the existing test does
    with patch("tinyagentos.app.LLMProxy", return_value=MagicMock(port=7834, create_agent_key=MagicMock())):
        app = create_app(data_dir=tmp_path)
        app.state.auth = _StubAuth()
        app.add_middleware(AuthMiddleware)

        async with app.router.lifespan_context(app):
            # Mock the orchestrator's prepare method to track calls
            orchestrator = app.state.orchestrator
            original_prepare = orchestrator.prepare
            prepare_calls = []

            async def tracking_prepare(scope, reason):
                prepare_calls.append({"scope": scope, "reason": reason})
                return await original_prepare(scope, reason)

            orchestrator.prepare = tracking_prepare
            app.state._test_prepare_calls = prepare_calls

            # Mock the notification store's add method to track calls
            notif_store = app.state.notifications
            original_add = notif_store.add
            notify_calls = []

            async def tracking_add(*args, **kwargs):
                notify_calls.append({"args": args, "kwargs": kwargs})
                return await original_add(*args, **kwargs)

            notif_store.add = tracking_add

            transport = ASGITransport(app=app, client=("127.0.0.1", 5555))
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                # Use local token for auth
                resp = await client.post(
                    "/api/system/restart/prepare",
                    headers={"Authorization": "Bearer test-token", "Accept": "application/json"},
                )

            assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
            assert resp.json() == {"status": "restarting"}

            # Check that orchestrator.prepare was NOT called with system-shutdown
            shutdown_calls = [c for c in prepare_calls if c.get("reason") == "system-shutdown"]
            assert not shutdown_calls, (
                f"orchestrator.prepare was called with system-shutdown: {shutdown_calls}"
            )

            # Check that no "Graceful shutdown started" notification was posted
            graceful_shutdown_notifs = [
                c for c in notify_calls if "Graceful shutdown started" in str(c["kwargs"].get("title", ""))
            ]
            assert not graceful_shutdown_notifs, (
                f"'Graceful shutdown started' notification was posted: {graceful_shutdown_notifs}"
            )

            # Check that no resume_note.json was created in data/agent-memory
            agent_memory_dir = tmp_path / "data" / "agent-memory"
            resume_notes = list(agent_memory_dir.rglob("resume_note.json"))
            assert not resume_notes, (
                f"resume_note.json files were created: {resume_notes}"
            )


@pytest.mark.asyncio
async def test_restart_prepare_marks_restarting_for_update(tmp_path: Path, monkeypatch) -> None:
    """POST /api/system/restart/prepare should set _restarting_for_update flag."""
    config = {
        "server": {"host": "0.0.0.0", "port": 6969},
        "backends": [],
        "qmd": {"url": "http://localhost:7832"},
        "agents": [
            {
                "name": "test-agent",
                "host": "127.0.0.1",
                "port": 8080,
                "container_name": "taos-agent-test-agent",
            }
        ],
        "metrics": {"poll_interval": 30, "retention_days": 30},
    }
    config_path = tmp_path / "config.yaml"
    import yaml

    config_path.write_text(yaml.dump(config))
    (tmp_path / ".setup_complete").touch()

    # Set TAOS_DATA_DIR to tmp_path to avoid mutating repo data dir
    monkeypatch.setenv("TAOS_DATA_DIR", str(tmp_path))

    with patch("tinyagentos.app.LLMProxy", return_value=MagicMock(port=7834, create_agent_key=MagicMock())):
        app = create_app(data_dir=tmp_path)
        app.state.auth = _StubAuth()
        app.add_middleware(AuthMiddleware)

        async with app.router.lifespan_context(app):
            transport = ASGITransport(app=app, client=("127.0.0.1", 5555))
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/system/restart/prepare",
                    headers={"Authorization": "Bearer test-token", "Accept": "application/json"},
                )

            assert resp.status_code == 200
            assert app.state._restarting_for_update is True


@pytest.mark.asyncio
async def test_prepare_shutdown_skips_drain_when_restarting_for_update(tmp_path: Path, monkeypatch) -> None:
    """When _restarting_for_update is True and no shutdown job is queued,
    prepare-shutdown should return drain_skipped and not call orchestrator.prepare."""
    config = {
        "server": {"host": "0.0.0.0", "port": 6969},
        "backends": [],
        "qmd": {"url": "http://localhost:7832"},
        "agents": [
            {
                "name": "test-agent",
                "host": "127.0.0.1",
                "port": 8080,
                "container_name": "taos-agent-test-agent",
            }
        ],
        "metrics": {"poll_interval": 30, "retention_days": 30},
    }
    config_path = tmp_path / "config.yaml"
    import yaml

    config_path.write_text(yaml.dump(config))
    (tmp_path / ".setup_complete").touch()

    # Set TAOS_DATA_DIR to tmp_path to avoid mutating repo data dir
    monkeypatch.setenv("TAOS_DATA_DIR", str(tmp_path))

    with patch("tinyagentos.app.LLMProxy", return_value=MagicMock(port=7834, create_agent_key=MagicMock())):
        app = create_app(data_dir=tmp_path)
        app.state.auth = _StubAuth()
        app.add_middleware(AuthMiddleware)

        async with app.router.lifespan_context(app):
            # Mock the orchestrator's prepare method to track calls
            orchestrator = app.state.orchestrator
            original_prepare = orchestrator.prepare
            prepare_calls = []

            async def tracking_prepare(scope, reason):
                prepare_calls.append({"scope": scope, "reason": reason})
                return await original_prepare(scope, reason)

            orchestrator.prepare = tracking_prepare
            app.state._test_prepare_calls = prepare_calls

            # Set the restarting flag
            app.state._restarting_for_update = True

            # Mock _shutdown_job_queued to return False (no shutdown job)
            from tinyagentos.routes import system as system_routes

            original_shutdown_job_queued = system_routes._shutdown_job_queued

            async def mock_shutdown_job_queued() -> bool:
                return False

            system_routes._shutdown_job_queued = mock_shutdown_job_queued
            try:
                transport = ASGITransport(app=app, client=("127.0.0.1", 5555))
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    resp = await client.post(
                        "/api/system/prepare-shutdown",
                        headers={"Accept": "application/json"},
                    )

                assert resp.status_code == 200
                data = resp.json()
                assert data["status"] == "ready"
                assert data.get("drain_skipped") == "controller restarting for an update; no shutdown job queued"
                assert data["report"] == {}

                # Verify orchestrator.prepare was not called
                assert not prepare_calls, f"orchestrator.prepare was called: {prepare_calls}"
            finally:
                system_routes._shutdown_job_queued = original_shutdown_job_queued
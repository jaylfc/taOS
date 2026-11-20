"""Tests for the other-systems routes (phone OS switching)."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from tinyagentos.routes import other_systems as other_systems_routes


def _write_helper(script: str) -> str:
    """Write a helper script with shebang and return its path."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write("#!/usr/bin/env python3\n")
        f.write(script)
        helper_path = f.name
    os.chmod(helper_path, 0o755)
    return helper_path


class TestOtherSystemsList:
    """Tests for GET /api/system/other-systems."""

    @pytest.mark.asyncio
    async def test_list_valid_entries(self, monkeypatch):
        """Valid helper output is parsed and returned."""
        helper_script = """
import json, sys
print(json.dumps([
    {"id": "omarchy", "name": "Omarchy", "ready": True, "reason": ""},
    {"id": "luma", "name": "Luma", "ready": False, "reason": "guest kernel is stale"}
]))
"""
        helper_path = _write_helper(helper_script)

        try:
            monkeypatch.setenv("TAOS_SWITCH_OS_HELPER", helper_path)
            systems = await other_systems_routes._run_list(helper_path)
            assert len(systems) == 2
            assert systems[0]["id"] == "omarchy"
            assert systems[0]["ready"] is True
            assert systems[1]["id"] == "luma"
            assert systems[1]["ready"] is False
            assert systems[1]["reason"] == "guest kernel is stale"
        finally:
            os.unlink(helper_path)
            monkeypatch.delenv("TAOS_SWITCH_OS_HELPER", raising=False)

    @pytest.mark.asyncio
    async def test_list_drops_invalid_entry(self, monkeypatch, caplog):
        """Invalid entries in helper output are dropped and logged."""
        helper_script = """
import json, sys
print(json.dumps([
    {"id": "omarchy", "name": "Omarchy", "ready": True, "reason": ""},
    {"id": "Invalid_ID!", "name": "Bad", "ready": True, "reason": ""},
    {"id": "luma", "name": "Luma", "ready": False, "reason": "stale"}
]))
"""
        helper_path = _write_helper(helper_script)

        try:
            monkeypatch.setenv("TAOS_SWITCH_OS_HELPER", helper_path)
            with caplog.at_level("WARNING"):
                systems = await other_systems_routes._run_list(helper_path)
            assert len(systems) == 2
            assert systems[0]["id"] == "omarchy"
            assert systems[1]["id"] == "luma"
            assert any("dropped invalid entry" in r.message for r in caplog.records)
        finally:
            os.unlink(helper_path)
            monkeypatch.delenv("TAOS_SWITCH_OS_HELPER", raising=False)

    @pytest.mark.asyncio
    async def test_list_helper_missing_returns_empty(self, monkeypatch):
        """Missing helper returns empty list (available=false at route level)."""
        monkeypatch.setenv("TAOS_SWITCH_OS_HELPER", "/nonexistent/taos-switch-os")
        systems = await other_systems_routes._run_list("/nonexistent/taos-switch-os")
        assert systems == []
        monkeypatch.delenv("TAOS_SWITCH_OS_HELPER", raising=False)

    @pytest.mark.asyncio
    async def test_list_timeout_returns_empty(self, monkeypatch, caplog):
        """Helper timeout returns empty list."""
        helper_script = """
import time, sys
time.sleep(20)
print("[]")
"""
        helper_path = _write_helper(helper_script)

        try:
            monkeypatch.setenv("TAOS_SWITCH_OS_HELPER", helper_path)
            with caplog.at_level("WARNING"):
                systems = await other_systems_routes._run_list(helper_path)
            assert systems == []
            assert any("timed out" in r.message for r in caplog.records)
        finally:
            os.unlink(helper_path)
            monkeypatch.delenv("TAOS_SWITCH_OS_HELPER", raising=False)


class TestOtherSystemsSwitch:
    """Tests for POST /api/system/other-systems/{id}/switch."""

    def _make_request(self, user_id="user1", is_admin=True, via="session"):
        request = MagicMock()
        request.state.user_id = user_id
        request.state.is_admin = is_admin
        request.state.via = via
        request.app.state = MagicMock()
        request.app.state.notifications = AsyncMock()
        request.app.state.orchestrator = AsyncMock()
        request.app.state.orchestrator.prepare = AsyncMock(return_value={})
        return request

    @pytest.fixture(autouse=True)
    def _unlock_console(self):
        """Unlock the console for all switch tests."""
        with patch("tinyagentos.routes.auth._CONSOLE_LOCKED", False):
            yield

    @pytest.mark.asyncio
    async def test_switch_happy_path(self, monkeypatch):
        """Successful switch returns helper JSON."""
        helper_script = """
import json, sys
if sys.argv[1] == "list":
    print(json.dumps([{"id": "omarchy", "name": "Omarchy", "ready": True, "reason": ""}]))
elif sys.argv[1] == "go":
    print(json.dumps({"ok": True, "id": "omarchy", "in_seconds": 2}))
    sys.exit(0)
"""
        helper_path = _write_helper(helper_script)

        try:
            monkeypatch.setenv("TAOS_SWITCH_OS_HELPER", helper_path)

            request = self._make_request()
            request.json = AsyncMock(return_value={"confirm": True})

            # Mock _run_list to return the system list
            with patch("tinyagentos.routes.other_systems._run_list", AsyncMock(return_value=[{"id": "omarchy", "name": "Omarchy", "ready": True, "reason": ""}])):
                # Mock the subprocess call for "go" to return success
                mock_proc = AsyncMock()
                mock_proc.communicate = AsyncMock(return_value=(b'{"ok": true, "id": "omarchy", "in_seconds": 2}', b""))
                mock_proc.returncode = 0

                with patch("tinyagentos.routes.other_systems.asyncio.create_subprocess_exec", return_value=mock_proc):
                    result = await other_systems_routes.switch_other_system("omarchy", request)

            assert result == {"ok": True, "id": "omarchy", "in_seconds": 2}
            request.app.state.orchestrator.prepare.assert_called_once_with("all", "system-shutdown")
        finally:
            os.unlink(helper_path)
            monkeypatch.delenv("TAOS_SWITCH_OS_HELPER", raising=False)

    @pytest.mark.asyncio
    async def test_switch_id_not_in_list_returns_409(self, monkeypatch):
        """Switching to unknown id returns 409."""
        helper_script = """
import json, sys
print(json.dumps([{"id": "omarchy", "name": "Omarchy", "ready": True, "reason": ""}]))
"""
        helper_path = _write_helper(helper_script)

        try:
            monkeypatch.setenv("TAOS_SWITCH_OS_HELPER", helper_path)

            request = self._make_request()
            request.json = AsyncMock(return_value={"confirm": True})

            with patch("tinyagentos.routes.other_systems._run_list", AsyncMock(return_value=[{"id": "omarchy", "name": "Omarchy", "ready": True, "reason": ""}])):
                with pytest.raises(HTTPException) as exc:
                    await other_systems_routes.switch_other_system("unknown", request)

            assert exc.value.status_code == 409
            assert exc.value.detail == "system not found"
        finally:
            os.unlink(helper_path)
            monkeypatch.delenv("TAOS_SWITCH_OS_HELPER", raising=False)

    @pytest.mark.asyncio
    async def test_switch_not_ready_returns_409_with_reason(self, monkeypatch):
        """Switching to not-ready system returns 409 with reason."""
        helper_script = """
import json, sys
print(json.dumps([{"id": "luma", "name": "Luma", "ready": False, "reason": "guest kernel is stale"}]))
"""
        helper_path = _write_helper(helper_script)

        try:
            monkeypatch.setenv("TAOS_SWITCH_OS_HELPER", helper_path)

            request = self._make_request()
            request.json = AsyncMock(return_value={"confirm": True})

            with patch("tinyagentos.routes.other_systems._run_list", AsyncMock(return_value=[{"id": "luma", "name": "Luma", "ready": False, "reason": "guest kernel is stale"}])):
                with pytest.raises(HTTPException) as exc:
                    await other_systems_routes.switch_other_system("luma", request)

            assert exc.value.status_code == 409
            assert exc.value.detail == "guest kernel is stale"
        finally:
            os.unlink(helper_path)
            monkeypatch.delenv("TAOS_SWITCH_OS_HELPER", raising=False)

    @pytest.mark.asyncio
    async def test_switch_exit_code_2_returns_400(self, monkeypatch):
        """Helper exit code 2 (bad form) maps to 400."""
        helper_script = """
import json, sys
if sys.argv[1] == "list":
    print(json.dumps([{"id": "omarchy", "name": "Omarchy", "ready": True, "reason": ""}]))
elif sys.argv[1] == "go":
    sys.exit(2)
"""
        helper_path = _write_helper(helper_script)

        try:
            monkeypatch.setenv("TAOS_SWITCH_OS_HELPER", helper_path)

            request = self._make_request()
            request.json = AsyncMock(return_value={"confirm": True})

            with patch("tinyagentos.routes.other_systems._run_list", AsyncMock(return_value=[{"id": "omarchy", "name": "Omarchy", "ready": True, "reason": ""}])):
                mock_proc = AsyncMock()
                mock_proc.communicate = AsyncMock(return_value=(b"", b""))
                mock_proc.returncode = 2

                with patch("tinyagentos.routes.other_systems.asyncio.create_subprocess_exec", return_value=mock_proc):
                    with pytest.raises(HTTPException) as exc:
                        await other_systems_routes.switch_other_system("omarchy", request)

            assert exc.value.status_code == 400
            assert exc.value.detail == "bad switch request"
        finally:
            os.unlink(helper_path)
            monkeypatch.delenv("TAOS_SWITCH_OS_HELPER", raising=False)

    @pytest.mark.asyncio
    async def test_switch_exit_code_3_returns_409_with_stderr_reason(self, monkeypatch):
        """Helper exit code 3 (not ready) returns stderr reason."""
        helper_script = """
import json, sys
if sys.argv[1] == "list":
    print(json.dumps([{"id": "omarchy", "name": "Omarchy", "ready": True, "reason": ""}]))
elif sys.argv[1] == "go":
    print("guest kernel is stale: run refresh-guest-boot.sh", file=sys.stderr)
    sys.exit(3)
"""
        helper_path = _write_helper(helper_script)

        try:
            monkeypatch.setenv("TAOS_SWITCH_OS_HELPER", helper_path)

            request = self._make_request()
            request.json = AsyncMock(return_value={"confirm": True})

            with patch("tinyagentos.routes.other_systems._run_list", AsyncMock(return_value=[{"id": "omarchy", "name": "Omarchy", "ready": True, "reason": ""}])):
                mock_proc = AsyncMock()
                mock_proc.communicate = AsyncMock(return_value=(b"", b"guest kernel is stale: run refresh-guest-boot.sh"))
                mock_proc.returncode = 3

                with patch("tinyagentos.routes.other_systems.asyncio.create_subprocess_exec", return_value=mock_proc):
                    with pytest.raises(HTTPException) as exc:
                        await other_systems_routes.switch_other_system("omarchy", request)

            assert exc.value.status_code == 409
            assert "guest kernel is stale" in exc.value.detail
        finally:
            os.unlink(helper_path)
            monkeypatch.delenv("TAOS_SWITCH_OS_HELPER", raising=False)

    @pytest.mark.asyncio
    async def test_switch_exit_code_1_returns_503(self, monkeypatch):
        """Helper exit code 1 (no root/sudoers) maps to 503."""
        helper_script = """
import json, sys
if sys.argv[1] == "list":
    print(json.dumps([{"id": "omarchy", "name": "Omarchy", "ready": True, "reason": ""}]))
elif sys.argv[1] == "go":
    sys.exit(1)
"""
        helper_path = _write_helper(helper_script)

        try:
            monkeypatch.setenv("TAOS_SWITCH_OS_HELPER", helper_path)

            request = self._make_request()
            request.json = AsyncMock(return_value={"confirm": True})

            with patch("tinyagentos.routes.other_systems._run_list", AsyncMock(return_value=[{"id": "omarchy", "name": "Omarchy", "ready": True, "reason": ""}])):
                mock_proc = AsyncMock()
                mock_proc.communicate = AsyncMock(return_value=(b"", b""))
                mock_proc.returncode = 1

                with patch("tinyagentos.routes.other_systems.asyncio.create_subprocess_exec", return_value=mock_proc):
                    with pytest.raises(HTTPException) as exc:
                        await other_systems_routes.switch_other_system("omarchy", request)

            assert exc.value.status_code == 503
            assert exc.value.detail == "this phone is not set up to switch systems"
        finally:
            os.unlink(helper_path)
            monkeypatch.delenv("TAOS_SWITCH_OS_HELPER", raising=False)

    @pytest.mark.asyncio
    async def test_switch_exit_code_other_returns_502(self, monkeypatch):
        """Other non-zero exit codes map to 502."""
        helper_script = """
import json, sys
if sys.argv[1] == "list":
    print(json.dumps([{"id": "omarchy", "name": "Omarchy", "ready": True, "reason": ""}]))
elif sys.argv[1] == "go":
    sys.exit(99)
"""
        helper_path = _write_helper(helper_script)

        try:
            monkeypatch.setenv("TAOS_SWITCH_OS_HELPER", helper_path)

            request = self._make_request()
            request.json = AsyncMock(return_value={"confirm": True})

            with patch("tinyagentos.routes.other_systems._run_list", AsyncMock(return_value=[{"id": "omarchy", "name": "Omarchy", "ready": True, "reason": ""}])):
                mock_proc = AsyncMock()
                mock_proc.communicate = AsyncMock(return_value=(b"", b""))
                mock_proc.returncode = 99

                with patch("tinyagentos.routes.other_systems.asyncio.create_subprocess_exec", return_value=mock_proc):
                    with pytest.raises(HTTPException) as exc:
                        await other_systems_routes.switch_other_system("omarchy", request)

            assert exc.value.status_code == 502
            assert exc.value.detail == "switch failed"
        finally:
            os.unlink(helper_path)
            monkeypatch.delenv("TAOS_SWITCH_OS_HELPER", raising=False)

    @pytest.mark.asyncio
    async def test_switch_non_admin_refused_403(self, monkeypatch):
        """Non-admin users are refused with 403."""
        helper_script = """
import json, sys
print(json.dumps([{"id": "omarchy", "name": "Omarchy", "ready": True, "reason": ""}]))
"""
        helper_path = _write_helper(helper_script)

        try:
            monkeypatch.setenv("TAOS_SWITCH_OS_HELPER", helper_path)

            request = self._make_request(is_admin=False)

            with pytest.raises(HTTPException) as exc:
                await other_systems_routes.switch_other_system("omarchy", request)

            assert exc.value.status_code == 403
        finally:
            os.unlink(helper_path)
            monkeypatch.delenv("TAOS_SWITCH_OS_HELPER", raising=False)

    @pytest.mark.asyncio
    async def test_switch_local_token_refused_403(self, monkeypatch):
        """Local token (taosctl/deployed agents) is refused with 403."""
        helper_script = """
import json, sys
print(json.dumps([{"id": "omarchy", "name": "Omarchy", "ready": True, "reason": ""}]))
"""
        helper_path = _write_helper(helper_script)

        try:
            monkeypatch.setenv("TAOS_SWITCH_OS_HELPER", helper_path)

            request = self._make_request(via="local_token")

            with pytest.raises(HTTPException) as exc:
                await other_systems_routes.switch_other_system("omarchy", request)

            assert exc.value.status_code == 403
        finally:
            os.unlink(helper_path)
            monkeypatch.delenv("TAOS_SWITCH_OS_HELPER", raising=False)

    @pytest.mark.asyncio
    async def test_switch_agent_token_refused_403(self, monkeypatch):
        """Agent token is refused with 403."""
        helper_script = """
import json, sys
print(json.dumps([{"id": "omarchy", "name": "Omarchy", "ready": True, "reason": ""}]))
"""
        helper_path = _write_helper(helper_script)

        try:
            monkeypatch.setenv("TAOS_SWITCH_OS_HELPER", helper_path)

            request = self._make_request(via="agent")

            with pytest.raises(HTTPException) as exc:
                await other_systems_routes.switch_other_system("omarchy", request)

            assert exc.value.status_code == 403
        finally:
            os.unlink(helper_path)
            monkeypatch.delenv("TAOS_SWITCH_OS_HELPER", raising=False)

    @pytest.mark.asyncio
    async def test_switch_drain_called_before_helper(self, monkeypatch):
        """Agent drain is called before the helper."""
        helper_script = """
import json, sys
if sys.argv[1] == "list":
    print(json.dumps([{"id": "omarchy", "name": "Omarchy", "ready": True, "reason": ""}]))
elif sys.argv[1] == "go":
    print(json.dumps({"ok": True, "id": "omarchy", "in_seconds": 2}))
    sys.exit(0)
"""
        helper_path = _write_helper(helper_script)

        try:
            monkeypatch.setenv("TAOS_SWITCH_OS_HELPER", helper_path)

            request = self._make_request()
            request.json = AsyncMock(return_value={"confirm": True})

            call_order = []

            async def mock_prepare(scope, reason):
                call_order.append("drain")
                return {}

            request.app.state.orchestrator.prepare = mock_prepare

            with patch("tinyagentos.routes.other_systems._run_list", AsyncMock(return_value=[{"id": "omarchy", "name": "Omarchy", "ready": True, "reason": ""}])):
                mock_proc = AsyncMock()
                mock_proc.communicate = AsyncMock(return_value=(b'{"ok": true, "id": "omarchy", "in_seconds": 2}', b""))
                mock_proc.returncode = 0

                async def tracking_create_subprocess_exec(*args, **kwargs):
                    call_order.append("helper")
                    return mock_proc

                with patch("tinyagentos.routes.other_systems.asyncio.create_subprocess_exec", tracking_create_subprocess_exec):
                    await other_systems_routes.switch_other_system("omarchy", request)

            assert call_order == ["drain", "helper"]
        finally:
            os.unlink(helper_path)
            monkeypatch.delenv("TAOS_SWITCH_OS_HELPER", raising=False)

    @pytest.mark.asyncio
    async def test_switch_force_overrides_drain_failure(self, monkeypatch):
        """force=true allows switch even when drain fails."""
        helper_script = """
import json, sys
if sys.argv[1] == "list":
    print(json.dumps([{"id": "omarchy", "name": "Omarchy", "ready": True, "reason": ""}]))
elif sys.argv[1] == "go":
    print(json.dumps({"ok": True, "id": "omarchy", "in_seconds": 2}))
    sys.exit(0)
"""
        helper_path = _write_helper(helper_script)

        try:
            monkeypatch.setenv("TAOS_SWITCH_OS_HELPER", helper_path)

            request = self._make_request()
            request.json = AsyncMock(return_value={"confirm": True, "force": True})
            request.app.state.orchestrator.prepare = AsyncMock(return_value={"agent1": {"status": "timeout"}})

            with patch("tinyagentos.routes.other_systems._run_list", AsyncMock(return_value=[{"id": "omarchy", "name": "Omarchy", "ready": True, "reason": ""}])):
                mock_proc = AsyncMock()
                mock_proc.communicate = AsyncMock(return_value=(b'{"ok": true, "id": "omarchy", "in_seconds": 2}', b""))
                mock_proc.returncode = 0

                with patch("tinyagentos.routes.other_systems.asyncio.create_subprocess_exec", return_value=mock_proc):
                    result = await other_systems_routes.switch_other_system("omarchy", request)

            assert result == {"ok": True, "id": "omarchy", "in_seconds": 2}
        finally:
            os.unlink(helper_path)
            monkeypatch.delenv("TAOS_SWITCH_OS_HELPER", raising=False)

    @pytest.mark.asyncio
    async def test_switch_without_force_fails_on_drain_failure(self, monkeypatch):
        """Without force, drain failure returns 409."""
        helper_script = """
import json, sys
if sys.argv[1] == "list":
    print(json.dumps([{"id": "omarchy", "name": "Omarchy", "ready": True, "reason": ""}]))
elif sys.argv[1] == "go":
    print(json.dumps({"ok": True, "id": "omarchy", "in_seconds": 2}))
    sys.exit(0)
"""
        helper_path = _write_helper(helper_script)

        try:
            monkeypatch.setenv("TAOS_SWITCH_OS_HELPER", helper_path)

            request = self._make_request()
            request.json = AsyncMock(return_value={"confirm": True, "force": False})
            request.app.state.orchestrator.prepare = AsyncMock(return_value={"agent1": {"status": "timeout"}})

            with pytest.raises(HTTPException) as exc:
                await other_systems_routes.switch_other_system("omarchy", request)

            assert exc.value.status_code == 409
            assert "agents not ready" in exc.value.detail
        finally:
            os.unlink(helper_path)
            monkeypatch.delenv("TAOS_SWITCH_OS_HELPER", raising=False)

    @pytest.mark.asyncio
    async def test_switch_console_locked_returns_423(self, monkeypatch):
        """Locked console returns 423."""
        helper_script = """
import json, sys
print(json.dumps([{"id": "omarchy", "name": "Omarchy", "ready": True, "reason": ""}]))
"""
        helper_path = _write_helper(helper_script)

        try:
            monkeypatch.setenv("TAOS_SWITCH_OS_HELPER", helper_path)

            # Patch _CONSOLE_LOCKED to True in the auth module where it's imported from
            with patch("tinyagentos.routes.auth._CONSOLE_LOCKED", True):
                request = self._make_request()
                request.json = AsyncMock(return_value={"confirm": True})

                with pytest.raises(HTTPException) as exc:
                    await other_systems_routes.switch_other_system("omarchy", request)

            assert exc.value.status_code == 423
            assert exc.value.detail == "console is locked"
        finally:
            os.unlink(helper_path)
            monkeypatch.delenv("TAOS_SWITCH_OS_HELPER", raising=False)


class TestOtherSystemsRouteIntegration:
    """Integration tests using TestClient."""

    def _make_app(self):
        from fastapi import FastAPI
        from tinyagentos.middleware.csrf import verify_csrf
        from fastapi import Depends

        app = FastAPI()
        app.include_router(other_systems_routes.router, dependencies=[Depends(verify_csrf)])
        return app

    @pytest.fixture
    def client(self):
        app = self._make_app()
        return TestClient(app)

    def test_get_other_systems_helper_missing(self, client, monkeypatch):
        """GET returns available=false when helper missing."""
        monkeypatch.setenv("TAOS_SWITCH_OS_HELPER", "/nonexistent/helper")
        response = client.get("/api/system/other-systems")
        assert response.status_code == 200
        assert response.json() == {"available": False, "systems": []}
        monkeypatch.delenv("TAOS_SWITCH_OS_HELPER", raising=False)


class TestOtherSystemsHelperPathValidation:
    """Tests for TAOS_SWITCH_OS_HELPER path validation."""

    def _make_request(self, user_id="user1", is_admin=True, via="session"):
        request = MagicMock()
        request.state.user_id = user_id
        request.state.is_admin = is_admin
        request.state.via = via
        request.app.state = MagicMock()
        request.app.state.notifications = AsyncMock()
        request.app.state.orchestrator = AsyncMock()
        request.app.state.orchestrator.prepare = AsyncMock(return_value={})
        return request

    @pytest.fixture(autouse=True)
    def _unlock_console(self):
        """Unlock the console for all switch tests."""
        with patch("tinyagentos.routes.auth._CONSOLE_LOCKED", False):
            yield

    @pytest.mark.asyncio
    async def test_helper_non_absolute_path_returns_unavailable(self, monkeypatch):
        """Non-absolute TAOS_SWITCH_OS_HELPER returns available=false."""
        monkeypatch.setenv("TAOS_SWITCH_OS_HELPER", "relative/path/helper")
        systems = await other_systems_routes._run_list("relative/path/helper")
        # _run_list is called directly with the path, but the route uses _helper_path()
        # which returns None for non-absolute paths
        helper = other_systems_routes._helper_path()
        assert helper is None
        monkeypatch.delenv("TAOS_SWITCH_OS_HELPER", raising=False)

    @pytest.mark.asyncio
    async def test_helper_absolute_path_accepted(self, monkeypatch):
        """Absolute TAOS_SWITCH_OS_HELPER is accepted."""
        monkeypatch.setenv("TAOS_SWITCH_OS_HELPER", "/absolute/path/helper")
        helper = other_systems_routes._helper_path()
        assert helper == "/absolute/path/helper"
        monkeypatch.delenv("TAOS_SWITCH_OS_HELPER", raising=False)

    @pytest.mark.asyncio
    async def test_switch_argv_passed_to_create_subprocess_exec(self, monkeypatch):
        """Test that exact argv is passed to create_subprocess_exec."""
        helper_script = """
import json, sys
if sys.argv[1] == "list":
    print(json.dumps([{"id": "omarchy", "name": "Omarchy", "ready": True, "reason": ""}]))
elif sys.argv[1] == "go":
    print(json.dumps({"ok": True, "id": "omarchy", "in_seconds": 2}))
    sys.exit(0)
"""
        helper_path = _write_helper(helper_script)

        try:
            monkeypatch.setenv("TAOS_SWITCH_OS_HELPER", helper_path)

            request = self._make_request()
            request.json = AsyncMock(return_value={"confirm": True})

            captured_argv = []

            with patch("tinyagentos.routes.other_systems._run_list", AsyncMock(return_value=[{"id": "omarchy", "name": "Omarchy", "ready": True, "reason": ""}])):
                mock_proc = AsyncMock()
                mock_proc.communicate = AsyncMock(return_value=(b'{"ok": true, "id": "omarchy", "in_seconds": 2}', b""))
                mock_proc.returncode = 0

                async def capture_argv(*args, **kwargs):
                    captured_argv.extend(args)
                    return mock_proc

                with patch("tinyagentos.routes.other_systems.asyncio.create_subprocess_exec", capture_argv):
                    await other_systems_routes.switch_other_system("omarchy", request)

            # Assert the exact argv passed
            assert captured_argv == ["sudo", "-n", helper_path, "go", "omarchy"]
        finally:
            os.unlink(helper_path)
            monkeypatch.delenv("TAOS_SWITCH_OS_HELPER", raising=False)
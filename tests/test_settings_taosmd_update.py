"""Settings-update taOSmd currency + running-server verification (tsk-jjkukj).

The contract under test:

* ``/api/settings/update`` brings a LOCALLY-hosted taOSmd to latest in the same
  action, restarts its service, and then verifies the RUNNING server — never
  the checkout, never pip, never exit status alone.
* Verification asserts in order: (a) HTTP 200 whose Content-Type is
  application/json (the SPA catch-all serves text/html 200 and must FAIL),
  (b) the core capability identifiers are present in the body.
* Any taOSmd failure fails the whole update loudly; skips (remote taOSmd,
  hooks unset) are reported in the response, never silent.
"""

import contextlib
import types

import httpx
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from tinyagentos.routes import settings as settings_mod
from tinyagentos.routes.settings import (
    REQUIRED_TAOSMD_CAPABILITIES,
    _update_local_taosmd,
    _verify_taosmd_running,
)

REAL_HEALTH_BODY = {
    "status": "ok",
    "version": "0.4.0",
    "capabilities": sorted(REQUIRED_TAOSMD_CAPABILITIES | {"graph.v1", "tasks.v1"}),
}


class _FakeResp:
    def __init__(self, status_code=200, content_type="application/json", body=None):
        self.status_code = status_code
        self.headers = {"content-type": content_type}
        self._body = body

    def json(self):
        if self._body is None:
            raise ValueError("not json")
        return self._body


class _FakeHttpClient:
    def __init__(self, resp=None, exc=None):
        self._resp = resp
        self._exc = exc

    async def get(self, url, **kwargs):
        if self._exc is not None:
            raise self._exc
        return self._resp


def _fake_request(resp=None, exc=None, memory_url="http://localhost:7900",
                  taosmd_dir="", taosmd_restart_cmd=""):
    config = types.SimpleNamespace(
        memory_url=memory_url,
        taosmd_dir=taosmd_dir,
        taosmd_restart_cmd=taosmd_restart_cmd,
    )
    state = types.SimpleNamespace(config=config, http_client=_FakeHttpClient(resp, exc))
    return types.SimpleNamespace(app=types.SimpleNamespace(state=state))


class TestVerifyTaosmdRunning:
    @pytest.mark.asyncio
    async def test_fails_on_spa_catchall_html_200(self):
        """A text/html 200 is the SPA answering the wrong port — MUST fail."""
        req = _fake_request(_FakeResp(200, "text/html; charset=utf-8"))
        reason = await _verify_taosmd_running(req)
        assert reason is not None
        assert "Content-Type" in reason and "SPA catch-all" in reason

    @pytest.mark.asyncio
    async def test_fails_on_missing_capability_identifiers(self):
        body = {"status": "ok", "capabilities": ["collections.v1", "search.v1"]}
        req = _fake_request(_FakeResp(200, "application/json", body))
        reason = await _verify_taosmd_running(req)
        assert reason is not None and "a2a.v1" in reason

    @pytest.mark.asyncio
    async def test_fails_on_non_200(self):
        req = _fake_request(_FakeResp(503, "application/json", {"status": "down"}))
        reason = await _verify_taosmd_running(req)
        assert reason is not None and "503" in reason

    @pytest.mark.asyncio
    async def test_fails_on_missing_capabilities_list(self):
        req = _fake_request(_FakeResp(200, "application/json", {"status": "ok"}))
        reason = await _verify_taosmd_running(req)
        assert reason is not None and "capabilities" in reason

    @pytest.mark.asyncio
    async def test_fails_when_unreachable(self):
        req = _fake_request(exc=httpx.ConnectError("connection refused"))
        reason = await _verify_taosmd_running(req)
        assert reason is not None and "unreachable" in reason

    @pytest.mark.asyncio
    async def test_fails_named_on_non_string_capability_entries(self):
        """Unhashable/odd entries must yield a NAMED reason, not a TypeError."""
        body = {"status": "ok", "capabilities": [{"cap": "a2a.v1"}, "search.v1"]}
        req = _fake_request(_FakeResp(200, "application/json", body))
        reason = await _verify_taosmd_running(req)
        assert reason is not None and "non-string" in reason

    @pytest.mark.asyncio
    async def test_passes_on_real_health_shape(self):
        req = _fake_request(_FakeResp(200, "application/json", REAL_HEALTH_BODY))
        assert await _verify_taosmd_running(req) is None


class TestUpdateLocalTaosmd:
    @pytest.mark.asyncio
    async def test_skips_when_memory_url_remote(self):
        req = _fake_request(memory_url="http://192.168.1.50:7900",
                            taosmd_dir="/somewhere", taosmd_restart_cmd="true")
        report = await _update_local_taosmd(req)
        assert "not local" in report["skipped"]

    @pytest.mark.asyncio
    async def test_skips_when_dir_not_configured(self):
        req = _fake_request()
        report = await _update_local_taosmd(req)
        assert report["skipped"] == "taosmd_dir not configured"

    @pytest.mark.asyncio
    async def test_errors_when_dir_is_not_a_git_checkout(self, tmp_path):
        req = _fake_request(taosmd_dir=str(tmp_path), taosmd_restart_cmd="true")
        report = await _update_local_taosmd(req)
        assert "not a git checkout" in report["error"]

    @pytest.mark.asyncio
    async def test_errors_when_restart_cmd_unset(self, tmp_path):
        """A pull the running service never loads is a silent half-update."""
        (tmp_path / ".git").mkdir()
        req = _fake_request(taosmd_dir=str(tmp_path))
        report = await _update_local_taosmd(req)
        assert "taosmd_restart_cmd" in report["error"]

    @pytest.mark.asyncio
    async def test_errors_named_on_unparseable_restart_cmd(self, tmp_path):
        """An unmatched quote must be a NAMED error before anything runs."""
        (tmp_path / ".git").mkdir()
        req = _fake_request(taosmd_dir=str(tmp_path),
                            taosmd_restart_cmd="systemctl restart 'taosmd")
        report = await _update_local_taosmd(req)
        assert "not parseable" in report["error"]

    @pytest.mark.asyncio
    async def test_errors_named_on_whitespace_restart_cmd(self, tmp_path):
        (tmp_path / ".git").mkdir()
        req = _fake_request(taosmd_dir=str(tmp_path), taosmd_restart_cmd="  ")
        report = await _update_local_taosmd(req)
        # Whitespace-only strips to empty -> caught by the unset check.
        assert "taosmd_restart_cmd" in report["error"]

    @pytest.mark.asyncio
    async def test_errors_loudly_when_pull_fails(self, tmp_path):
        (tmp_path / ".git").mkdir()
        req = _fake_request(taosmd_dir=str(tmp_path), taosmd_restart_cmd="true")
        with patch.object(settings_mod, "_run_capture",
                          new=AsyncMock(return_value=(1, "fatal: not fast-forward"))):
            report = await _update_local_taosmd(req)
        assert "git pull failed" in report["error"]

    @pytest.mark.asyncio
    async def test_announces_before_restart_and_reports(self, tmp_path):
        (tmp_path / ".git").mkdir()
        req = _fake_request(taosmd_dir=str(tmp_path),
                            taosmd_restart_cmd="systemctl restart taosmd")
        calls = []

        async def _fake_run(cmd, cwd=None, timeout=None, env=None):
            calls.append(list(cmd))
            return 0, "ok"

        async def _fake_announce(text):
            calls.append(["ANNOUNCE"])
            return True

        with (
            patch.object(settings_mod, "_run_capture", new=_fake_run),
            patch.object(settings_mod, "_announce_taosmd_restart", new=_fake_announce),
        ):
            report = await _update_local_taosmd(req)

        assert report["updated"] and report["restarted"] and report["announced"]
        # Order: pull, announce, restart — the notice must precede the restart.
        assert calls == [
            ["git", "pull", "--ff-only"],
            ["ANNOUNCE"],
            ["systemctl", "restart", "taosmd"],
        ]


def _update_route_patches():
    """The subprocess/restart patch set the existing update tests use."""
    fake_proc = MagicMock()
    fake_proc.returncode = 0
    fake_proc.communicate = AsyncMock(return_value=(b"Already up to date.\n", b""))

    async def _fake_restart(_app_state):
        return None

    return (
        patch(
            "tinyagentos.routes.settings.asyncio.create_subprocess_exec",
            return_value=fake_proc,
        ),
        patch(
            "tinyagentos.routes.settings._run_capture",
            new=AsyncMock(return_value=(0, "ok")),
        ),
        patch(
            "tinyagentos.desktop_rebuild.rebuild_desktop_bundle_if_stale",
            new=AsyncMock(
                return_value=MagicMock(rebuilt=False, success=True, message="current")
            ),
        ),
        patch("tinyagentos.routes.system._do_restart", new=_fake_restart),
        patch("tinyagentos.restart_orchestrator.write_pending_restart"),
        patch(
            "tinyagentos.routes.settings._announce_taosmd_restart",
            new=AsyncMock(return_value=True),
        ),
        patch.object(settings_mod, "_TAOSMD_VERIFY_RETRIES", 1),
        patch.object(settings_mod, "_TAOSMD_VERIFY_DELAY", 0),
    )


class TestApplyUpdateTaosmdContract:
    @pytest.mark.asyncio
    async def test_update_reports_failure_on_verification_mismatch(
        self, client, app, tmp_path
    ):
        """The update must FAIL (500, named reason) when the restarted taOSmd
        answers with the SPA catch-all shape instead of real health JSON."""
        (tmp_path / ".git").mkdir()
        app.state.config.taosmd_dir = str(tmp_path)
        app.state.config.taosmd_restart_cmd = "systemctl restart taosmd"
        real_http_client = app.state.http_client
        app.state.http_client = _FakeHttpClient(_FakeResp(200, "text/html"))
        try:
            with contextlib.ExitStack() as stack:
                for p in _update_route_patches():
                    stack.enter_context(p)
                resp = await client.post("/api/settings/update")
        finally:
            app.state.http_client = real_http_client
            app.state.config.taosmd_dir = ""
            app.state.config.taosmd_restart_cmd = ""

        assert resp.status_code == 500, (
            f"Expected the update to FAIL on a text/html health response, got "
            f"{resp.status_code}: {resp.json()!r}"
        )
        assert "verification FAILED" in resp.json()["error"]

    @pytest.mark.asyncio
    async def test_update_succeeds_and_reports_taosmd_updated(
        self, client, app, tmp_path
    ):
        (tmp_path / ".git").mkdir()
        app.state.config.taosmd_dir = str(tmp_path)
        app.state.config.taosmd_restart_cmd = "systemctl restart taosmd"
        real_http_client = app.state.http_client
        app.state.http_client = _FakeHttpClient(
            _FakeResp(200, "application/json", REAL_HEALTH_BODY)
        )
        try:
            with contextlib.ExitStack() as stack:
                for p in _update_route_patches():
                    stack.enter_context(p)
                resp = await client.post("/api/settings/update")
        finally:
            app.state.http_client = real_http_client
            app.state.config.taosmd_dir = ""
            app.state.config.taosmd_restart_cmd = ""

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "restarting"
        assert data["taosmd"]["updated"] is True
        assert "taOSmd updated and verified" in data["message"]

    @pytest.mark.asyncio
    async def test_config_save_round_trip_preserves_taosmd_settings(self, client, app):
        """PUT /api/config rebuilds AppConfig field-by-field; the taOSmd hooks
        must survive the round trip, not silently drop (CodeRabbit find)."""
        import yaml as _yaml

        resp = await client.get("/api/config")
        data = _yaml.safe_load(resp.json()["yaml"])
        data["taosmd_dir"] = "/srv/taosmd"
        data["taosmd_restart_cmd"] = "systemctl restart taosmd"
        try:
            resp = await client.put(
                "/api/config", json={"yaml": _yaml.dump(data)}
            )
            assert resp.status_code == 200, resp.json()
            assert app.state.config.taosmd_dir == "/srv/taosmd"
            assert app.state.config.taosmd_restart_cmd == "systemctl restart taosmd"
            resp = await client.get("/api/config")
            round_tripped = _yaml.safe_load(resp.json()["yaml"])
            assert round_tripped["taosmd_dir"] == "/srv/taosmd"
            assert round_tripped["taosmd_restart_cmd"] == "systemctl restart taosmd"
        finally:
            app.state.config.taosmd_dir = ""
            app.state.config.taosmd_restart_cmd = ""

    @pytest.mark.asyncio
    async def test_update_still_succeeds_when_taosmd_not_configured(self, client):
        """Installs without the taOSmd hooks keep updating exactly as before —
        with the skip REPORTED, not silent."""
        with contextlib.ExitStack() as stack:
            for p in _update_route_patches():
                stack.enter_context(p)
            resp = await client.post("/api/settings/update")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "restarting"
        assert data["taosmd"] == {"skipped": "taosmd_dir not configured"}

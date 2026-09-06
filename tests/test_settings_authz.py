"""Authorization gate for the system-settings router (GHSA-47g9-fwwp-hrfp).

``tinyagentos/routes/settings.py`` previously had no authorization check beyond
"any valid session": a plain non-admin user could read or overwrite system
config (``/api/config``, ``/api/settings/platform``) and trigger updates or
restarts (``/api/settings/update``, ``/api/settings/update-channel``). The only
legitimate callers are an admin session or a caller presenting the host's
local token (see ``tinyagentos/routes/settings.py::_require_admin_or_local_token``
and ``tinyagentos/auth_middleware.py``) -- mirrors the fix already shipped for
skill EXECUTION in ``tests/test_skill_exec_authz.py``.

This suite locks in the fix end-to-end through the real ``AuthMiddleware``:
  (a) a non-admin user session is rejected 403 on every gated endpoint;
  (b) an admin session is allowed;
  (c) a request presenting the valid local token is allowed, with no session
      cookie at all.
"""
from __future__ import annotations

import yaml
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient


async def _member_client(app) -> AsyncClient:
    """Cookie'd client for a non-admin member session on *app*.

    Requires the admin created by the ``client`` fixture (add_user_invite
    checks the inviter is an admin), so callers must depend on ``client`` (or
    otherwise set up the admin) before calling this.
    """
    auth_mgr = app.state.auth
    invite_code = auth_mgr.add_user_invite("member", "admin")
    auth_mgr.complete_invite(
        "member", invite_code, "Test Member", "", "membersettingspass123"
    )
    member = auth_mgr.find_user("member")
    token = auth_mgr.create_session(user_id=member["id"], long_lived=True)
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={"taos_session": token},
    )


@pytest.mark.asyncio
class TestSettingsAuthzNonAdminRejected:
    """(a) A plain non-admin user session must be rejected 403 on every
    sensitive settings endpoint."""

    async def test_get_config_rejected(self, client, app):
        member_client = await _member_client(app)
        try:
            resp = await member_client.get("/api/config")
        finally:
            await member_client.aclose()
        assert resp.status_code == 403

    async def test_put_config_rejected(self, client, app):
        member_client = await _member_client(app)
        try:
            new_yaml = yaml.dump({
                "server": {"host": "0.0.0.0", "port": 9999},
                "backends": [],
                "qmd": {"url": "http://localhost:7832"},
                "agents": [],
                "metrics": {"poll_interval": 60, "retention_days": 7},
            })
            resp = await member_client.put("/api/config", json={"yaml": new_yaml})
        finally:
            await member_client.aclose()
        assert resp.status_code == 403

    async def test_put_platform_settings_rejected(self, client, app):
        member_client = await _member_client(app)
        try:
            resp = await member_client.put(
                "/api/settings/platform",
                json={"poll_interval": 60, "retention_days": 14},
            )
        finally:
            await member_client.aclose()
        assert resp.status_code == 403

    async def test_post_update_rejected(self, client, app):
        member_client = await _member_client(app)
        try:
            with patch("tinyagentos.routes.settings.asyncio.create_subprocess_exec") as mock_exec:
                resp = await member_client.post("/api/settings/update")
        finally:
            await member_client.aclose()
        assert resp.status_code == 403
        mock_exec.assert_not_called()

    async def test_post_update_channel_rejected(self, client, app):
        member_client = await _member_client(app)
        try:
            with patch(
                "tinyagentos.routes.settings._remote_branches",
                new=AsyncMock(return_value=["master", "dev"]),
            ) as mock_branches:
                resp = await member_client.post(
                    "/api/settings/update-channel", json={"branch": "dev"}
                )
        finally:
            await member_client.aclose()
        assert resp.status_code == 403
        mock_branches.assert_not_called()


@pytest.mark.asyncio
class TestSettingsAuthzAdminAllowed:
    """(b) An admin session must not be blocked by the new guard."""

    async def test_get_config_allowed(self, client):
        resp = await client.get("/api/config")
        assert resp.status_code == 200

    async def test_put_config_allowed(self, client):
        new_yaml = yaml.dump({
            "server": {"host": "0.0.0.0", "port": 9999},
            "backends": [],
            "qmd": {"url": "http://localhost:7832"},
            "agents": [],
            "metrics": {"poll_interval": 60, "retention_days": 7},
        })
        resp = await client.put("/api/config", json={"yaml": new_yaml})
        assert resp.status_code == 200

    async def test_put_platform_settings_allowed(self, client):
        resp = await client.put(
            "/api/settings/platform",
            json={"poll_interval": 60, "retention_days": 14},
        )
        assert resp.status_code == 200

    async def test_post_update_allowed(self, client):
        """Mirrors TestUpdateAlwaysRestarts in test_routes_settings.py -- the
        heavy git/pip/restart machinery is mocked out; only the authz gate is
        under test here."""
        fake_proc = MagicMock()
        fake_proc.returncode = 0
        fake_proc.communicate = AsyncMock(return_value=(b"Already up to date.\n", b""))

        async def _fake_restart(_app_state):
            return None

        with (
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
            patch(
                "tinyagentos.routes.system._do_restart",
                new=_fake_restart,
            ),
            patch("tinyagentos.restart_orchestrator.write_pending_restart"),
        ):
            resp = await client.post("/api/settings/update")

        assert resp.status_code != 403
        assert resp.status_code == 200

    async def test_post_update_channel_allowed(self, client, monkeypatch):
        """Mirrors test_noop_on_same_branch in test_update_channel_routes.py --
        the branch is already current so the route short-circuits before any
        real switch machinery runs; only the authz gate is under test here."""
        import tinyagentos.routes.settings as s

        async def fake_lsremote(project_dir):
            return ["master", "dev"]

        async def fake_resolve(store, project_dir):
            return "master"

        monkeypatch.setattr(s, "_remote_branches", fake_lsremote, raising=False)
        monkeypatch.setattr(s, "resolve_tracked_branch", fake_resolve, raising=False)

        resp = await client.post("/api/settings/update-channel", json={"branch": "master"})
        assert resp.status_code != 403
        assert resp.status_code == 200
        assert resp.json()["status"] == "unchanged"


@pytest.mark.asyncio
class TestSettingsAuthzLocalTokenAllowed:
    """(c) A request presenting the valid local token is allowed, with no
    session cookie at all."""

    async def test_local_token_get_config_allowed(self, client, app):
        local_token = app.state.auth.get_local_token()
        bare_client = AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        )
        try:
            resp = await bare_client.get(
                "/api/config",
                headers={"Authorization": f"Bearer {local_token}"},
            )
        finally:
            await bare_client.aclose()
        assert resp.status_code == 200

    async def test_local_token_put_platform_settings_allowed(self, client, app):
        local_token = app.state.auth.get_local_token()
        bare_client = AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        )
        try:
            resp = await bare_client.put(
                "/api/settings/platform",
                json={"poll_interval": 60, "retention_days": 14},
                headers={"Authorization": f"Bearer {local_token}"},
            )
        finally:
            await bare_client.aclose()
        assert resp.status_code == 200

    async def test_no_auth_at_all_rejected(self, client, app):
        """Sanity check: no session, no token at all is rejected by the
        AuthMiddleware session gate before it ever reaches the router guard."""
        bare_client = AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        )
        try:
            resp = await bare_client.get("/api/config")
        finally:
            await bare_client.aclose()
        assert resp.status_code in (401, 403)

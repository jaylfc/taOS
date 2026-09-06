"""Authorization gate for the global-resource routers (tsk-exyzu4).

``AuthMiddleware`` only proves a request carries *a* valid session; whether that
session may touch a given resource is decided per route.  The routers under
test here operate on system-global state -- the secrets keystore, the
controller process, the LLM provider config, MCP server integrations, and
Agent-as-a-Model consent keys -- but carried no authorization dependency at
all, so an invited non-admin member was authorized identically to an admin.

This suite locks in the fix end-to-end through the real ``AuthMiddleware``
and the real CSRF check (the member client echoes the CSRF cookie exactly as
the SPA does, so a 403 here is the AUTHZ gate, never the CSRF gate -- each
rejection asserts the ``forbidden`` body to make that distinction explicit):

  (a) a non-admin member session is rejected 403 on every gated endpoint and
      the underlying action is NOT performed;
  (b) an admin session is allowed (and the app is single-user at that point,
      so single-user installs are provably unaffected);
  (c) the host local token is allowed with no session cookie at all (the path
      deployed agents and ``taosctl`` use);
  (d) the agent-scoped reads stay owner-scoped: a member reads its OWN agent's
      granted secrets and mints a consent key for its OWN agent, but not for
      an agent it does not own.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from taos_test_csrf import csrf_event_hooks

FORBIDDEN = {"detail": "forbidden"}


@pytest_asyncio.fixture(autouse=True)
async def _ensure_agent_model_key_store(client, tmp_path_factory):
    """Init app.state.agent_model_keys on a fresh DB; the test client registers
    the store but does not run the lifespan that init()s it."""
    store = client._transport.app.state.agent_model_keys
    if store._db is not None:
        await store.close()
    store.db_path = tmp_path_factory.mktemp("agent_model_keys_authz") / "keys.db"
    await store.init()
    yield
    await store.close()

# A non-loopback peer address so the loopback-only prepare-shutdown carve-out
# in AuthMiddleware does not apply (the default ASGITransport peer is loopback).
_LAN_PEER = ("192.168.1.10", 51234)


async def _invite_member(app) -> dict:
    """Invite + complete a non-admin member; returns the user record."""
    auth_mgr = app.state.auth
    existing = auth_mgr.find_user("member")
    if existing:
        return existing
    invite_code = auth_mgr.add_user_invite("member", "admin")
    auth_mgr.complete_invite("member", invite_code, "Test Member", "", "memberauthzpass123")
    return auth_mgr.find_user("member")


async def _member_client(app, *, peer=None) -> AsyncClient:
    """Cookie'd, CSRF-armed client for a non-admin member session on *app*.

    Requires the admin created by the ``client`` fixture (add_user_invite
    checks the inviter is an admin).  CSRF is armed on purpose: without it a
    mutating request 403s for the CSRF reason and the authz gate is never
    observed.
    """
    member = await _invite_member(app)
    token = app.state.auth.create_session(user_id=member["id"], long_lived=True)
    kwargs = {"app": app}
    if peer is not None:
        kwargs["client"] = peer
    return AsyncClient(
        transport=ASGITransport(**kwargs),
        base_url="http://test",
        cookies={"taos_session": token},
        event_hooks=csrf_event_hooks(),
    )


def _local_token_client(app) -> AsyncClient:
    local_token = app.state.auth.get_local_token()
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {local_token}"},
    )


async def _init_registry(app):
    registry = app.state.agent_registry
    if registry._db is None:
        await registry.init()
    return registry


async def _close_registry(app):
    registry = app.state.agent_registry
    if registry._db is not None:
        await registry.close()


async def _init_mcp(app):
    """Bring up the MCP store + a stub supervisor on the full app (the
    lifespan owns the real supervisor and the test client bypasses it)."""
    store = app.state.mcp_store
    if store._db is None:
        await store.init()
    supervisor = MagicMock()
    supervisor.get_status = MagicMock(return_value={"status": "stopped"})
    supervisor.start = AsyncMock(return_value=True)
    supervisor.stop = AsyncMock(return_value=True)
    supervisor.restart = AsyncMock(return_value=True)
    supervisor.uninstall = AsyncMock(return_value={"removed_attachments": 0})
    app.state.mcp_supervisor = supervisor
    return store, supervisor


async def _close_mcp(app):
    store = app.state.mcp_store
    if store._db is not None:
        await store.close()
    app.state.mcp_supervisor = None


# ---------------------------------------------------------------------------
# (a) non-admin member rejected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestSecretsMemberRejected:
    async def test_get_secret_rejected(self, client, app):
        await client.post("/api/secrets", json={"name": "openai_key", "value": "sk-real"})
        member = await _member_client(app)
        try:
            resp = await member.get("/api/secrets/openai_key")
        finally:
            await member.aclose()
        assert resp.status_code == 403, resp.text
        assert resp.json() == FORBIDDEN

    async def test_list_secrets_rejected(self, client, app):
        member = await _member_client(app)
        try:
            resp = await member.get("/api/secrets")
        finally:
            await member.aclose()
        assert resp.status_code == 403, resp.text
        assert resp.json() == FORBIDDEN

    async def test_list_categories_rejected(self, client, app):
        member = await _member_client(app)
        try:
            resp = await member.get("/api/secrets/categories")
        finally:
            await member.aclose()
        assert resp.status_code == 403, resp.text
        assert resp.json() == FORBIDDEN

    async def test_add_secret_rejected_and_not_stored(self, client, app):
        member = await _member_client(app)
        try:
            resp = await member.post(
                "/api/secrets", json={"name": "planted", "value": "attacker"}
            )
        finally:
            await member.aclose()
        assert resp.status_code == 403, resp.text
        assert resp.json() == FORBIDDEN
        assert await app.state.secrets.get("planted") is None

    async def test_update_secret_rejected_and_not_applied(self, client, app):
        await client.post("/api/secrets", json={"name": "openai_key", "value": "sk-real"})
        member = await _member_client(app)
        try:
            resp = await member.put("/api/secrets/openai_key", json={"value": "sk-swapped"})
        finally:
            await member.aclose()
        assert resp.status_code == 403, resp.text
        assert resp.json() == FORBIDDEN
        assert (await app.state.secrets.get("openai_key"))["value"] == "sk-real"

    async def test_delete_secret_rejected_and_not_applied(self, client, app):
        await client.post("/api/secrets", json={"name": "openai_key", "value": "sk-real"})
        member = await _member_client(app)
        try:
            resp = await member.delete("/api/secrets/openai_key")
        finally:
            await member.aclose()
        assert resp.status_code == 403, resp.text
        assert resp.json() == FORBIDDEN
        assert await app.state.secrets.get("openai_key") is not None

    async def test_agent_secrets_of_unowned_agent_rejected(self, client, app):
        """Granted secrets are returned in PLAINTEXT by this read, so a member
        must not read them for an agent it does not own -- including an agent
        the registry has never heard of (fail closed, not open)."""
        await client.post(
            "/api/secrets",
            json={"name": "AGENT_SEC", "value": "val", "agents": ["test-agent"]},
        )
        member = await _member_client(app)
        try:
            resp = await member.get("/api/secrets/agent/test-agent")
        finally:
            await member.aclose()
        assert resp.status_code == 403, resp.text
        assert resp.json() == FORBIDDEN

    async def test_agent_secrets_of_admin_owned_agent_rejected(self, client, app):
        registry = await _init_registry(app)
        try:
            admin_uid = app.state.auth.find_user("admin")["id"]
            rec = await registry.register(
                framework="taosmd", display_name="Owner Bot",
                handle="@owner-bot", user_id=admin_uid,
            )
            await client.post(
                "/api/secrets",
                json={"name": "AGENT_SEC", "value": "val", "agents": [rec["canonical_id"]]},
            )
            member = await _member_client(app)
            try:
                resp = await member.get(f"/api/secrets/agent/{rec['canonical_id']}")
            finally:
                await member.aclose()
        finally:
            await _close_registry(app)
        assert resp.status_code == 403, resp.text
        assert resp.json() == FORBIDDEN


@pytest.mark.asyncio
class TestSystemMemberRejected:
    async def test_restart_prepare_rejected_and_no_restart(self, client, app, monkeypatch):
        monkeypatch.setattr(app.state, "orchestrator", MagicMock(), raising=False)
        member = await _member_client(app)
        try:
            with patch("tinyagentos.routes.system._do_restart") as do_restart, patch(
                "tinyagentos.routes.system.write_pending_restart"
            ) as pending:
                resp = await member.post("/api/system/restart/prepare")
        finally:
            await member.aclose()
        assert resp.status_code == 403, resp.text
        assert resp.json() == FORBIDDEN
        do_restart.assert_not_called()
        pending.assert_not_called()

    async def test_prepare_shutdown_from_remote_member_rejected(self, client, app, monkeypatch):
        orchestrator = MagicMock()
        orchestrator.prepare = AsyncMock(return_value={})
        monkeypatch.setattr(app.state, "orchestrator", orchestrator, raising=False)
        member = await _member_client(app, peer=_LAN_PEER)
        try:
            resp = await member.post("/api/system/prepare-shutdown")
        finally:
            await member.aclose()
        assert resp.status_code == 403, resp.text
        assert resp.json() == FORBIDDEN
        orchestrator.prepare.assert_not_called()

    async def test_ai_stack_restart_rejected(self, client, app, monkeypatch):
        from tinyagentos.routes import system as system_routes

        units = AsyncMock(return_value=[("qmd.service", "system")])
        monkeypatch.setattr(system_routes, "_managed_ai_units", units)
        member = await _member_client(app)
        try:
            resp = await member.post("/api/system/ai-stack/restart")
        finally:
            await member.aclose()
        assert resp.status_code == 403, resp.text
        assert resp.json() == FORBIDDEN
        units.assert_not_called()


@pytest.mark.asyncio
class TestProvidersMemberRejected:
    async def test_add_provider_rejected_and_not_saved(self, client, app):
        member = await _member_client(app)
        try:
            with patch("tinyagentos.routes.providers.save_config_locked", new=AsyncMock()) as save:
                resp = await member.post(
                    "/api/providers",
                    json={"name": "evil", "type": "llama-cpp", "url": "http://localhost:9",
                          "api_key": "attacker-key"},
                )
        finally:
            await member.aclose()
        assert resp.status_code == 403, resp.text
        assert resp.json() == FORBIDDEN
        save.assert_not_called()
        assert not any(b["name"] == "evil" for b in app.state.config.backends)

    async def test_patch_provider_rejected_and_not_saved(self, client, app):
        member = await _member_client(app)
        try:
            with patch("tinyagentos.routes.providers.save_config_locked", new=AsyncMock()) as save:
                resp = await member.patch(
                    "/api/providers/test-backend", json={"api_key": "attacker-key"}
                )
        finally:
            await member.aclose()
        assert resp.status_code == 403, resp.text
        assert resp.json() == FORBIDDEN
        save.assert_not_called()
        backend = next(b for b in app.state.config.backends if b["name"] == "test-backend")
        assert "api_key" not in backend

    async def test_delete_provider_rejected_and_not_saved(self, client, app):
        member = await _member_client(app)
        try:
            with patch("tinyagentos.routes.providers.save_config_locked", new=AsyncMock()) as save:
                resp = await member.delete("/api/providers/test-backend")
        finally:
            await member.aclose()
        assert resp.status_code == 403, resp.text
        assert resp.json() == FORBIDDEN
        save.assert_not_called()
        assert any(b["name"] == "test-backend" for b in app.state.config.backends)

    async def test_start_provider_rejected(self, client, app, monkeypatch):
        lifecycle = MagicMock()
        lifecycle.start = AsyncMock()
        monkeypatch.setattr(app.state, "lifecycle_manager", lifecycle, raising=False)
        member = await _member_client(app)
        try:
            resp = await member.post("/api/providers/test-backend/start")
        finally:
            await member.aclose()
        assert resp.status_code == 403, resp.text
        assert resp.json() == FORBIDDEN
        lifecycle.start.assert_not_called()

    async def test_stop_provider_rejected(self, client, app, monkeypatch):
        lifecycle = MagicMock()
        lifecycle.drain_and_stop = AsyncMock()
        monkeypatch.setattr(app.state, "lifecycle_manager", lifecycle, raising=False)
        member = await _member_client(app)
        try:
            resp = await member.post(
                "/api/providers/test-backend/stop", json={"force": True}
            )
        finally:
            await member.aclose()
        assert resp.status_code == 403, resp.text
        assert resp.json() == FORBIDDEN
        lifecycle.drain_and_stop.assert_not_called()

    async def test_list_providers_redacts_inline_api_key_for_member(self, client, app):
        """The list stays readable (the model pickers need it) but inline key
        material is never returned to a non-admin."""
        app.state.config.backends.append({
            "name": "cloudy", "type": "openai", "url": "http://localhost:9",
            "priority": 2, "api_key": "sk-inline-secret", "models": [{"name": "m"}],
        })
        adapter = MagicMock()
        adapter.health = AsyncMock(return_value={"status": "ok", "models": [{"name": "m"}]})
        member = await _member_client(app)
        try:
            with patch("tinyagentos.routes.providers.get_adapter", return_value=adapter):
                resp = await member.get("/api/providers")
        finally:
            await member.aclose()
        assert resp.status_code == 200, resp.text
        cloudy = next(p for p in resp.json() if p["name"] == "cloudy")
        assert "api_key" not in cloudy
        assert "sk-inline-secret" not in resp.text


@pytest.mark.asyncio
class TestMcpMemberRejected:
    @pytest.mark.parametrize(
        "method,path,body",
        [
            ("POST", "/api/mcp/servers/mcp-fetch/start", None),
            ("POST", "/api/mcp/servers/mcp-fetch/stop", None),
            ("POST", "/api/mcp/servers/mcp-fetch/restart", None),
            ("DELETE", "/api/mcp/servers/mcp-fetch", None),
            ("PUT", "/api/mcp/servers/mcp-fetch/config", {"config": {"command": "sh"}}),
            ("GET", "/api/mcp/servers/mcp-fetch/env", None),
            ("PUT", "/api/mcp/servers/mcp-fetch/env", {"env": {"TOKEN": "x"}}),
            (
                "POST",
                "/api/mcp/servers/mcp-fetch/permissions",
                {"scope_kind": "all", "allowed_tools": [], "allowed_resources": []},
            ),
            ("DELETE", "/api/mcp/servers/mcp-fetch/permissions/1", None),
            (
                "POST",
                "/api/mcp/call",
                {"server_id": "mcp-fetch", "tool": "fetch", "agent_name": "bot1"},
            ),
        ],
    )
    async def test_mutation_rejected(self, client, app, method, path, body):
        store, supervisor = await _init_mcp(app)
        try:
            await store.register_server("mcp-fetch", "1.0.0", "stdio")
            # One pre-existing attachment (id 1) so the DELETE case has a
            # real target to fail to remove.
            await store.add_attachment(
                server_id="mcp-fetch", scope_kind="agent", scope_id="bot1",
                allowed_tools=[], allowed_resources=[],
            )
            member = await _member_client(app)
            try:
                with patch(
                    "tinyagentos.routes.mcp.call_tool",
                    new=AsyncMock(return_value={"ok": True, "result": "x"}),
                ) as call_tool:
                    resp = await member.request(method, path, json=body)
            finally:
                await member.aclose()
            assert resp.status_code == 403, resp.text
            assert resp.json() == FORBIDDEN
            # Nothing was performed.
            call_tool.assert_not_called()
            for action in ("start", "stop", "restart", "uninstall"):
                getattr(supervisor, action).assert_not_called()
            assert await store.get_config("mcp-fetch") in (None, {})
            assert len(await store.list_attachments("mcp-fetch")) == 1
            assert await app.state.secrets.get("mcp:mcp-fetch:TOKEN") is None
        finally:
            await _close_mcp(app)


@pytest.mark.asyncio
class TestAgentModelKeysMint:
    async def test_member_cannot_mint_for_admin_owned_agent(self, client, app):
        registry = await _init_registry(app)
        try:
            admin_uid = app.state.auth.find_user("admin")["id"]
            rec = await registry.register(
                framework="taosmd", display_name="Owner Bot",
                handle="@owner-bot", user_id=admin_uid,
            )
            member = await _member_client(app)
            try:
                resp = await member.post(
                    "/api/agent-model-keys", json={"agent_ids": [rec["canonical_id"]]}
                )
            finally:
                await member.aclose()
        finally:
            await _close_registry(app)
        assert resp.status_code == 403, resp.text
        assert resp.json() == FORBIDDEN
        member_uid = app.state.auth.find_user("member")["id"]
        assert await app.state.agent_model_keys.list_for_user(member_uid) == []

    async def test_member_cannot_mint_for_unknown_agent(self, client, app):
        """An id the registry cannot attribute to the caller is not the
        caller's to expose -- fail closed for non-admins."""
        await _init_registry(app)
        try:
            member = await _member_client(app)
            try:
                resp = await member.post(
                    "/api/agent-model-keys", json={"agent_ids": ["some-other-users-agent"]}
                )
            finally:
                await member.aclose()
        finally:
            await _close_registry(app)
        assert resp.status_code == 403, resp.text
        assert resp.json() == FORBIDDEN

    async def test_member_mints_for_own_agent(self, client, app):
        registry = await _init_registry(app)
        try:
            member_rec = await _invite_member(app)
            rec = await registry.register(
                framework="taosmd", display_name="Member Bot",
                handle="@member-bot", user_id=member_rec["id"],
            )
            member = await _member_client(app)
            try:
                resp = await member.post(
                    "/api/agent-model-keys", json={"agent_ids": [rec["canonical_id"]]}
                )
            finally:
                await member.aclose()
        finally:
            await _close_registry(app)
        assert resp.status_code == 200, resp.text
        assert resp.json()["key"].startswith("sk-taosagent-")

    async def test_member_mixed_list_rejected_atomically(self, client, app):
        """One unowned id in the list rejects the whole mint -- no key is
        issued for the owned subset either."""
        registry = await _init_registry(app)
        try:
            member_rec = await _invite_member(app)
            admin_uid = app.state.auth.find_user("admin")["id"]
            own = await registry.register(
                framework="taosmd", display_name="Member Bot",
                handle="@member-bot", user_id=member_rec["id"],
            )
            other = await registry.register(
                framework="taosmd", display_name="Owner Bot",
                handle="@owner-bot", user_id=admin_uid,
            )
            member = await _member_client(app)
            try:
                resp = await member.post(
                    "/api/agent-model-keys",
                    json={"agent_ids": [own["canonical_id"], other["canonical_id"]]},
                )
            finally:
                await member.aclose()
        finally:
            await _close_registry(app)
        assert resp.status_code == 403, resp.text
        assert await app.state.agent_model_keys.list_for_user(member_rec["id"]) == []

    async def test_admin_mints_for_any_agent(self, client, app):
        await _init_registry(app)
        try:
            resp = await client.post(
                "/api/agent-model-keys", json={"agent_ids": ["assistant-20260101-000000"]}
            )
        finally:
            await _close_registry(app)
        assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# (b) admin allowed -- and the app is single-user at that point
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestAdminAllowedSingleUser:
    """The ``client`` fixture is the sole (admin) user: ``is_multi_user`` is
    False, i.e. this is exactly a single-user install.  Every gated endpoint
    keeps answering 2xx for it."""

    async def test_is_single_user(self, client, app):
        assert app.state.auth.is_multi_user() is False

    async def test_secrets_crud_allowed(self, client, app):
        assert app.state.auth.is_multi_user() is False
        assert (await client.post("/api/secrets", json={"name": "k", "value": "v"})).status_code == 200
        assert (await client.get("/api/secrets/k")).status_code == 200
        assert (await client.get("/api/secrets")).status_code == 200
        assert (await client.get("/api/secrets/categories")).status_code == 200
        assert (await client.get("/api/secrets/agent/test-agent")).status_code == 200
        assert (await client.put("/api/secrets/k", json={"value": "v2"})).status_code == 200
        assert (await client.delete("/api/secrets/k")).status_code == 200

    async def test_system_restart_allowed(self, client, app, monkeypatch):
        monkeypatch.setattr(app.state, "orchestrator", MagicMock(), raising=False)
        with patch("tinyagentos.routes.system._do_restart"), patch(
            "tinyagentos.routes.system.write_pending_restart"
        ):
            resp = await client.post("/api/system/restart/prepare")
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"status": "restarting"}

    async def test_prepare_shutdown_from_loopback_hook_allowed(self, client, app, monkeypatch):
        """The systemd stop hook: loopback peer, no session, no token."""
        orchestrator = MagicMock()
        orchestrator.prepare = AsyncMock(return_value={"drained": 0})
        monkeypatch.setattr(app.state, "orchestrator", orchestrator, raising=False)
        bare = AsyncClient(
            transport=ASGITransport(app=app, client=("127.0.0.1", 51234)),
            base_url="http://test",
        )
        try:
            resp = await bare.post("/api/system/prepare-shutdown")
        finally:
            await bare.aclose()
        assert resp.status_code == 200, resp.text
        orchestrator.prepare.assert_awaited_once()

    async def test_providers_mutations_allowed(self, client, app):
        with patch("tinyagentos.routes.providers.save_config_locked", new=AsyncMock()):
            resp = await client.post(
                "/api/providers",
                json={"name": "mine", "type": "llama-cpp", "url": "http://localhost:9"},
            )
            assert resp.status_code == 200, resp.text
            resp = await client.patch("/api/providers/mine", json={"enabled": False})
            assert resp.status_code == 200, resp.text
            resp = await client.delete("/api/providers/mine")
            assert resp.status_code == 200, resp.text

    async def test_list_providers_keeps_inline_api_key_for_admin(self, client, app):
        app.state.config.backends.append({
            "name": "cloudy", "type": "openai", "url": "http://localhost:9",
            "priority": 2, "api_key": "sk-inline-secret", "models": [{"name": "m"}],
        })
        adapter = MagicMock()
        adapter.health = AsyncMock(return_value={"status": "ok", "models": [{"name": "m"}]})
        with patch("tinyagentos.routes.providers.get_adapter", return_value=adapter):
            resp = await client.get("/api/providers")
        assert resp.status_code == 200, resp.text
        cloudy = next(p for p in resp.json() if p["name"] == "cloudy")
        assert cloudy["api_key"] == "sk-inline-secret"

    async def test_mcp_mutations_allowed(self, client, app):
        store, supervisor = await _init_mcp(app)
        try:
            await store.register_server("mcp-fetch", "1.0.0", "stdio")
            assert (await client.post("/api/mcp/servers/mcp-fetch/start")).status_code == 200
            assert (await client.post("/api/mcp/servers/mcp-fetch/stop")).status_code == 200
            assert (await client.post("/api/mcp/servers/mcp-fetch/restart")).status_code == 200
            resp = await client.put(
                "/api/mcp/servers/mcp-fetch/config", json={"config": {"command": "sh"}}
            )
            assert resp.status_code == 200, resp.text
            resp = await client.put(
                "/api/mcp/servers/mcp-fetch/env", json={"env": {"TOKEN": "x"}}
            )
            assert resp.status_code == 200, resp.text
            assert (await client.get("/api/mcp/servers/mcp-fetch/env")).status_code == 200
            resp = await client.post(
                "/api/mcp/servers/mcp-fetch/permissions",
                json={"scope_kind": "all", "allowed_tools": [], "allowed_resources": []},
            )
            assert resp.status_code == 200, resp.text
            attachment_id = resp.json()["attachment_id"]
            resp = await client.delete(
                f"/api/mcp/servers/mcp-fetch/permissions/{attachment_id}"
            )
            assert resp.status_code == 200, resp.text
            with patch(
                "tinyagentos.routes.mcp.call_tool",
                new=AsyncMock(return_value={"ok": True, "result": "x"}),
            ):
                resp = await client.post(
                    "/api/mcp/call",
                    json={"server_id": "mcp-fetch", "tool": "fetch", "agent_name": "bot1"},
                )
            assert resp.status_code == 200, resp.text
            assert (await client.delete("/api/mcp/servers/mcp-fetch")).status_code == 200
        finally:
            await _close_mcp(app)

    async def test_mcp_reads_stay_open_to_members(self, client, app):
        """Non-key-bearing MCP reads are not gated (the MCP app lists servers
        for every signed-in user)."""
        store, _ = await _init_mcp(app)
        try:
            await store.register_server("mcp-fetch", "1.0.0", "stdio")
            member = await _member_client(app)
            try:
                assert (await member.get("/api/mcp/servers")).status_code == 200
                assert (await member.get("/api/mcp/servers/mcp-fetch/config")).status_code == 200
            finally:
                await member.aclose()
        finally:
            await _close_mcp(app)


# ---------------------------------------------------------------------------
# (c) host local token allowed with no session cookie (agents + taosctl)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestLocalTokenAllowed:
    async def test_secrets_via_local_token(self, client, app):
        bare = _local_token_client(app)
        try:
            resp = await bare.post("/api/secrets", json={"name": "k", "value": "v"})
            assert resp.status_code == 200, resp.text
            resp = await bare.get("/api/secrets/k")
            assert resp.status_code == 200, resp.text
            assert resp.json()["value"] == "v"
        finally:
            await bare.aclose()

    async def test_restart_via_local_token(self, client, app, monkeypatch):
        monkeypatch.setattr(app.state, "orchestrator", MagicMock(), raising=False)
        bare = _local_token_client(app)
        try:
            with patch("tinyagentos.routes.system._do_restart"), patch(
                "tinyagentos.routes.system.write_pending_restart"
            ):
                resp = await bare.post("/api/system/restart/prepare")
        finally:
            await bare.aclose()
        assert resp.status_code == 200, resp.text

    async def test_provider_add_via_local_token(self, client, app):
        bare = _local_token_client(app)
        try:
            with patch("tinyagentos.routes.providers.save_config_locked", new=AsyncMock()):
                resp = await bare.post(
                    "/api/providers",
                    json={"name": "cli", "type": "llama-cpp", "url": "http://localhost:9"},
                )
        finally:
            await bare.aclose()
        assert resp.status_code == 200, resp.text

    async def test_mcp_call_via_local_token(self, client, app):
        store, _ = await _init_mcp(app)
        try:
            await store.register_server("mcp-fetch", "1.0.0", "stdio")
            bare = _local_token_client(app)
            try:
                with patch(
                    "tinyagentos.routes.mcp.call_tool",
                    new=AsyncMock(return_value={"ok": True, "result": "x"}),
                ):
                    resp = await bare.post(
                        "/api/mcp/call",
                        json={"server_id": "mcp-fetch", "tool": "fetch", "agent_name": "bot1"},
                    )
            finally:
                await bare.aclose()
        finally:
            await _close_mcp(app)
        assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# (d) owner-scoped agent reads keep working for the owning member
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestOwnerScopedAgentReads:
    async def test_member_reads_own_agent_secrets(self, client, app):
        registry = await _init_registry(app)
        try:
            member_rec = await _invite_member(app)
            rec = await registry.register(
                framework="taosmd", display_name="Member Bot",
                handle="@member-bot", user_id=member_rec["id"],
            )
            await client.post(
                "/api/secrets",
                json={"name": "MEMBER_SEC", "value": "val", "agents": [rec["canonical_id"]]},
            )
            member = await _member_client(app)
            try:
                resp = await member.get(f"/api/secrets/agent/{rec['canonical_id']}")
            finally:
                await member.aclose()
        finally:
            await _close_registry(app)
        assert resp.status_code == 200, resp.text
        assert [s["name"] for s in resp.json()] == ["MEMBER_SEC"]


# ---------------------------------------------------------------------------
# Cluster admin actions (scope extension): the six handlers in
# tinyagentos/routes/cluster.py that mutate the fleet or execute on a worker
# but carried no admin gate, unlike their revoke/block/unblock siblings.
# Worker-facing paths (heartbeat, pairing, leases, capabilities) are HMAC /
# possession gated and are deliberately NOT covered here.
# ---------------------------------------------------------------------------


def _stub_cluster(app, monkeypatch, *, online: bool = True):
    """Replace the cluster manager + task router with call-recording stubs.

    Every stubbed method is asserted NOT called when the gate rejects, which is
    the proof that the handler body never ran (a 403 alone could also be the
    remote-command allowlist check, hence the FORBIDDEN body assertion too)."""
    worker = MagicMock()
    worker.name = "w1"
    worker.status = "online" if online else "offline"
    worker.url = "http://worker.invalid:9000"
    worker.models = ["llama3"]
    worker.capabilities = ["chat"]
    worker.hardware = {}
    cluster = MagicMock()
    cluster.get_worker = MagicMock(return_value=worker)
    cluster.get_workers = MagicMock(return_value=[worker])
    cluster.unregister_worker = AsyncMock(return_value=True)
    task_router = MagicMock()
    task_router.route_request = AsyncMock(return_value=({"ok": True}, "w1"))
    monkeypatch.setattr(app.state, "cluster_manager", cluster, raising=False)
    monkeypatch.setattr(app.state, "task_router", task_router, raising=False)
    return cluster, task_router


_CLUSTER_MEMBER_CASES = [
    ("delete", "/api/cluster/workers/w1", None),
    ("post", "/api/cluster/workers/w1/deploy", {"command": "install-llama-cpp"}),
    ("post", "/api/cluster/workers/w1/remote", {"command": "systemctl status taos-worker"}),
    ("post", "/api/cluster/move", {"item": "llama3", "from_worker": "w1", "to_worker": "w1"}),
    ("post", "/api/cluster/route", {"capability": "chat", "method": "GET", "path": "/health"}),
    ("post", "/api/cluster/promote-archived", None),
]


@pytest.mark.asyncio
class TestClusterMemberRejected:
    @pytest.mark.parametrize("method,path,body", _CLUSTER_MEMBER_CASES)
    async def test_member_rejected_and_handler_not_run(
        self, client, app, monkeypatch, method, path, body
    ):
        cluster, task_router = _stub_cluster(app, monkeypatch)
        member = await _member_client(app)
        try:
            kwargs = {"json": body} if body is not None else {}
            resp = await getattr(member, method)(path, **kwargs)
        finally:
            await member.aclose()
        assert resp.status_code == 403, resp.text
        assert resp.json() == FORBIDDEN
        cluster.get_worker.assert_not_called()
        cluster.get_workers.assert_not_called()
        cluster.unregister_worker.assert_not_called()
        task_router.route_request.assert_not_called()


@pytest.mark.asyncio
class TestClusterAdminAllowed:
    """Single-user admin (and the local token) reach every handler body.  The
    worker stub is offline for deploy/remote so the handler answers its own
    400 'not online' -- proof it ran -- without any outbound HTTP."""

    async def test_admin_reaches_handlers(self, client, app, monkeypatch):
        assert app.state.auth.is_multi_user() is False
        cluster, task_router = _stub_cluster(app, monkeypatch, online=False)
        resp = await client.delete("/api/cluster/workers/w1")
        assert resp.status_code == 200, resp.text
        cluster.unregister_worker.assert_awaited_once_with("w1")
        resp = await client.post(
            "/api/cluster/route", json={"capability": "chat", "method": "GET", "path": "/health"}
        )
        assert resp.status_code == 200, resp.text
        task_router.route_request.assert_awaited_once()
        resp = await client.post(
            "/api/cluster/workers/w1/deploy", json={"command": "install-llama-cpp"}
        )
        assert resp.status_code == 400 and "not online" in resp.text, resp.text
        resp = await client.post(
            "/api/cluster/workers/w1/remote", json={"command": "systemctl status taos-worker"}
        )
        assert resp.status_code == 400 and "not online" in resp.text, resp.text
        resp = await client.post(
            "/api/cluster/move", json={"item": "llama3", "to_worker": "w1"}
        )
        assert resp.status_code == 400 and "not online" in resp.text, resp.text
        resp = await client.post("/api/cluster/promote-archived")
        assert resp.status_code == 200, resp.text
        assert resp.json()["workers_scanned"] == 0

    async def test_local_token_reaches_handlers(self, client, app, monkeypatch):
        cluster, task_router = _stub_cluster(app, monkeypatch)
        bare = _local_token_client(app)
        try:
            resp = await bare.delete("/api/cluster/workers/w1")
            assert resp.status_code == 200, resp.text
            resp = await bare.post("/api/cluster/promote-archived")
            assert resp.status_code == 200, resp.text
        finally:
            await bare.aclose()
        cluster.unregister_worker.assert_awaited_once_with("w1")

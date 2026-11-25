"""A per-agent local token authenticates AS THE AGENT, never as an admin.

Before: the deployer-minted TAOS_LOCAL_TOKEN mapped to the PRIMARY user with
is_admin True, so every deployed agent passed every admin route and a member's
agent was the primary admin. After: via == "agent_token", is_admin False,
user_id = the agent's registry OWNER. Everything here goes through the real
AuthMiddleware and the real routes.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from fastapi import Request
from httpx import ASGITransport, AsyncClient

ADMIN_SAMPLE = [
    ("GET", "/api/settings/update-status"),
    ("POST", "/api/settings/update"),
    ("GET", "/api/settings/storage"),
    ("GET", "/api/config"),
    ("GET", "/api/secrets"),
    ("GET", "/api/kg/stats"),
    ("POST", "/api/kg/triples"),
    ("GET", "/api/kg/entities"),
    ("POST", "/api/agents/deploy"),
    ("POST", "/api/agents/bulk/stop"),
    ("GET", "/api/memory/settings"),
    ("PUT", "/api/desktop/dock"),
    ("POST", "/api/desktop/browser/agent-command"),
    ("POST", "/api/mcp/call"),
    ("GET", "/api/tasks"),
    ("GET", "/api/hardware/wifi"),
    ("GET", "/auth/users"),
]


async def _init_registry(app):
    registry = app.state.agent_registry
    if registry._db is None:
        await registry.init()
    return registry


@pytest_asyncio.fixture
async def world(app):
    """Admin (primary) + a member; the member owns agent 'mem-bot', the admin
    owns 'adm-bot'. Each has a bound per-agent token."""
    auth = app.state.auth
    auth.setup_user("admin", "Test Admin", "", "testpass")
    code = auth.add_user_invite("member", "admin")
    auth.complete_invite("member", code, "Test Member", "", "memberpass12345")
    admin = auth.find_user("admin")
    member = auth.find_user("member")
    registry = await _init_registry(app)
    cfg = app.state.config
    out = {"admin": admin, "member": member, "tokens": {}}
    for name, owner in (("mem-bot", member), ("adm-bot", admin)):
        rec = await registry.register(
            framework="taosmd", display_name=name, handle=f"@{name}", user_id=owner["id"]
        )
        cfg.agents.append({"name": name, "registry_canonical_id": rec["canonical_id"]})
        out["tokens"][name] = auth.mint_agent_local_token(name, rec["canonical_id"])
    out["host"] = auth.get_local_token()

    async def probe(request: Request):  # reports exactly what the middleware stamped
        s = request.state
        return {
            "user_id": getattr(s, "user_id", None),
            "is_admin": getattr(s, "is_admin", None),
            "via": getattr(s, "via", None),
            "agent_name": getattr(s, "agent_name", None),
        }

    app.router.routes.insert(0, __import__("fastapi").routing.APIRoute("/api/_scope_probe", probe, methods=["GET"]))
    app.state._startup_complete = True
    yield out
    if registry._db is not None:
        await registry.close()


def _bearer(app, token: str) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )


@pytest.mark.asyncio
class TestAgentTokenIdentity:
    async def test_member_agent_is_its_owner_not_primary_not_admin(self, app, world):
        async with _bearer(app, world["tokens"]["mem-bot"]) as c:
            body = (await c.get("/api/_scope_probe")).json()
        assert body["via"] == "agent_token"
        assert body["is_admin"] is False
        assert body["agent_name"] == "mem-bot"
        assert body["user_id"] == world["member"]["id"]
        assert body["user_id"] != world["admin"]["id"]

    async def test_admins_agent_acts_for_the_admin_but_is_not_admin(self, app, world):
        async with _bearer(app, world["tokens"]["adm-bot"]) as c:
            body = (await c.get("/api/_scope_probe")).json()
        assert body["via"] == "agent_token"
        assert body["is_admin"] is False
        assert body["user_id"] == world["admin"]["id"]

    async def test_host_token_is_unchanged(self, app, world):
        async with _bearer(app, world["host"]) as c:
            body = (await c.get("/api/_scope_probe")).json()
        assert body == {
            "user_id": world["admin"]["id"],
            "is_admin": True,
            "via": "local_token",
            "agent_name": None,
        }

    async def test_unattributable_agent_has_no_user(self, app, world):
        """A bound agent the registry cannot attribute never falls back to the primary user."""
        app.state.config.agents.append({"name": "orphan-bot"})
        token = app.state.auth.mint_agent_local_token("orphan-bot")
        async with _bearer(app, token) as c:
            body = (await c.get("/api/_scope_probe")).json()
        assert body["via"] == "agent_token"
        assert body["user_id"] is None
        assert body["is_admin"] is False


@pytest.mark.asyncio
class TestAgentTokenRefusedOnAdminRoutes:
    @pytest.mark.parametrize("method,path", ADMIN_SAMPLE)
    async def test_member_agent_refused(self, app, world, method, path):
        async with _bearer(app, world["tokens"]["mem-bot"]) as c:
            resp = await c.request(method, path, json={})
        assert resp.status_code == 403, (method, path, resp.status_code, resp.text[:200])

    @pytest.mark.parametrize("method,path", ADMIN_SAMPLE)
    async def test_admins_agent_refused(self, app, world, method, path):
        async with _bearer(app, world["tokens"]["adm-bot"]) as c:
            resp = await c.request(method, path, json={})
        assert resp.status_code == 403, (method, path, resp.status_code, resp.text[:200])

    @pytest.mark.parametrize(
        "method,path",
        [("GET", "/api/settings/storage"), ("GET", "/api/kg/stats"), ("GET", "/api/secrets"),
         ("GET", "/api/tasks"), ("GET", "/api/memory/settings")],
    )
    async def test_host_token_still_admitted(self, app, world, method, path):
        async with _bearer(app, world["host"]) as c:
            try:
                resp = await c.request(method, path)
            except Exception:  # noqa: BLE001 - reached the handler (store not wired in this fixture)
                return
        assert resp.status_code not in (401, 403), (path, resp.status_code, resp.text[:200])


@pytest.mark.asyncio
class TestAgentRoutesStillWork:
    async def test_skill_exec_tools_own_agent_only(self, app, world):
        app.state.skills = MagicMock()
        app.state.skills.get_agent_skills = AsyncMock(return_value=[])
        async with _bearer(app, world["tokens"]["mem-bot"]) as c:
            own = await c.get("/api/skill-exec/tools", params={"agent_name": "mem-bot"})
            other = await c.get("/api/skill-exec/tools", params={"agent_name": "adm-bot"})
        assert own.status_code == 200, own.text
        assert other.status_code == 403

    async def test_skill_exec_call_runs_for_agent_but_code_exec_refused(self, app, world):
        app.state.skills = MagicMock()
        app.state.skills.get_skill = AsyncMock(return_value={"id": "code_exec"})
        app.state.skills.get_agent_skills = AsyncMock(return_value=[{"id": "code_exec"}])
        app.state.execution_policies = MagicMock()
        app.state.execution_policies.effective_effect = AsyncMock(return_value="allow")
        async with _bearer(app, world["tokens"]["mem-bot"]) as c:
            resp = await c.post(
                "/api/skill-exec/code_exec/call", json={"args": {"code": "print(1)"}}
            )
        # the route admits the agent; the host-RCE skill's own check refuses it
        assert resp.status_code == 403, resp.text
        assert "not available to agent tokens" in resp.text

    async def test_skill_exec_call_cannot_name_another_agent(self, app, world):
        async with _bearer(app, world["tokens"]["mem-bot"]) as c:
            resp = await c.post(
                "/api/skill-exec/memory_search/call",
                json={"agent_name": "adm-bot", "args": {}},
            )
        assert resp.status_code == 403

    async def test_openclaw_bootstrap_fenced_to_own_agent(self, app, world):
        async with _bearer(app, world["tokens"]["mem-bot"]) as c:
            other = await c.get("/api/openclaw/bootstrap", params={"agent": "adm-bot"})
            own = await c.get("/api/openclaw/bootstrap", params={"agent": "mem-bot"})
        assert other.status_code == 403
        assert own.status_code != 403

    async def test_openclaw_events_fenced_to_own_agent(self, app, world):
        async with _bearer(app, world["tokens"]["mem-bot"]) as c:
            other = await c.get("/api/openclaw/sessions/adm-bot/events")
        assert other.status_code == 403

    async def test_memory_search_fenced_to_own_agent(self, app, world):
        async with _bearer(app, world["tokens"]["mem-bot"]) as c:
            other = await c.post("/api/memory/search", json={"query": "x", "agent": "adm-bot"})
        assert other.status_code == 403

    async def test_thinking_own_slug_only(self, app, world):
        async with _bearer(app, world["tokens"]["mem-bot"]) as c:
            other = await c.post(
                "/api/chat/channels/c1/thinking", json={"slug": "adm-bot", "state": "start"}
            )
        assert other.status_code == 403

    async def test_delegate_only_as_itself(self, app, world):
        async with _bearer(app, world["tokens"]["mem-bot"]) as c:
            resp = await c.post(
                "/api/agents/adm-bot/delegate",
                json={"to_agent": "mem-bot", "task_title": "t"},
            )
        assert resp.status_code == 403

    async def test_agent_list_shows_only_itself(self, app, world):
        async with _bearer(app, world["tokens"]["mem-bot"]) as c:
            resp = await c.get("/api/agents")
        assert resp.status_code == 200
        assert [a["name"] for a in resp.json()] == ["mem-bot"]


@pytest.mark.asyncio
class TestChannelReadScope:
    async def test_agent_reads_only_channels_it_is_member_of(self, app, world):
        ch = app.state.chat_channels
        if ch._db is None:
            await ch.init()
        mine = await ch.create_channel(name="dm-mine", type="dm", created_by="user",
                                       members=["mem-bot", world["member"]["id"]])
        other = await ch.create_channel(name="dm-other", type="dm", created_by="user",
                                        members=["adm-bot", world["admin"]["id"]])
        async with _bearer(app, world["tokens"]["mem-bot"]) as c:
            ok = await c.get(f"/api/chat/channels/{mine['id']}")
            no = await c.get(f"/api/chat/channels/{other['id']}")
        assert ok.status_code == 200, ok.text
        assert no.status_code == 403
        async with _bearer(app, world["host"]) as c:
            assert (await c.get(f"/api/chat/channels/{other['id']}")).status_code == 200
        await ch.close()


@pytest.mark.asyncio
class TestCrystallizeCarriesAgent:
    async def test_crystallize_write_gets_the_session_agent(self, tmp_path, monkeypatch):
        import json
        from tinyagentos.scheduling.job_worker import JobWorker
        import taosmd.crystallize as crystallize_mod

        seen = {}

        async def fake_crystallize(self, **kw):
            seen.update(kw)
            return {"id": "c1"}

        monkeypatch.setattr(crystallize_mod.CrystalStore, "crystallize", fake_crystallize)

        class FakeCatalog:
            def __init__(self, *a, **k): ...
            async def init(self): ...
            async def close(self): ...
            async def get_session_context(self, sid):
                return {"content_lines": [json.dumps({"summary": "hi", "timestamp": 1})],
                        "agent_name": "mem-bot"}

        import taosmd.session_catalog as sc
        monkeypatch.setattr(sc, "SessionCatalog", FakeCatalog)
        worker = JobWorker.__new__(JobWorker)
        worker._llm_url = "http://x"
        worker._path = lambda payload, key, default: str(tmp_path / default)
        res = await worker._do_crystallize({"session_id": "s1"})
        assert res["status"] == "crystallized"
        assert seen["agent_name"] == "mem-bot"
        seen.clear()
        await worker._do_crystallize({"session_id": "s1", "agent_name": "payload-bot"})
        assert seen["agent_name"] == "payload-bot"


@pytest.mark.asyncio
class TestOutsideTheMatrix:
    async def test_taosgo_app_join_refuses_agent_token(self, app, world):
        async with _bearer(app, world["tokens"]["mem-bot"]) as c:
            resp = await c.post("/api/taosgo/app-join")
        assert resp.status_code == 403, resp.text
        assert "preauth" not in resp.text

    async def test_taosgo_app_join_host_token_still_works(self, app, world):
        async with _bearer(app, world["host"]) as c:
            resp = await c.post("/api/taosgo/app-join")
        assert resp.status_code == 200, resp.text
        assert "preauth_key" in resp.json()

    @pytest.mark.parametrize("method,path", [
        ("GET", "/api/cluster/leases"),
        ("POST", "/api/cluster/leases/claim"),
        ("POST", "/api/cluster/leases/release"),
    ])
    async def test_cluster_leases_refuse_agent_token(self, app, world, method, path):
        body = {"resource_id": "gpu0", "lease_id": "x"}
        async with _bearer(app, world["tokens"]["mem-bot"]) as c:
            resp = await c.request(method, path, json=body if method == "POST" else None)
        assert resp.status_code == 403, (path, resp.status_code, resp.text[:200])

    async def test_cluster_leases_host_token_still_admitted(self, app, world):
        async with _bearer(app, world["host"]) as c:
            resp = await c.get("/api/cluster/leases")
        assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
class TestOwnerlessAgentMigration:
    async def test_old_config_agent_gets_the_install_owner_and_can_reply(self, app, world):
        from tinyagentos.migrations.agent_owner import assign_ownerless_agents

        cfg = app.state.config
        cfg.agents.append({"name": "old-bot"})  # an old config row: no registry id
        token = app.state.auth.mint_agent_local_token("old-bot")
        ch = app.state.chat_channels
        if ch._db is None:
            await ch.init()
        if app.state.chat_messages._db is None:
            await app.state.chat_messages.init()
        if app.state.device_store._db is None:
            await app.state.device_store.init()
        channel = await ch.create_channel(name="dm", type="dm", created_by="user",
                                          members=["old-bot", world["admin"]["id"]])
        body = {"channel_id": channel["id"], "content": "hi"}
        async with _bearer(app, token) as c:
            before = await c.post("/api/chat/messages", json=body)
        assert before.status_code in (401, 403)

        owned_before = {a["name"]: a.get("registry_canonical_id") for a in cfg.agents if a["name"] in ("mem-bot", "adm-bot")}
        assigned = await assign_ownerless_agents(cfg, app.state.agent_registry, app.state.auth)
        assert "old-bot" in assigned and not {"mem-bot", "adm-bot"} & set(assigned)
        row = await app.state.agent_registry.get(
            next(a for a in cfg.agents if a["name"] == "old-bot")["registry_canonical_id"])
        assert row["user_id"] == world["admin"]["id"]
        # agents that already had an owner are untouched
        assert {a["name"]: a.get("registry_canonical_id") for a in cfg.agents if a["name"] in ("mem-bot", "adm-bot")} == owned_before
        # idempotent
        assert await assign_ownerless_agents(cfg, app.state.agent_registry, app.state.auth) == []

        async with _bearer(app, token) as c:
            after = await c.post("/api/chat/messages", json=body)
        assert after.status_code == 200, after.text


@pytest.mark.asyncio
class TestEmptyOwnerAdoption:
    async def test_row_with_empty_owner_goes_to_install_owner_and_owned_rows_stay(self, app, world):
        from tinyagentos.migrations.agent_owner import assign_ownerless_agents

        reg = app.state.agent_registry
        cfg = app.state.config
        empty = await reg.register(framework="none", display_name="Empty", user_id="")
        kept = await reg.register(framework="none", display_name="Kept", user_id="someone-else")
        cfg.agents.append({"name": "empty-bot", "registry_canonical_id": empty["canonical_id"]})
        cfg.agents.append({"name": "kept-bot", "registry_canonical_id": kept["canonical_id"]})
        assigned = await assign_ownerless_agents(cfg, reg, app.state.auth)
        assert "empty-bot" in assigned and "kept-bot" not in assigned
        assert (await reg.get(empty["canonical_id"]))["user_id"] == world["admin"]["id"]
        assert (await reg.get(kept["canonical_id"]))["user_id"] == "someone-else"


@pytest.mark.asyncio
class TestChatReadsAndSkillAssignment:
    async def test_member_session_cannot_read_a_channel_it_is_not_in(self, app, world):
        ch = app.state.chat_channels
        if ch._db is None:
            await ch.init()
        if app.state.chat_messages._db is None:
            await app.state.chat_messages.init()
        other = await ch.create_channel(name="adm-dm", type="dm", created_by="user",
                                        members=["adm-bot", world["admin"]["id"]])
        token = app.state.auth.create_session(user_id=world["member"]["id"], long_lived=True)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                               cookies={"taos_session": token}) as c:
            for path in (f"/api/chat/channels/{other['id']}",
                         f"/api/chat/channels/{other['id']}/messages",
                         f"/api/chat/channels/{other['id']}/threads",
                         f"/api/chat/channels/{other['id']}/pins"):
                assert (await c.get(path)).status_code == 403, path

    async def test_unassigned_skill_is_refused_for_agent_token(self, app, world):
        app.state.skills = MagicMock()
        app.state.skills.get_skill = AsyncMock(return_value={"id": "memory_search"})
        app.state.skills.get_agent_skills = AsyncMock(return_value=[])
        async with _bearer(app, world["tokens"]["mem-bot"]) as c:
            resp = await c.post("/api/skill-exec/memory_search/call", json={"args": {}})
        assert resp.status_code == 403
        assert "not assigned" in resp.text

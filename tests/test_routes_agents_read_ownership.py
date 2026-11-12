"""Per-agent reads belong to the agent's owner, and no read returns llm_key.

llm_key is the agent's bearer credential (``_resolve_agent_by_bearer``), so a
response that carries it lets the reader act as the agent.
"""
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from taos_test_csrf import csrf_event_hooks

ALPHA_KEY = "sk-agent-ALPHA-SECRET-7731"
GHOST_KEY = "sk-agent-GHOST-SECRET-4410"


def _add_member(app, username: str, password: str) -> str:
    invite = app.state.auth.add_user_invite(username, "admin")
    app.state.auth.complete_invite(username, invite, username.title(), f"{username}@test.com", password)
    return app.state.auth.find_user(username)["id"]


def _add_agent(app, name: str, **extra) -> dict:
    agent = {"name": name, "host": "127.0.0.1", "qmd_index": "test", "color": "#cccccc", **extra}
    app.state.config.agents.append(agent)
    return agent


@pytest_asyncio.fixture
async def owner_clients(app, client):
    """``(alice, bob)`` member clients; agent ``alpha`` (with an llm_key) is alice's."""
    alice_uid = _add_member(app, "alice", "alicepass1")
    bob_uid = _add_member(app, "bob", "bobspass11")
    registry = app.state.agent_registry
    if registry._db is None:
        await registry.init()
    row = await registry.register(framework="claude-code", display_name="alpha", user_id=alice_uid)
    _add_agent(app, "alpha", llm_key=ALPHA_KEY, registry_canonical_id=row["canonical_id"])
    app.state.config.archived_agents.append(
        {"id": "ghost-1", "archived_slug": "ghost", "archive_dir": "archive/ghost-1",
         "original": {"name": "ghost", "llm_key": GHOST_KEY}}
    )

    clients = []
    for uid in (alice_uid, bob_uid):
        token = app.state.auth.create_session(user_id=uid, long_lived=True)
        clients.append(AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test",
            cookies={"taos_session": token}, event_hooks=csrf_event_hooks(),
        ))
    try:
        yield clients[0], clients[1]
    finally:
        for c in clients:
            await c.aclose()
        await registry.close()


PER_AGENT_READS = [
    "/api/agents/alpha",
    "/api/agents/alpha/export",
    "/api/agents/alpha/logs",
    "/api/agents/alpha/budget",
    "/api/agents/alpha/deploy-status",
]


class TestAgentReadOwnership:

    @pytest.mark.asyncio
    @pytest.mark.parametrize("path", PER_AGENT_READS)
    async def test_other_member_is_refused(self, owner_clients, path):
        _alice, bob = owner_clients
        resp = await bob.get(path)
        assert resp.status_code == 403, (path, resp.status_code, resp.text)
        assert ALPHA_KEY not in resp.text

    @pytest.mark.asyncio
    async def test_owner_can_read_and_export(self, owner_clients):
        alice, _bob = owner_clients
        assert (await alice.get("/api/agents/alpha")).status_code == 200
        resp = await alice.get("/api/agents/alpha/export")
        assert resp.status_code == 200
        assert resp.json()["agent"]["name"] == "alpha"
        assert (await alice.get("/api/agents/alpha/budget")).status_code == 200

    @pytest.mark.asyncio
    async def test_member_list_shows_only_own_agents(self, owner_clients, app):
        _add_agent(app, "orphan")  # config-only, no registry row: nobody's to list
        alice, bob = owner_clients
        assert [a["name"] for a in (await alice.get("/api/agents")).json()] == ["alpha"]
        assert (await bob.get("/api/agents")).json() == []
        assert (await bob.get("/api/agents/archived")).json() == []

    @pytest.mark.asyncio
    async def test_admin_still_sees_every_agent(self, owner_clients, client, app):
        _add_agent(app, "orphan")
        names = {a["name"] for a in (await client.get("/api/agents")).json()}
        assert {"alpha", "orphan"} <= names
        assert (await client.get("/api/agents/alpha/export")).status_code == 200


class TestNoReadReturnsLlmKey:
    """Not even to the owner or an admin: import re-mints the key, so it has no use here."""

    @pytest.mark.asyncio
    async def test_owner_reads_carry_no_llm_key(self, owner_clients):
        alice, _bob = owner_clients
        for path in ("/api/agents", "/api/agents/alpha", "/api/agents/alpha/export"):
            resp = await alice.get(path)
            assert resp.status_code == 200, path
            assert ALPHA_KEY not in resp.text, path
            assert "llm_key" not in resp.text, path

    @pytest.mark.asyncio
    async def test_admin_reads_carry_no_llm_key(self, owner_clients, client):
        for path in (
            "/api/agents", "/api/agents/alpha", "/api/agents/alpha/export", "/api/agents/archived",
        ):
            resp = await client.get(path)
            assert resp.status_code == 200, path
            assert ALPHA_KEY not in resp.text and GHOST_KEY not in resp.text, path
            assert "llm_key" not in resp.text, path

    @pytest.mark.asyncio
    async def test_stripping_does_not_touch_stored_config(self, owner_clients, client, app):
        await client.get("/api/agents/alpha/export")
        await client.get("/api/agents")
        stored = next(a for a in app.state.config.agents if a["name"] == "alpha")
        assert stored["llm_key"] == ALPHA_KEY

    @pytest.mark.asyncio
    async def test_deploy_status_carries_no_llm_key(self, owner_clients, client, app):
        app.state.deploy_tasks["alpha"] = {
            "status": "success", "name": "alpha", "result": {"success": True, "llm_key": ALPHA_KEY},
        }
        alice, _bob = owner_clients
        for c in (alice, client):
            resp = await c.get("/api/agents/alpha/deploy-status")
            assert resp.status_code == 200
            assert resp.json()["result"] == {"success": True}
        assert app.state.deploy_tasks["alpha"]["result"]["llm_key"] == ALPHA_KEY


class TestArchivedOwnership:
    """An archive entry's ``id`` is the config id; the owner is found through
    ``original.registry_canonical_id``, never the archive id or the reusable slug."""

    @pytest_asyncio.fixture
    async def archived(self, owner_clients, app):
        alice, bob = owner_clients
        alice_uid = app.state.auth.find_user("alice")["id"]
        row = await app.state.agent_registry.register(
            framework="claude-code", display_name="beta", user_id=alice_uid
        )
        app.state.config.archived_agents.append(
            {"id": "a1b2c3d4e5f6", "archived_slug": "beta",
             "original": {"name": "beta", "registry_canonical_id": row["canonical_id"]}}
        )
        return alice, bob

    @pytest.mark.asyncio
    async def test_owner_sees_own_archived_agent(self, archived):
        alice, bob = archived
        assert [e["id"] for e in (await alice.get("/api/agents/archived")).json()] == ["a1b2c3d4e5f6"]
        assert (await bob.get("/api/agents/archived")).json() == []

    @pytest.mark.asyncio
    async def test_other_member_cannot_restore_or_purge(self, archived):
        _alice, bob = archived
        assert (await bob.post("/api/agents/archived/a1b2c3d4e5f6/restore")).status_code == 403
        assert (await bob.delete("/api/agents/archived/a1b2c3d4e5f6")).status_code == 403

    @pytest.mark.asyncio
    async def test_owner_passes_the_restore_and_purge_guard(self, archived, monkeypatch):
        from tinyagentos.routes import agent_archive

        async def _fake(request, archive_id):
            return {"status": "ok", "id": archive_id}

        monkeypatch.setattr(agent_archive, "restore_archived", _fake)
        monkeypatch.setattr(agent_archive, "purge_archived", _fake)
        alice, _bob = archived
        assert (await alice.post("/api/agents/archived/a1b2c3d4e5f6/restore")).status_code == 200
        assert (await alice.delete("/api/agents/archived/a1b2c3d4e5f6")).status_code == 200

    @pytest.mark.asyncio
    async def test_unattributed_archive_is_admin_only(self, archived, client):
        # alice owns registry rows with no handle; an empty owner id must not resolve to them
        for c in archived:
            assert (await c.post("/api/agents/archived/ghost-1/restore")).status_code == 403
            assert "ghost-1" not in {e["id"] for e in (await c.get("/api/agents/archived")).json()}
        assert "ghost-1" in {e["id"] for e in (await client.get("/api/agents/archived")).json()}


class TestDedupedNameOwnership:
    """Deploy gives a repeated display name a unique config name (``helper-2``)
    while the registry slug stays ``helper``; the owner is the config entry's
    ``registry_canonical_id``, never whoever holds the ``helper-2`` slug."""

    @pytest_asyncio.fixture
    async def deduped(self, owner_clients, app):
        alice, bob = owner_clients
        uid = {u: app.state.auth.find_user(u)["id"] for u in ("alice", "bob")}
        reg = app.state.agent_registry
        a = await reg.register(framework="claude-code", display_name="helper", user_id=uid["alice"])
        b = await reg.register(framework="claude-code", display_name="helper", user_id=uid["bob"])
        # alice also holds the slug bob's config name decodes to
        await reg.register(framework="claude-code", display_name="helper-2", user_id=uid["alice"])
        _add_agent(app, "helper", registry_canonical_id=a["canonical_id"])
        _add_agent(app, "helper-2", registry_canonical_id=b["canonical_id"])
        return alice, bob

    @pytest.mark.asyncio
    async def test_owner_of_deduped_name_reads_it(self, deduped):
        alice, bob = deduped
        assert (await bob.get("/api/agents/helper-2")).status_code == 200
        assert "helper-2" in [a["name"] for a in (await bob.get("/api/agents")).json()]

    @pytest.mark.asyncio
    async def test_slug_holder_does_not_own_deduped_name(self, deduped):
        alice, _bob = deduped
        assert (await alice.get("/api/agents/helper-2")).status_code == 403
        assert "helper-2" not in [a["name"] for a in (await alice.get("/api/agents")).json()]

    @pytest.mark.asyncio
    async def test_name_without_config_entry_still_resolves_by_slug(self, deduped, app):
        from types import SimpleNamespace
        from tinyagentos.auth_context import resolve_agent_owner

        req = SimpleNamespace(app=app)
        alice_uid = app.state.auth.find_user("alice")["id"]
        assert await resolve_agent_owner(req, "alpha") == alice_uid  # config entry, canonical id
        assert await resolve_agent_owner(req, "helper-2") != alice_uid
        app.state.config.agents = [x for x in app.state.config.agents if x["name"] != "alpha"]
        assert await resolve_agent_owner(req, "alpha") == alice_uid  # no config entry: slug chain

    @pytest.mark.asyncio
    async def test_configured_entry_without_canonical_id_is_admin_only(self, deduped, app, client):
        # imported and config-only agents never get a registry row, so the name
        # can only ever match someone else's slug (alice holds helper-2's)
        alice, _bob = deduped
        bob_agent = next(a for a in app.state.config.agents if a["name"] == "helper-2")
        del bob_agent["registry_canonical_id"]
        assert (await alice.get("/api/agents/helper-2")).status_code == 403
        assert "helper-2" not in [a["name"] for a in (await alice.get("/api/agents")).json()]
        assert (await client.get("/api/agents/helper-2")).status_code == 200


class TestBoundAgentToken:
    """A per-agent local token is marked admin by the middleware, so every list
    and archive route must fence it to its own agent before the admin bypass."""

    @pytest_asyncio.fixture
    async def beta(self, owner_clients, app):
        _add_agent(app, "beta")
        token = app.state.auth.mint_agent_local_token("beta")
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test",
            headers={"Authorization": f"Bearer {token}"}, event_hooks=csrf_event_hooks(),
        ) as c:
            yield c

    @pytest.mark.asyncio
    async def test_list_shows_only_the_bound_agent(self, beta):
        assert [a["name"] for a in (await beta.get("/api/agents")).json()] == ["beta"]
        assert (await beta.get("/api/agents/archived")).json() == []

    @pytest.mark.asyncio
    async def test_cannot_restore_or_purge_archives(self, beta, app):
        assert (await beta.post("/api/agents/archived/ghost-1/restore")).status_code == 403
        assert (await beta.delete("/api/agents/archived/ghost-1")).status_code == 403
        assert any(e["id"] == "ghost-1" for e in app.state.config.archived_agents)

    @pytest.mark.asyncio
    async def test_container_list_shows_only_the_bound_agent(self, beta, monkeypatch):
        _fake_containers(monkeypatch, "alpha", "beta")
        assert [c["agent_name"] for c in (await beta.get("/api/agents/containers")).json()] == ["beta"]

    @pytest.mark.asyncio
    async def test_redeploy_under_the_name_revokes_the_old_token(self, app):
        auth = app.state.auth
        old = auth.mint_agent_local_token("beta")
        new = auth.mint_agent_local_token("beta")
        assert not auth.validate_local_token(old)
        assert auth.validate_local_token(new)
        assert auth.get_local_token_agent(new) == "beta"


def _fake_containers(monkeypatch, *names):
    from types import SimpleNamespace
    import tinyagentos.containers as containers

    async def fake_list(prefix=""):
        return [
            SimpleNamespace(name=f"taos-agent-{n}", status="Running", ip="10.0.0.9", memory_mb=512, cpu_cores=1)
            for n in names
        ]
    monkeypatch.setattr(containers, "list_containers", fake_list)


class TestContainerListOwnership:

    @pytest.mark.asyncio
    async def test_members_see_only_their_own_containers(self, owner_clients, monkeypatch):
        _fake_containers(monkeypatch, "alpha", "orphan")
        alice, bob = owner_clients
        assert [c["agent_name"] for c in (await alice.get("/api/agents/containers")).json()] == ["alpha"]
        assert (await bob.get("/api/agents/containers")).json() == []

    @pytest.mark.asyncio
    async def test_admin_sees_every_container(self, owner_clients, client, monkeypatch):
        _fake_containers(monkeypatch, "alpha", "orphan")
        names = [c["agent_name"] for c in (await client.get("/api/agents/containers")).json()]
        assert names == ["alpha", "orphan"]


class TestLocalTokenRevokedOnRemoval:
    """A local token is bound by name, so it must not outlive the agent: a later
    deploy under the same name would otherwise inherit it."""

    @pytest.mark.asyncio
    async def test_purge_revokes_the_archived_agents_token(self, owner_clients, client, app, monkeypatch):
        import tinyagentos.containers as containers

        async def fake_destroy(name):
            return {"success": True}
        monkeypatch.setattr(containers, "destroy_container", fake_destroy)
        token = app.state.auth.mint_agent_local_token("ghost")
        assert (await client.delete("/api/agents/archived/ghost-1")).status_code == 200
        assert not app.state.auth.validate_local_token(token)

    @pytest.mark.asyncio
    async def test_purge_keeps_the_token_of_a_live_agent_with_the_name(self, owner_clients, client, app, monkeypatch):
        import tinyagentos.containers as containers

        async def fake_destroy(name):
            return {"success": True}
        monkeypatch.setattr(containers, "destroy_container", fake_destroy)
        _add_agent(app, "ghost")
        token = app.state.auth.mint_agent_local_token("ghost")
        assert (await client.delete("/api/agents/archived/ghost-1")).status_code == 200
        assert app.state.auth.validate_local_token(token)

    def test_archive_revokes_the_agents_token(self, app):
        from types import SimpleNamespace
        from tinyagentos.routes.agent_archive import _revoke_gateway_keys

        token = app.state.auth.mint_agent_local_token("alpha")
        _revoke_gateway_keys(SimpleNamespace(app=app), "alpha", None)
        assert not app.state.auth.validate_local_token(token)

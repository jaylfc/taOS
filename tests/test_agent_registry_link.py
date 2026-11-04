"""Admin-only linking of a pre-registry config agent to its registry row.

A config agent with no ``registry_canonical_id`` is nobody's: the owner check
refuses to guess from its name, because any member can mint a registry row
whose slug equals it. An admin can list the candidate rows and link one
explicitly, after which the row's owner can reach the agent.
"""
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from taos_test_csrf import csrf_event_hooks

from tinyagentos.config import load_config


def _add_member(app, username: str, password: str) -> str:
    invite = app.state.auth.add_user_invite(username, "admin")
    app.state.auth.complete_invite(username, invite, username.title(), f"{username}@test.com", password)
    return app.state.auth.find_user(username)["id"]


@pytest_asyncio.fixture
async def link_env(app, client):
    """Unlinked config agent ``alpha``; alice and bob each own a registry row."""
    alice_uid = _add_member(app, "alice", "alicepass1")
    bob_uid = _add_member(app, "bob", "bobspass11")
    registry = app.state.agent_registry
    if registry._db is None:
        await registry.init()
    alice_row = await registry.register(framework="claude-code", display_name="alpha", user_id=alice_uid)
    inactive_row = await registry.register(framework="claude-code", display_name="alpha", user_id=bob_uid)
    await registry.set_status(inactive_row["canonical_id"], "revoked")
    # A second active "alpha" row (bob's): candidates are hints, all are listed.
    bob_row = await registry.register(framework="claude-code", display_name="alpha", user_id=bob_uid)
    # A sibling slug must not be offered as a candidate for "alpha".
    await registry.register(framework="claude-code", display_name="alpha-beta", user_id=bob_uid)
    app.state.config.agents.append({
        "name": "alpha", "host": "127.0.0.1", "qmd_index": "test", "color": "#cccccc",
    })
    clients = []
    for uid in (alice_uid, bob_uid):
        token = app.state.auth.create_session(user_id=uid, long_lived=True)
        clients.append(AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test",
            cookies={"taos_session": token}, event_hooks=csrf_event_hooks(),
        ))
    try:
        yield {
            "alice": clients[0], "bob": clients[1], "alice_uid": alice_uid,
            "row": alice_row, "inactive": inactive_row, "bob_row": bob_row,
        }
    finally:
        for c in clients:
            await c.aclose()
        await registry.close()


def _alpha(app) -> dict:
    return next(a for a in app.state.config.agents if a["name"] == "alpha")


@pytest.mark.asyncio
async def test_member_cannot_list_or_link(link_env, app):
    bob = link_env["bob"]
    assert (await bob.get("/api/agents/registry-links")).status_code == 403
    resp = await bob.post(
        "/api/agents/alpha/registry-link", json={"canonical_id": link_env["row"]["canonical_id"]},
    )
    assert resp.status_code == 403
    assert not _alpha(app).get("registry_canonical_id")


@pytest.mark.asyncio
async def test_admin_lists_active_exact_slug_candidates(link_env, client):
    resp = await client.get("/api/agents/registry-links")
    assert resp.status_code == 200
    entry = next(e for e in resp.json()["unlinked"] if e["name"] == "alpha")
    ids = [c["canonical_id"] for c in entry["candidates"]]
    assert ids == [link_env["row"]["canonical_id"], link_env["bob_row"]["canonical_id"]]
    cand = entry["candidates"][0]
    assert cand["user_id"] == link_env["alice_uid"]
    assert set(cand) >= {"canonical_id", "user_id", "display_name", "created_ts"}


@pytest.mark.asyncio
async def test_admin_link_grants_owner_access(link_env, app, client):
    alice, bob = link_env["alice"], link_env["bob"]
    assert (await alice.get("/api/agents/alpha/messages")).status_code == 403
    cid = link_env["row"]["canonical_id"]
    resp = await client.post("/api/agents/alpha/registry-link", json={"canonical_id": cid})
    assert resp.status_code == 200
    body = resp.json()
    assert body["agent"]["registry_canonical_id"] == cid
    assert body["user_id"] == link_env["alice_uid"]
    assert (await alice.get("/api/agents/alpha/messages")).status_code == 200
    assert (await bob.get("/api/agents/alpha/messages")).status_code == 403
    listed = (await client.get("/api/agents/registry-links")).json()["unlinked"]
    assert not any(e["name"] == "alpha" for e in listed)


@pytest.mark.asyncio
async def test_link_survives_config_reload(link_env, app, client):
    cid = link_env["row"]["canonical_id"]
    assert (await client.post("/api/agents/alpha/registry-link", json={"canonical_id": cid})).status_code == 200
    reloaded = load_config(app.state.config.config_path)
    agent = next(a for a in reloaded.agents if a["name"] == "alpha")
    assert agent["registry_canonical_id"] == cid


@pytest.mark.asyncio
async def test_relink_is_conflict_and_unchanged(link_env, app, client):
    cid = link_env["row"]["canonical_id"]
    assert (await client.post("/api/agents/alpha/registry-link", json={"canonical_id": cid})).status_code == 200
    other = await app.state.agent_registry.register(framework="x", display_name="alpha", user_id="someone")
    resp = await client.post("/api/agents/alpha/registry-link", json={"canonical_id": other["canonical_id"]})
    assert resp.status_code == 409
    assert _alpha(app)["registry_canonical_id"] == cid


@pytest.mark.asyncio
async def test_inactive_or_missing_row_refused(link_env, app, client):
    resp = await client.post(
        "/api/agents/alpha/registry-link", json={"canonical_id": link_env["inactive"]["canonical_id"]},
    )
    assert resp.status_code == 400
    resp = await client.post(
        "/api/agents/alpha/registry-link", json={"canonical_id": "alpha-20200101-000000"},
    )
    assert resp.status_code == 404
    assert not _alpha(app).get("registry_canonical_id")


@pytest.mark.asyncio
async def test_unknown_agent_is_404(link_env, client):
    resp = await client.post(
        "/api/agents/nosuch/registry-link", json={"canonical_id": link_env["row"]["canonical_id"]},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_linked_agent_is_409_even_for_a_bad_row(link_env, app, client):
    cid = link_env["row"]["canonical_id"]
    assert (await client.post("/api/agents/alpha/registry-link", json={"canonical_id": cid})).status_code == 200
    for bad in (link_env["inactive"]["canonical_id"], "alpha-20200101-000000"):
        resp = await client.post("/api/agents/alpha/registry-link", json={"canonical_id": bad})
        assert resp.status_code == 409
    assert _alpha(app)["registry_canonical_id"] == cid


@pytest.mark.asyncio
async def test_a_row_already_linked_to_another_agent_is_409(link_env, app, client):
    cid = link_env["row"]["canonical_id"]
    app.state.config.agents.append({
        "name": "gamma", "host": "127.0.0.1", "qmd_index": "test", "color": "#cccccc",
    })
    assert (await client.post("/api/agents/alpha/registry-link", json={"canonical_id": cid})).status_code == 200
    resp = await client.post("/api/agents/gamma/registry-link", json={"canonical_id": cid})
    assert resp.status_code == 409
    gamma = next(a for a in app.state.config.agents if a["name"] == "gamma")
    assert not gamma.get("registry_canonical_id")

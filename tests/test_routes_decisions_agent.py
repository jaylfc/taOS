"""Agent-token path for POST /api/decisions (decisions_write grant gating)."""
import pytest
from httpx import ASGITransport, AsyncClient

from tinyagentos.agent_registry_store import mint_registry_token


async def _mint_agent(app, project_id, scopes, handle="@taOS-dev"):
    """Register an active agent, grant it *scopes* for *project_id*, return
    (canonical_id, bearer_token). project_id=None grants globally."""
    registry = app.state.agent_registry
    grants = app.state.agent_grants
    for store in (registry, grants):
        if store._db is None:
            await store.init()
    priv, _pub = app.state.agent_registry_keypair
    rec = await registry.register(
        framework="claude-code",
        display_name="taOS dev",
        origin="internal",
        handle=handle,
    )
    cid = rec["canonical_id"]
    if rec.get("status") != "active":
        await registry.set_status(cid, "active")
    for scope in scopes:
        await grants.add_grant(cid, scope, project_id=project_id)
    token = mint_registry_token(
        cid, priv, user_id="u", framework="claude-code", project_id=project_id
    )
    return cid, token


def _agent_client(app, token):
    """Cookieless client that authenticates only via the agent bearer token."""
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )


async def _new_project(client, name="alpha", slug="alpha"):
    resp = await client.post("/api/projects", json={"name": name, "slug": slug})
    assert resp.status_code in (200, 201), resp.text
    return resp.json()["id"]


def _decision_body(**over):
    body = {"from_agent": "spoofed", "question": "ship it?", "type": "approve_deny"}
    body.update(over)
    return body


@pytest.mark.asyncio
async def test_agent_with_project_grant_posts(client):
    """A granted agent posts into its project: attributed to the agent, decided
    by the project owner, from_agent not taken from the (spoofed) body."""
    app = client._transport.app
    admin_id = app.state.auth.find_user("admin")["id"]
    pid = await _new_project(client)  # owned by the admin session
    cid, token = await _mint_agent(app, pid, ("decisions_write",))

    async with _agent_client(app, token) as ac:
        resp = await ac.post("/api/decisions", json=_decision_body(project_id=pid))
    assert resp.status_code == 200, resp.text
    d = resp.json()
    assert d["from_agent"] == cid          # authenticated identity, not "spoofed"
    assert d["user_id"] == admin_id        # project owner, resolved not caller
    assert d["project_id"] == pid


@pytest.mark.asyncio
async def test_agent_global_grant_posts_os_level(client):
    """A global (null-project) grant lets the agent raise an OS-level decision,
    decided by the instance admin."""
    app = client._transport.app
    admin_id = app.state.auth.find_user("admin")["id"]
    cid, token = await _mint_agent(app, None, ("decisions_write",))

    async with _agent_client(app, token) as ac:
        resp = await ac.post("/api/decisions", json=_decision_body())  # no project_id
    assert resp.status_code == 200, resp.text
    d = resp.json()
    assert d["from_agent"] == cid
    assert d["user_id"] == admin_id
    assert d["project_id"] is None


@pytest.mark.asyncio
async def test_agent_global_grant_403_into_project(client):
    """A global grant is not a skeleton key: posting into a specific project
    without a per-project grant is 403."""
    app = client._transport.app
    pid = await _new_project(client)
    _cid, token = await _mint_agent(app, None, ("decisions_write",))

    async with _agent_client(app, token) as ac:
        resp = await ac.post("/api/decisions", json=_decision_body(project_id=pid))
    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_agent_without_decisions_grant_403(client):
    """A valid agent token with some other scope but no decisions_write is 403."""
    app = client._transport.app
    pid = await _new_project(client)
    _cid, token = await _mint_agent(app, pid, ("project_tasks",))  # wrong scope

    async with _agent_client(app, token) as ac:
        resp = await ac.post("/api/decisions", json=_decision_body(project_id=pid))
    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_human_path_unchanged(client):
    """The session user path still works: the decision is attributed to the
    body's from_agent and decided by the session user."""
    app = client._transport.app
    admin_id = app.state.auth.find_user("admin")["id"]
    resp = await client.post("/api/decisions", json=_decision_body(from_agent="@taOS-dev"))
    assert resp.status_code == 200, resp.text
    d = resp.json()
    assert d["from_agent"] == "@taOS-dev"
    assert d["user_id"] == admin_id

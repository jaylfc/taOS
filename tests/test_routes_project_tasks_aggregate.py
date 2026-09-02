"""Cross-project kanban aggregate (Kanban Viewer S1) authorization tests.

``GET /api/projects/tasks/aggregate`` is a READ-ONLY cross-project board: it
returns every project the caller is authorized to see, each with that project's
tasks.  The whole risk is the aggregate leaking a project the caller is NOT
entitled to, so these tests pin the authorization boundary hard -- a test that
only proves an entitled caller sees their own board cannot fail on a leak, so
every test here asserts the ABSENCE of the other project:

  * a session owner entitled to project A must NOT receive project B;
  * an agent holding ``project_tasks`` for A (and not B) must NOT receive B;
  * an admin sees every active project's board (the one case allowed to span).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from tinyagentos.agent_registry_store import mint_registry_token
from taos_test_csrf import csrf_event_hooks


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def members(client):
    """Two non-admin member clients (alice, bob) sharing the admin `client` app.

    Reuses the fully-initialised conftest `client` (which inits every store and
    the admin user) and just adds two member identities + sessions on top.
    """
    app = client._transport.app
    auth = app.state.auth

    def _add(username: str, password: str) -> str:
        invite = auth.add_user_invite(username, invited_by_username="admin")
        auth.complete_invite(
            username=username,
            invite_code=invite,
            full_name="Member User",
            email=f"{username}@test.local",
            password=password,
        )
        return auth.find_user(username)["id"]

    alice_uid = _add("alice", "alicepass1")
    bob_uid = _add("bob", "bobspass1")
    alice_token = auth.create_session(user_id=alice_uid, long_lived=True)
    bob_token = auth.create_session(user_id=bob_uid, long_lived=True)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies={"taos_session": alice_token},
        event_hooks=csrf_event_hooks(),
    ) as alice:
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
            cookies={"taos_session": bob_token},
            event_hooks=csrf_event_hooks(),
        ) as bob:
            yield alice, bob


@pytest_asyncio.fixture
async def agent_ctx(client):
    """Reuse the session-admin `client` and additionally init the agent registry
    + grants stores so a project-bound token can be minted against real stores."""
    app = client._transport.app
    for attr in ("agent_registry", "agent_grants"):
        store = getattr(app.state, attr)
        if store._db is None:
            await store.init()
    yield SimpleNamespace(client=client, app=app)
    for attr in ("agent_registry", "agent_grants"):
        store = getattr(app.state, attr)
        if store._db is not None:
            await store.close()


def _bare(app):
    """Cookieless client so requests carry only the Bearer header."""
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _new_project(client, slug: str) -> str:
    resp = await client.post("/api/projects", json={"name": slug, "slug": slug})
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


async def _new_task(client, pid: str, title: str = "T") -> str:
    resp = await client.post(f"/api/projects/{pid}/tasks", json={"title": title})
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


async def _mint_agent(ctx, project_id, scopes=("project_tasks",)):
    registry = ctx.app.state.agent_registry
    grants = ctx.app.state.agent_grants
    priv, _pub = ctx.app.state.agent_registry_keypair
    rec = await registry.register(
        framework="grok",
        display_name="Grok",
        origin="external-selfjoin",
        handle="@grok",
    )
    cid = rec["canonical_id"]
    await registry.set_status(cid, "active")
    for scope in scopes:
        await grants.add_grant(cid, scope, project_id=project_id)
    token = mint_registry_token(
        cid, priv, user_id="u", framework="grok", project_id=project_id
    )
    return cid, token


def _hdr(token):
    return {"Authorization": f"Bearer {token}"}


def _project_ids(resp) -> set[str]:
    assert resp.status_code == 200, resp.text
    return {item["project_id"] for item in resp.json()["items"]}


def _task_ids(resp, project_id: str) -> set[str]:
    assert resp.status_code == 200, resp.text
    for item in resp.json()["items"]:
        if item["project_id"] == project_id:
            return {t["id"] for t in item["tasks"]}
    return set()


# ---------------------------------------------------------------------------
# Session-owner authorization boundary
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_owner_aggregate_must_not_include_other_project(members):
    """THE acceptance: alice entitled to A asks for the aggregate and must NOT
    receive B.  A test that only checked "alice sees A" could not fail on a
    leak, so this asserts BOTH that A is present and B is absent."""
    alice, bob = members
    pid_a = await _new_project(alice, "alpha")
    tid_a = await _new_task(alice, pid_a, "alice task")
    pid_b = await _new_project(bob, "bravo")
    tid_b = await _new_task(bob, pid_b, "bob task")

    resp = await alice.get("/api/projects/tasks/aggregate")

    ids = _project_ids(resp)
    assert pid_a in ids
    assert pid_b not in ids
    # A's board carries A's task; B's task is nowhere in the aggregate.
    assert tid_a in _task_ids(resp, pid_a)
    assert tid_b not in {t["id"] for it in resp.json()["items"] for t in it["tasks"]}


@pytest.mark.asyncio
async def test_owner_aggregate_empty_when_no_projects(members):
    """A caller with no projects gets an empty aggregate, not an error."""
    alice, bob = members
    resp = await alice.get("/api/projects/tasks/aggregate")
    assert resp.status_code == 200
    assert resp.json()["items"] == []


# ---------------------------------------------------------------------------
# Aggregate shape
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_aggregate_shape(client):
    """Each entry carries project_id / name / slug and a tasks array whose
    entries carry a valid status (open | claimed | closed)."""
    pid = await _new_project(client, "shape")
    tid = await _new_task(client, pid, "shape task")

    resp = await client.get("/api/projects/tasks/aggregate")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    item = items[0]
    assert item["project_id"] == pid
    assert item["name"] == "shape"
    assert item["slug"] == "shape"
    assert isinstance(item["tasks"], list)
    assert len(item["tasks"]) == 1
    task = item["tasks"][0]
    assert task["id"] == tid
    assert task["status"] in ("open", "claimed", "closed")


@pytest.mark.asyncio
async def test_aggregate_status_filter(client):
    """The optional status query filters tasks to the requested status only."""
    pid = await _new_project(client, "filter")
    await _new_task(client, pid, "open one")
    await _new_task(client, pid, "open two")

    resp = await client.get("/api/projects/tasks/aggregate?status=open")
    assert resp.status_code == 200
    tasks = [t for it in resp.json()["items"] for t in it["tasks"]]
    assert len(tasks) == 2
    assert all(t["status"] == "open" for t in tasks)

    resp = await client.get("/api/projects/tasks/aggregate?status=claimed")
    assert resp.status_code == 200
    tasks = [t for it in resp.json()["items"] for t in it["tasks"]]
    assert tasks == []


# ---------------------------------------------------------------------------
# Admin sees all active projects
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_admin_sees_all_active_projects(members, client):
    """The admin is entitled to every active project's board."""
    alice, bob = members
    pid_a = await _new_project(alice, "adm-alpha")
    pid_b = await _new_project(bob, "adm-bravo")

    resp = await client.get("/api/projects/tasks/aggregate")
    ids = _project_ids(resp)
    assert pid_a in ids
    assert pid_b in ids


# ---------------------------------------------------------------------------
# Agent-token authorization boundary
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_agent_aggregate_must_not_include_ungranted_project(agent_ctx):
    """An agent holding project_tasks for A (and not B) must see A's board and
    NOT B's in the aggregate -- the grant is the sole per-project gate."""
    pid_a = await _new_project(agent_ctx.client, "agent-alpha")
    tid_a = await _new_task(agent_ctx.client, pid_a, "granted task")
    pid_b = await _new_project(agent_ctx.client, "agent-bravo")
    tid_b = await _new_task(agent_ctx.client, pid_b, "ungranted task")

    _cid, token = await _mint_agent(agent_ctx, pid_a)

    async with _bare(agent_ctx.app) as bare:
        resp = await bare.get("/api/projects/tasks/aggregate", headers=_hdr(token))

    ids = _project_ids(resp)
    assert pid_a in ids
    assert pid_b not in ids
    assert tid_a in _task_ids(resp, pid_a)
    assert tid_b not in {t["id"] for it in resp.json()["items"] for t in it["tasks"]}


@pytest.mark.asyncio
async def test_agent_aggregate_empty_without_project_tasks_grant(agent_ctx):
    """An active agent with NO project_tasks grant sees an empty aggregate
    (no leak, no crash)."""
    pid = await _new_project(agent_ctx.client, "no-grant")
    await _new_task(agent_ctx.client, pid)

    _cid, token = await _mint_agent(agent_ctx, pid, scopes=("a2a_receive",))

    async with _bare(agent_ctx.app) as bare:
        resp = await bare.get("/api/projects/tasks/aggregate", headers=_hdr(token))

    assert resp.status_code == 200
    assert resp.json()["items"] == []


# ---------------------------------------------------------------------------
# Regression: status enum validation (Kilo #1)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_aggregate_rejects_invalid_status(client):
    """A status outside open|claimed|closed is rejected 400 up front, not
    silently filtered to an empty board (which would hide a caller's typo)."""
    pid = await _new_project(client, "bad-status")
    await _new_task(client, pid, "t")

    resp = await client.get("/api/projects/tasks/aggregate?status=garbage")
    assert resp.status_code == 400

    # The documented enum still passes (validation is exact, not over-broad).
    for ok in ("open", "claimed", "closed"):
        r = await client.get(f"/api/projects/tasks/aggregate?status={ok}")
        assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# Regression: archived-project exclusion inside the loop (Kilo #3)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_aggregate_excludes_project_archived_after_listing(members, monkeypatch):
    """A project archived between the candidate listing and the per-project
    check must NOT leak into the response (the 'active only' contract).

    This exercises the TOCTOU race itself: the candidate snapshot is captured
    BEFORE the archive (so it still reports ``active``), the project is then
    archived, and ``_caller_project_candidates`` hands back the stale snapshot.
    The handler must re-read the FRESH project (via ``_authorize_task_actor``)
    and exclude it, rather than trusting the stale ``active`` snapshot.
    """
    alice, _bob = members
    pstore = alice._transport.app.state.project_store
    pid_keep = await _new_project(alice, "keep-active")
    await _new_task(alice, pid_keep, "kept")
    pid_arch = await _new_project(alice, "archive-me")
    await _new_task(alice, pid_arch, "archived")

    # Capture the snapshot BEFORE archiving so it still reports "active".
    arch_snapshot = await pstore.get_project(pid_arch)
    assert arch_snapshot["status"] == "active"
    # ...THEN the race: the project is archived after the listing ran.
    await pstore.set_status(pid_arch, "archived")
    keep = await pstore.get_project(pid_keep)

    import tinyagentos.routes.projects as prj

    async def _fake_candidates(request, _pstore):
        # Simulate a listing that ran BEFORE the archive and still returns the
        # now-stale "active" snapshot alongside the active project.
        return [keep, arch_snapshot], None, None

    monkeypatch.setattr(prj, "_caller_project_candidates", _fake_candidates)

    resp = await alice.get("/api/projects/tasks/aggregate")
    assert resp.status_code == 200
    ids = _project_ids(resp)
    assert pid_keep in ids
    assert pid_arch not in ids


# ---------------------------------------------------------------------------
# Regression: per-project authorization failure is skipped, not raised (Kilo #4)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_aggregate_skips_project_on_auth_failure(client, monkeypatch):
    """A per-project authorization failure (e.g. agent suspended, token
    superseded, grant revoked mid-iteration) skips that project instead of
    aborting the whole aggregate with an unhandled 403/500."""
    pid_ok = await _new_project(client, "auth-ok")
    await _new_task(client, pid_ok, "kept")
    pid_bad = await _new_project(client, "auth-bad")
    await _new_task(client, pid_bad, "dropped")

    import tinyagentos.routes.projects as prj
    from fastapi import HTTPException

    real = prj._authorize_task_actor

    async def _flaky(request, pstore, project_id, **kwargs):
        if project_id == pid_bad:
            raise HTTPException(status_code=403, detail="agent is not active in the registry")
        return await real(request, pstore, project_id, **kwargs)

    monkeypatch.setattr(prj, "_authorize_task_actor", _flaky)

    resp = await client.get("/api/projects/tasks/aggregate")
    assert resp.status_code == 200
    ids = _project_ids(resp)
    assert pid_ok in ids
    assert pid_bad not in ids

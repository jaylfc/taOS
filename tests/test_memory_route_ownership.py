"""Memory routes require that the caller owns the agent.

Drives the REAL app (AuthMiddleware + CSRF) with two non-admin member
sessions, each owning one agent, a fake qmd that honours ``dbPath`` over real
SQLite files, and checks all four agent-scoped routes:

  GET    /api/memory/browse
  POST   /api/memory/search
  GET    /api/memory/collections/{agent_name}
  DELETE /api/memory/chunk/{hash}

Expectations: other member 403, owner 200, admin 200, bound agent token 200
for its own agent and 403 for another, anonymous 401, and a refused request
creates nothing on disk.
"""
from __future__ import annotations

import json
import sqlite3
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from taos_test_csrf import csrf_event_hooks

BOB_HASH = "BOB-HASH"
ALICE_HASH = "ALICE-HASH"
CANARY = "BOB-SECRET-CANARY"


def _make_index(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE IF NOT EXISTS chunks (hash TEXT PRIMARY KEY, collection TEXT, body TEXT)")
    con.executemany("INSERT OR REPLACE INTO chunks VALUES (?,?,?)", rows)
    con.commit()
    con.close()


def _fake_qmd(request: httpx.Request) -> httpx.Response:
    """Stands in for ``qmd serve``; honours ``dbPath`` like the real service."""
    parsed = urlparse(str(request.url))
    if request.method == "GET":
        query = parse_qs(parsed.query)
        db = query["dbPath"][0]
    else:
        body = json.loads(request.content)
        db = body["dbPath"]
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE IF NOT EXISTS chunks (hash TEXT PRIMARY KEY, collection TEXT, body TEXT)")
    rows = lambda sql, *a: [  # noqa: E731
        {"hash": h, "collection": c, "body": b} for h, c, b in con.execute(sql, a).fetchall()
    ]
    try:
        if parsed.path == "/browse":
            out = {"chunks": rows("SELECT hash, collection, body FROM chunks")}
        elif parsed.path == "/search":
            needle = parse_qs(parsed.query)["q"][0]
            out = {"results": rows(
                "SELECT hash, collection, body FROM chunks WHERE body LIKE ?", f"%{needle}%")}
        elif parsed.path == "/collections":
            out = [r[0] for r in con.execute("SELECT DISTINCT collection FROM chunks")]
        elif parsed.path == "/delete-chunk":
            cur = con.execute("DELETE FROM chunks WHERE hash = ?", (body["hash"],))
            con.commit()
            out = {"status": "ok", "deleted": cur.rowcount}
        else:
            return httpx.Response(404)
    finally:
        con.close()
    return httpx.Response(200, json=out)


def _add_member(app, username: str) -> str:
    auth = app.state.auth
    code = auth.add_user_invite(username, invited_by_username="admin")
    auth.complete_invite(username=username, invite_code=code, full_name=username,
                         email=f"{username}@test.local", password=f"{username}-pass-123")
    return auth.find_user(username)["id"]


def _session_client(app, user_id: str) -> AsyncClient:
    token = app.state.auth.create_session(user_id=user_id, long_lived=True)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       cookies={"taos_session": token}, event_hooks=csrf_event_hooks())


def _bearer_client(app, token: str) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       headers={"Authorization": f"Bearer {token}"})


async def _register(app, name: str, owner_id: str, *, display_name: str | None = None) -> str:
    """Register *name* as a deployed agent owned by *owner_id*; return its canonical id."""
    registry = app.state.agent_registry
    rec = await registry.register(framework="claude-code", display_name=display_name or name,
                                  handle=name, user_id=owner_id)
    app.state.config.agents.append({"name": name, "registry_canonical_id": rec["canonical_id"]})
    return rec["canonical_id"]


@pytest_asyncio.fixture
async def world(app):
    app.state.auth.setup_user("admin", "Admin", "", "adminpass1")
    admin_id = app.state.auth.find_user("admin")["id"]
    alice_id = _add_member(app, "alice")
    bob_id = _add_member(app, "bob")
    registry = app.state.agent_registry
    if registry._db is None:
        await registry.init()
    mem = app.state.agent_memory_dir
    _make_index(mem / "bob-agent" / "index.sqlite", [(BOB_HASH, "private", f"bob notes {CANARY}")])
    _make_index(mem / "alice-agent" / "index.sqlite", [(ALICE_HASH, "private", "alice notes")])
    await _register(app, "bob-agent", bob_id)
    await _register(app, "alice-agent", alice_id)
    app.state.qmd_client.base_url = "http://qmd.test"
    app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(_fake_qmd))
    app.state._startup_complete = True
    yield {"app": app, "mem": mem, "admin": admin_id, "alice": alice_id, "bob": bob_id}
    await app.state.http_client.aclose()
    await registry.close()


def _bob_has_chunk(mem) -> bool:
    con = sqlite3.connect(mem / "bob-agent" / "index.sqlite")
    try:
        return bool(con.execute("SELECT 1 FROM chunks WHERE hash=?", (BOB_HASH,)).fetchone())
    finally:
        con.close()


async def _call(c: AsyncClient, route: str, agent: str):
    if route == "browse":
        return await c.get("/api/memory/browse", params={"agent": agent})
    if route == "search":
        return await c.post("/api/memory/search",
                            json={"query": "notes", "mode": "keyword", "agent": agent})
    if route == "collections":
        return await c.get(f"/api/memory/collections/{agent}")
    if route == "delete":
        h = BOB_HASH if agent == "bob-agent" else ALICE_HASH
        return await c.delete(f"/api/memory/chunk/{h}", params={"agent": agent})
    raise AssertionError(route)


ROUTES = ["browse", "search", "collections", "delete"]


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ROUTES)
async def test_other_member_refused_and_nothing_leaks(world, route):
    async with _session_client(world["app"], world["alice"]) as alice:
        r = await _call(alice, route, "bob-agent")
    assert r.status_code == 403, r.text
    assert CANARY not in r.text
    assert _bob_has_chunk(world["mem"])


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ROUTES)
async def test_owner_allowed(world, route):
    async with _session_client(world["app"], world["bob"]) as bob:
        r = await _call(bob, route, "bob-agent")
    assert r.status_code == 200, r.text
    if route == "delete":
        assert not _bob_has_chunk(world["mem"])
    elif route in ("browse", "search"):
        assert CANARY in r.text


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ROUTES)
async def test_admin_allowed(world, route):
    async with _session_client(world["app"], world["admin"]) as admin:
        r = await _call(admin, route, "bob-agent")
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ROUTES)
async def test_host_local_token_allowed(world, route):
    token = world["app"].state.auth.get_local_token()
    async with _bearer_client(world["app"], token) as host:
        r = await _call(host, route, "bob-agent")
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ROUTES)
async def test_anonymous_unauthorised(world, route):
    async with AsyncClient(transport=ASGITransport(app=world["app"]), base_url="http://test",
                           event_hooks=csrf_event_hooks()) as anon:
        r = await _call(anon, route, "bob-agent")
    assert r.status_code == 401, r.text
    assert _bob_has_chunk(world["mem"])


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ["browse", "search", "collections"])
async def test_bound_agent_token_own_ok_other_refused(world, route):
    app = world["app"]
    tok = app.state.auth.mint_agent_local_token("alice-agent")
    async with _bearer_client(app, tok) as agent:
        own = await _call(agent, route, "alice-agent")
        other = await _call(agent, route, "bob-agent")
    assert own.status_code == 200, own.text
    assert other.status_code == 403, other.text
    assert CANARY not in other.text


@pytest.mark.asyncio
async def test_bound_agent_token_cannot_delete_anything(world):
    app = world["app"]
    tok = app.state.auth.mint_agent_local_token("alice-agent")
    async with _bearer_client(app, tok) as agent:
        other = await _call(agent, "delete", "bob-agent")
        own = await _call(agent, "delete", "alice-agent")
    assert other.status_code == 403
    assert own.status_code == 403
    assert _bob_has_chunk(world["mem"])


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ROUTES)
async def test_refused_request_creates_no_directory(world, route):
    """A refused request must not mkdir the target agent's memory directory."""
    app, mem = world["app"], world["mem"]
    # carol-agent is bob's, has no memory directory yet; ghost-agent is unregistered.
    await _register(app, "carol-agent", world["bob"])
    for name in ("carol-agent", "ghost-agent"):
        assert not (mem / name).exists()
        async with _session_client(app, world["alice"]) as alice:
            r = await _call(alice, route, name)
        assert r.status_code == 403, (name, r.text)
        assert not (mem / name).exists(), f"{route} created {name} directory"


@pytest.mark.asyncio
@pytest.mark.parametrize("route", ROUTES)
async def test_lookalike_names_resolve_to_the_configured_owner(world, route):
    """Agent names are slugs anyone can mint as a display name.

    alice owns the configured agent ``helper``; bob registers an identity whose
    display name slugs to the same ``helper``. bob must not read alice's
    ``helper`` memory, and alice (the configured owner) still can.
    """
    app, mem = world["app"], world["mem"]
    await _register(app, "helper", world["alice"])
    _make_index(mem / "helper" / "index.sqlite", [("H", "c", "helper notes")])
    await app.state.agent_registry.register(
        framework="claude-code", display_name="Helper", handle="helper-bob", user_id=world["bob"])
    async with _session_client(app, world["bob"]) as bob:
        refused = await _call(bob, route, "helper")
    async with _session_client(app, world["alice"]) as alice:
        allowed = await _call(alice, route, "helper")
    assert refused.status_code == 403, refused.text
    assert allowed.status_code in (200,), allowed.text


# ------------------------------------------------ no-agent form: per-person index

def _no_agent_row(mem, who_dir, h, body):
    root = mem.parent / "user-qmd-index"
    p = (root / who_dir / "index.sqlite") if who_dir else (root / "index.sqlite")
    _make_index(p, [(h, "notes", body)])
    return p


def _has(path, h):
    con = sqlite3.connect(path)
    try:
        return bool(con.execute("SELECT 1 FROM chunks WHERE hash=?", (h,)).fetchone())
    finally:
        con.close()


@pytest.mark.asyncio
async def test_no_agent_form_is_per_person(world):
    app, mem = world["app"], world["mem"]
    root = mem.parent / "user-qmd-index"
    legacy = _no_agent_row(mem, None, "ADMIN-H", "admin private note")
    bob_p = _no_agent_row(mem, world["bob"], "BOB-H", "bob private note")
    async with _session_client(app, world["alice"]) as alice:
        for r in (await alice.get("/api/memory/browse"),
                  await alice.post("/api/memory/search", json={"query": "private", "mode": "keyword"})):
            assert r.status_code == 200
            assert "bob private note" not in r.text and "admin private note" not in r.text
        # alice writes nothing here, but a delete aimed at another person's hash touches only alice's own index
        assert (await alice.delete("/api/memory/chunk/BOB-H")).status_code == 200
        assert (await alice.delete("/api/memory/chunk/ADMIN-H")).status_code == 200
    assert _has(bob_p, "BOB-H") and _has(legacy, "ADMIN-H")
    assert (root / world["alice"] / "index.sqlite").parent.is_dir()  # created for alice, after authorisation
    async with _session_client(app, world["bob"]) as bob:
        assert "bob private note" in (await bob.get("/api/memory/browse")).text
        assert (await bob.delete("/api/memory/chunk/BOB-H")).status_code == 200
    assert not _has(bob_p, "BOB-H")
    # the install owner and the host token keep the existing index
    async with _session_client(app, world["admin"]) as admin:
        r = await admin.get("/api/memory/browse")
        assert "admin private note" in r.text and "bob" not in r.text
    host = world["app"].state.auth.get_local_token()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                           headers={"Authorization": f"Bearer {host}"}) as h:
        assert "admin private note" in (await h.get("/api/memory/browse")).text


@pytest.mark.asyncio
async def test_no_agent_anonymous_creates_nothing(world):
    app, mem = world["app"], world["mem"]
    root = mem.parent / "user-qmd-index"
    before = set(root.iterdir()) if root.exists() else set()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                           event_hooks=csrf_event_hooks()) as anon:
        assert (await anon.get("/api/memory/browse")).status_code == 401
        assert (await anon.post("/api/memory/search", json={"query": "x"})).status_code == 401
        assert (await anon.delete("/api/memory/chunk/h")).status_code == 401
    after = set(root.iterdir()) if root.exists() else set()
    assert before == after


@pytest.mark.asyncio
async def test_person_index_is_never_addressable_as_an_agent(world):
    app, mem = world["app"], world["mem"]
    bob_id = world["bob"]
    _no_agent_row(mem, bob_id, "BOB-H", "PERSON-CANARY")
    _make_index(mem / "bob-agent" / "index.sqlite", [(BOB_HASH, "private", "AGENT-CANARY")])
    # an admin may address ANY agent name; none of these reaches the person's index
    async with _session_client(app, world["admin"]) as admin:
        for name in (bob_id, f"person:{bob_id}", "user-qmd-index", f"user-qmd-index-{bob_id}"):
            r = await admin.get("/api/memory/browse", params={"agent": name})
            assert "PERSON-CANARY" not in r.text, name
            r = await admin.post("/api/memory/search", json={"query": "CANARY", "agent": name})
            assert "PERSON-CANARY" not in r.text, name
        for bad in ("..", f"../user-qmd-index/{bob_id}", f"{bob_id}/.."):
            r = await admin.get("/api/memory/browse", params={"agent": bad})
            assert r.status_code == 400, bad
    # a member cannot address them either (not an owner of any of those names)
    async with _session_client(app, world["alice"]) as alice:
        for name in (bob_id, f"person:{bob_id}", "user-qmd-index", ".."):
            r = await alice.get("/api/memory/browse", params={"agent": name})
            assert r.status_code in (400, 403), name
            assert "PERSON-CANARY" not in r.text
        # and no-agent never lands on an agent's index
        r = await alice.get("/api/memory/browse")
        assert "AGENT-CANARY" not in r.text and "PERSON-CANARY" not in r.text
    # the person's index file is untouched and lives outside the agent-memory tree
    assert _has(mem.parent / "user-qmd-index" / bob_id / "index.sqlite", "BOB-H")
    assert not (mem / bob_id / "index.sqlite").samefile(mem.parent / "user-qmd-index" / bob_id / "index.sqlite") \
        if (mem / bob_id / "index.sqlite").exists() else True


@pytest.mark.asyncio
async def test_unsafe_user_id_is_refused(world):
    app = world["app"]
    from tinyagentos.routes import memory as memmod
    class R:  # minimal request stand-in for the resolver only
        class state:  # noqa: N801
            user_id = "../x"; is_admin = False; via = "session"; agent_name = None
        class app:  # noqa: N801
            class state:  # noqa: N801
                agent_memory_dir = world["mem"]
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as e:
        memmod._user_index_path(R)
    assert e.value.status_code == 403

import pytest

from tinyagentos.auth_context import CurrentUser, current_user


def _as(app, user_id, is_admin=False):
    app.dependency_overrides[current_user] = lambda: CurrentUser(user_id=user_id, is_admin=is_admin)


def _clear(app):
    app.dependency_overrides.pop(current_user, None)


@pytest.mark.asyncio
async def test_post_creates_and_stamps_user(app, client):
    _as(app, "u1")
    try:
        r = await client.post("/api/receipts", json={
            "agent_canonical_id": "taos-dev-20260629-090000",
            "tool_name": "file_write",
            "tool_args": {"path": "a.py"},
            "trace_id": "trace-1",
        })
        assert r.status_code == 200
        rid = r.json()["id"]
        assert rid.startswith("rct-")
        got = await client.get(f"/api/receipts/{rid}")
        assert got.status_code == 200
        body = got.json()
        assert body["created_by_user_id"] == "u1"  # stamped from the caller
        assert body["tool_args"] == {"path": "a.py"}
    finally:
        _clear(app)


@pytest.mark.asyncio
async def test_blank_agent_rejected(app, client):
    _as(app, "u1")
    try:
        r = await client.post("/api/receipts", json={"agent_canonical_id": "  "})
        assert r.status_code == 400
    finally:
        _clear(app)


@pytest.mark.asyncio
async def test_handle_is_not_caller_settable(app, client):
    _as(app, "u1")
    try:
        # A caller-supplied handle must be ignored (not spoofable vs the canonical id).
        rid = (await client.post(
            "/api/receipts", json={"agent_canonical_id": "a", "handle": "spoofed"}
        )).json()["id"]
        got = await client.get(f"/api/receipts/{rid}")
        assert got.json()["handle"] == ""
    finally:
        _clear(app)


@pytest.mark.asyncio
async def test_non_admin_cannot_read_another_users_receipt(app, client):
    _as(app, "u1")
    try:
        rid = (await client.post("/api/receipts", json={"agent_canonical_id": "a"})).json()["id"]
    finally:
        _clear(app)
    _as(app, "u2")
    try:
        r = await client.get(f"/api/receipts/{rid}")
        assert r.status_code == 404  # do not leak existence across users
    finally:
        _clear(app)


@pytest.mark.asyncio
async def test_admin_can_read_any_receipt(app, client):
    _as(app, "u1")
    try:
        rid = (await client.post("/api/receipts", json={"agent_canonical_id": "a"})).json()["id"]
    finally:
        _clear(app)
    _as(app, "root", is_admin=True)
    try:
        r = await client.get(f"/api/receipts/{rid}")
        assert r.status_code == 200
    finally:
        _clear(app)


@pytest.mark.asyncio
async def test_list_scopes_non_admin_to_own(app, client):
    _as(app, "u1")
    try:
        await client.post("/api/receipts", json={"agent_canonical_id": "a"})
    finally:
        _clear(app)
    _as(app, "u2")
    try:
        await client.post("/api/receipts", json={"agent_canonical_id": "b"})
        mine = await client.get("/api/receipts")
        assert mine.status_code == 200
        agents = [r["agent_canonical_id"] for r in mine.json()["receipts"]]
        assert agents == ["b"]  # u2 sees only their own
    finally:
        _clear(app)
    _as(app, "root", is_admin=True)
    try:
        allr = await client.get("/api/receipts")
        assert allr.json()["count"] >= 2  # admin sees both
    finally:
        _clear(app)


@pytest.mark.asyncio
async def test_list_filter_by_agent(app, client):
    _as(app, "u1")
    try:
        await client.post("/api/receipts", json={"agent_canonical_id": "agent-a"})
        await client.post("/api/receipts", json={"agent_canonical_id": "agent-b"})
        r = await client.get("/api/receipts", params={"agent": "agent-a"})
        rows = r.json()["receipts"]
        assert len(rows) == 1 and rows[0]["agent_canonical_id"] == "agent-a"
    finally:
        _clear(app)

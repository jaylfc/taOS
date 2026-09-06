import json
import secrets
import time

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from tinyagentos.app import create_app
from tinyagentos.auth import hash_password
from tinyagentos.store_submissions import StoreSubmissionStore
from taos_test_csrf import csrf_event_hooks


def _add_non_admin_user(auth_mgr, username, password):
    """Add a non-admin user directly to the auth user store."""
    data = auth_mgr._read_users()
    uid = secrets.token_urlsafe(8)
    record = {
        "id": uid,
        "username": username,
        "full_name": "Regular User",
        "email": "",
        "password_hash": hash_password(password),
        "created_at": int(time.time()),
        "is_admin": False,
    }
    data.setdefault("users", []).append(record)
    auth_mgr._write_users(data)
    return uid


@pytest_asyncio.fixture
async def store_submissions_store(tmp_path):
    store = StoreSubmissionStore(tmp_path / "store_submissions.db")
    await store.init()
    yield store
    await store.close()


@pytest.fixture
def app_with_store_submissions(tmp_data_dir):
    app = create_app(data_dir=tmp_data_dir)
    return app


@pytest_asyncio.fixture
async def client_with_store_submissions(app_with_store_submissions, tmp_data_dir):
    app = app_with_store_submissions
    store = StoreSubmissionStore(tmp_data_dir / "store_submissions.db")
    await store.init()
    app.state.store_submissions = store
    app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
    _record = app.state.auth.find_user("admin")
    _uid = _record["id"] if _record else ""
    _token = app.state.auth.create_session(user_id=_uid, long_lived=True)
    app.state._startup_complete = True
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies={"taos_session": _token},
        event_hooks=csrf_event_hooks(),
    ) as c:
        yield c
    await store.close()


@pytest_asyncio.fixture
async def client_non_admin(app_with_store_submissions, tmp_data_dir):
    app = app_with_store_submissions
    store = StoreSubmissionStore(tmp_data_dir / "store_submissions.db")
    await store.init()
    app.state.store_submissions = store
    app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
    uid = _add_non_admin_user(app.state.auth, "regular", "testpass123")
    _token = app.state.auth.create_session(user_id=uid, long_lived=True)
    app.state._startup_complete = True
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies={"taos_session": _token},
        event_hooks=csrf_event_hooks(),
    ) as c:
        yield c
    await store.close()


@pytest.mark.asyncio
async def test_create_submission(store_submissions_store):
    row = await store_submissions_store.create(
        artifact_id="art-001",
        artifact_kind="app",
        owner_id="user-1",
        title="My App",
        publish_mode="repo",
    )
    assert row["status"] == "draft"
    assert row["artifact_id"] == "art-001"
    assert row["artifact_kind"] == "app"
    assert row["owner_id"] == "user-1"
    assert row["title"] == "My App"
    assert row["publish_mode"] == "repo"
    assert row["gitaos_ref"] is None
    assert row["reject_reason"] is None
    assert row["id"].startswith("ss-")


@pytest.mark.asyncio
async def test_full_lifecycle_approve(store_submissions_store):
    row = await store_submissions_store.create(
        artifact_id="art-002",
        artifact_kind="game",
        owner_id="user-2",
        title="My Game",
        publish_mode="bundle",
    )
    sid = row["id"]
    assert row["status"] == "draft"

    row = await store_submissions_store.submit(sid)
    assert row["status"] == "pending_verification"

    row = await store_submissions_store.approve(sid)
    assert row["status"] == "published"


@pytest.mark.asyncio
async def test_full_lifecycle_reject(store_submissions_store):
    row = await store_submissions_store.create(
        artifact_id="art-003",
        artifact_kind="project",
        owner_id="user-3",
        title="My Project",
        publish_mode="repo",
    )
    sid = row["id"]

    row = await store_submissions_store.submit(sid)
    assert row["status"] == "pending_verification"

    row = await store_submissions_store.reject(sid, "Incomplete metadata")
    assert row["status"] == "rejected"
    assert row["reject_reason"] == "Incomplete metadata"


@pytest.mark.asyncio
async def test_illegal_transition_submit_from_published(store_submissions_store):
    row = await store_submissions_store.create(
        artifact_id="art-004",
        artifact_kind="app",
        owner_id="user-4",
        title="Test",
        publish_mode="repo",
    )
    sid = row["id"]
    await store_submissions_store.submit(sid)
    await store_submissions_store.approve(sid)

    with pytest.raises(ValueError, match="cannot submit from status"):
        await store_submissions_store.submit(sid)


@pytest.mark.asyncio
async def test_illegal_transition_approve_from_draft(store_submissions_store):
    row = await store_submissions_store.create(
        artifact_id="art-005",
        artifact_kind="app",
        owner_id="user-5",
        title="Test",
        publish_mode="repo",
    )
    sid = row["id"]

    with pytest.raises(ValueError, match="cannot approve from status"):
        await store_submissions_store.approve(sid)


@pytest.mark.asyncio
async def test_illegal_transition_reject_from_draft(store_submissions_store):
    row = await store_submissions_store.create(
        artifact_id="art-006",
        artifact_kind="app",
        owner_id="user-6",
        title="Test",
        publish_mode="repo",
    )
    sid = row["id"]

    with pytest.raises(ValueError, match="cannot reject from status"):
        await store_submissions_store.reject(sid, "bad")


@pytest.mark.asyncio
async def test_illegal_transition_approve_from_rejected(store_submissions_store):
    row = await store_submissions_store.create(
        artifact_id="art-007",
        artifact_kind="app",
        owner_id="user-7",
        title="Test",
        publish_mode="repo",
    )
    sid = row["id"]
    await store_submissions_store.submit(sid)
    await store_submissions_store.reject(sid, "bad")

    with pytest.raises(ValueError, match="cannot approve from status"):
        await store_submissions_store.approve(sid)


@pytest.mark.asyncio
async def test_list_and_get(store_submissions_store):
    await store_submissions_store.create(
        artifact_id="art-010", artifact_kind="app",
        owner_id="user-a", title="A", publish_mode="repo",
    )
    await store_submissions_store.create(
        artifact_id="art-011", artifact_kind="game",
        owner_id="user-a", title="B", publish_mode="bundle",
    )
    await store_submissions_store.create(
        artifact_id="art-012", artifact_kind="app",
        owner_id="user-b", title="C", publish_mode="repo",
    )

    all_rows = await store_submissions_store.list()
    assert len(all_rows) == 3

    user_a_rows = await store_submissions_store.list(owner_id="user-a")
    assert len(user_a_rows) == 2

    draft_rows = await store_submissions_store.list(status="draft")
    assert len(draft_rows) == 3

    row = await store_submissions_store.get(all_rows[0]["id"])
    assert row is not None
    assert row["id"] == all_rows[0]["id"]

    assert await store_submissions_store.get("ss-nonexistent") is None


@pytest.mark.asyncio
async def test_set_gitaos_ref(store_submissions_store):
    row = await store_submissions_store.create(
        artifact_id="art-013", artifact_kind="app",
        owner_id="user-c", title="Test", publish_mode="repo",
    )
    sid = row["id"]

    updated = await store_submissions_store.set_gitaos_ref(sid, "gitaos-ref-abc")
    assert updated["gitaos_ref"] == "gitaos-ref-abc"


@pytest.mark.asyncio
async def test_route_create_and_get(client_with_store_submissions):
    resp = await client_with_store_submissions.post(
        "/api/store/submissions",
        json={
            "artifact_id": "art-r1",
            "artifact_kind": "app",
            "title": "Route Test",
            "publish_mode": "repo",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "draft"
    sid = data["id"]

    resp = await client_with_store_submissions.get(
        f"/api/store/submissions/{sid}"
    )
    assert resp.status_code == 200
    assert resp.json()["id"] == sid


@pytest.mark.asyncio
async def test_route_full_approve_lifecycle(client_with_store_submissions):
    resp = await client_with_store_submissions.post(
        "/api/store/submissions",
        json={
            "artifact_id": "art-r2",
            "artifact_kind": "game",
            "title": "Lifecycle Test",
            "publish_mode": "bundle",
        },
    )
    assert resp.status_code == 200
    sid = resp.json()["id"]

    resp = await client_with_store_submissions.post(
        f"/api/store/submissions/{sid}/submit"
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "pending_verification"

    resp = await client_with_store_submissions.post(
        f"/api/store/submissions/{sid}/approve"
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "published"


@pytest.mark.asyncio
async def test_route_full_reject_lifecycle(client_with_store_submissions):
    resp = await client_with_store_submissions.post(
        "/api/store/submissions",
        json={
            "artifact_id": "art-r3",
            "artifact_kind": "project",
            "title": "Reject Test",
            "publish_mode": "repo",
        },
    )
    assert resp.status_code == 200
    sid = resp.json()["id"]

    resp = await client_with_store_submissions.post(
        f"/api/store/submissions/{sid}/submit"
    )
    assert resp.status_code == 200

    resp = await client_with_store_submissions.post(
        f"/api/store/submissions/{sid}/reject",
        json={"reason": "Missing assets"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "rejected"
    assert resp.json()["reject_reason"] == "Missing assets"


@pytest.mark.asyncio
async def test_route_admin_only_approve(client_non_admin):
    resp = await client_non_admin.post(
        "/api/store/submissions",
        json={
            "artifact_id": "art-r4",
            "artifact_kind": "app",
            "title": "Admin Test",
            "publish_mode": "repo",
        },
    )
    assert resp.status_code == 200
    sid = resp.json()["id"]

    resp = await client_non_admin.post(
        f"/api/store/submissions/{sid}/approve"
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_route_admin_only_reject(client_non_admin):
    resp = await client_non_admin.post(
        "/api/store/submissions",
        json={
            "artifact_id": "art-r5",
            "artifact_kind": "app",
            "title": "Admin Reject Test",
            "publish_mode": "repo",
        },
    )
    assert resp.status_code == 200
    sid = resp.json()["id"]

    resp = await client_non_admin.post(
        f"/api/store/submissions/{sid}/reject",
        json={"reason": "nope"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_route_admin_list(client_with_store_submissions):
    for i in range(3):
        await client_with_store_submissions.post(
            "/api/store/submissions",
            json={
                "artifact_id": f"art-list-{i}",
                "artifact_kind": "app",
                "title": f"List {i}",
                "publish_mode": "repo",
            },
        )

    resp = await client_with_store_submissions.get(
        "/api/store/submissions?status=draft"
    )
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) >= 3


@pytest.mark.asyncio
async def test_route_non_admin_cannot_list_all(client_non_admin):
    resp = await client_non_admin.get(
        "/api/store/submissions?status=draft"
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_route_list_mine(client_with_store_submissions):
    resp = await client_with_store_submissions.get(
        "/api/store/submissions/mine"
    )
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)


@pytest.mark.asyncio
async def test_route_illegal_transition_via_api(client_with_store_submissions):
    resp = await client_with_store_submissions.post(
        "/api/store/submissions",
        json={
            "artifact_id": "art-r6",
            "artifact_kind": "app",
            "title": "Illegal",
            "publish_mode": "repo",
        },
    )
    sid = resp.json()["id"]

    resp = await client_with_store_submissions.post(
        f"/api/store/submissions/{sid}/approve"
    )
    assert resp.status_code == 400

    resp = await client_with_store_submissions.post(
        f"/api/store/submissions/{sid}/reject",
        json={"reason": "nope"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_create_submission_invalid_kind_returns_400(client_with_store_submissions):
    c = client_with_store_submissions
    r = await c.post(
        "/api/store/submissions",
        json={"artifact_id": "x", "artifact_kind": "not_a_kind", "title": "t", "publish_mode": "repo"},
    )
    assert r.status_code == 400, r.text


@pytest.mark.asyncio
async def test_create_submission_invalid_mode_returns_400(client_with_store_submissions):
    c = client_with_store_submissions
    r = await c.post(
        "/api/store/submissions",
        json={"artifact_id": "x", "artifact_kind": "app", "title": "t", "publish_mode": "bogus"},
    )
    assert r.status_code == 400, r.text

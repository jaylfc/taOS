"""Generated videos belong to the member who made them (security sweep).

Images and music are stored per user; videos share one directory, so each
video's metadata sidecar records its owner. A member lists, plays and deletes
only their own videos; an admin can reach all of them. A video with no
recorded owner (made before this change) is admin only.
"""
import asyncio
import base64
import json
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient, Response
from httpx import Request as HttpxRequest
from taos_test_csrf import csrf_event_hooks

from tinyagentos.app import create_app


def _client(app, user_id: str) -> AsyncClient:
    token = app.state.auth.create_session(user_id=user_id, long_lived=True)
    return AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
        cookies={"taos_session": token}, event_hooks=csrf_event_hooks(),
    )


@pytest_asyncio.fixture
async def setup(tmp_data_dir):
    app = create_app(data_dir=tmp_data_dir)
    app.state.data_dir = str(tmp_data_dir)
    videos = tmp_data_dir / "videos"
    videos.mkdir(exist_ok=True)
    store = app.state.metrics
    if store._db is not None:
        await store.close()
    await store.init()
    await app.state.qmd_client.init()
    auth = app.state.auth
    auth.setup_user("admin", "Test Admin", "", "testpass")
    admin_id = auth.find_user("admin")["id"]
    invite = auth.add_user_invite("bob", "admin")
    auth.complete_invite("bob", invite, "Bob", "bob@test.com", "bobspass11")
    bob_id = auth.find_user("bob")["id"]
    invite = auth.add_user_invite("carol", "admin")
    auth.complete_invite("carol", invite, "Carol", "carol@test.com", "carolpass11")
    carol_id = auth.find_user("carol")["id"]
    admin, bob = _client(app, admin_id), _client(app, bob_id)
    try:
        yield {"app": app, "videos": videos, "admin": admin, "bob": bob,
               "admin_id": admin_id, "bob_id": bob_id, "carol_id": carol_id}
    finally:
        await admin.aclose()
        await bob.aclose()
        await store.close()
        await app.state.qmd_client.close()
        await app.state.http_client.aclose()


def _video(videos, name: str, owner: str | None) -> None:
    (videos / f"{name}.mp4").write_bytes(b"fake-mp4")
    meta = {"prompt": name}
    if owner is not None:
        meta["user_id"] = owner
    (videos / f"{name}.json").write_text(json.dumps(meta))


async def _names(client) -> list[str]:
    resp = await client.get("/api/video")
    assert resp.status_code == 200
    return sorted(v["filename"] for v in resp.json()["videos"])


@pytest.mark.asyncio
async def test_a_member_cannot_list_play_or_delete_another_members_video(setup):
    _video(setup["videos"], "1_admins", setup["admin_id"])
    bob = setup["bob"]

    assert await _names(bob) == []
    assert (await bob.get("/api/video/1_admins.mp4")).status_code == 403
    assert (await bob.delete("/api/video/1_admins.mp4")).status_code == 403
    assert (setup["videos"] / "1_admins.mp4").exists()


@pytest.mark.asyncio
async def test_a_member_reaches_their_own_video_and_the_admin_reaches_all(setup):
    _video(setup["videos"], "2_bobs", setup["bob_id"])
    _video(setup["videos"], "1_admins", setup["admin_id"])
    bob, admin = setup["bob"], setup["admin"]

    assert await _names(bob) == ["2_bobs.mp4"]
    assert (await bob.get("/api/video/2_bobs.mp4")).status_code == 200
    assert await _names(admin) == ["1_admins.mp4", "2_bobs.mp4"]
    assert (await admin.get("/api/video/2_bobs.mp4")).status_code == 200
    assert (await bob.delete("/api/video/2_bobs.mp4")).status_code == 200
    assert not (setup["videos"] / "2_bobs.mp4").exists()


@pytest.mark.asyncio
async def test_a_video_with_no_recorded_owner_is_admin_only(setup):
    _video(setup["videos"], "0_legacy", None)

    assert await _names(setup["bob"]) == []
    assert (await setup["bob"].get("/api/video/0_legacy.mp4")).status_code == 403
    assert (await setup["bob"].delete("/api/video/0_legacy.mp4")).status_code == 403
    assert await _names(setup["admin"]) == ["0_legacy.mp4"]
    assert (await setup["admin"].delete("/api/video/0_legacy.mp4")).status_code == 200


@pytest.mark.asyncio
async def test_generation_records_the_requesting_member_as_owner(setup):
    app = setup["app"]
    app.state.config.server["video_backend_url"] = "http://localhost:9000"
    response = Response(
        status_code=200,
        json={"data": [{"b64_json": base64.b64encode(b"mp4").decode()}]},
        request=HttpxRequest("POST", "http://localhost:9000/v1/videos/generations"),
    )
    with patch("tinyagentos.routes.video.httpx.AsyncClient") as mock_client:
        inst = AsyncMock()
        inst.post.return_value = response
        inst.__aenter__ = AsyncMock(return_value=inst)
        inst.__aexit__ = AsyncMock(return_value=False)
        mock_client.return_value = inst
        resp = await setup["bob"].post("/api/video/generate", json={"prompt": "mine", "seed": 7})
        assert resp.status_code == 202
        pending = [t for t in app.state._background_tasks if not t.done()]
        if pending:
            await asyncio.gather(*pending)

    meta = json.loads(next(setup["videos"].glob("*_7.json")).read_text())
    assert meta["user_id"] == setup["bob_id"]
    assert await _names(setup["bob"]) == [next(setup["videos"].glob("*_7.mp4")).name]


@pytest.mark.asyncio
async def test_a_member_cannot_reach_another_members_video(setup):
    _video(setup["videos"], "3_carols", setup["carol_id"])
    bob = setup["bob"]

    assert await _names(bob) == []
    assert (await bob.get("/api/video/3_carols.mp4")).status_code == 403
    assert (await bob.delete("/api/video/3_carols.mp4")).status_code == 403
    assert (setup["videos"] / "3_carols.mp4").exists()


@pytest.mark.asyncio
async def test_delete_refuses_a_sidecar_so_ownership_cannot_be_stripped(setup):
    _video(setup["videos"], "4_bobs", setup["bob_id"])

    resp = await setup["bob"].delete("/api/video/4_bobs.json")

    assert resp.status_code == 400
    assert (setup["videos"] / "4_bobs.json").exists()
    assert await _names(setup["bob"]) == ["4_bobs.mp4"]


@pytest.mark.asyncio
async def test_two_same_seed_jobs_in_one_second_keep_separate_files(setup):
    app = setup["app"]
    app.state.config.server["video_backend_url"] = "http://localhost:9000"
    response = Response(
        status_code=200,
        json={"data": [{"b64_json": base64.b64encode(b"mp4").decode()}]},
        request=HttpxRequest("POST", "http://localhost:9000/v1/videos/generations"),
    )
    with patch("tinyagentos.routes.video.httpx.AsyncClient") as mock_client, \
            patch("tinyagentos.routes.video.time.time", return_value=1_700_000_000):
        inst = AsyncMock()
        inst.post.return_value = response
        inst.__aenter__ = AsyncMock(return_value=inst)
        inst.__aexit__ = AsyncMock(return_value=False)
        mock_client.return_value = inst
        for client in (setup["bob"], setup["admin"]):
            resp = await client.post("/api/video/generate", json={"prompt": "same", "seed": 5})
            assert resp.status_code == 202
        pending = [t for t in app.state._background_tasks if not t.done()]
        if pending:
            await asyncio.gather(*pending)

    assert len(list(setup["videos"].glob("*_5.mp4"))) == 2
    assert len(await _names(setup["bob"])) == 1

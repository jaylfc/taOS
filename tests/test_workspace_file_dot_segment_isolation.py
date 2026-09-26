"""Per-user isolation of /data/workspace must survive dot segments (tsk-shj7wq).

``serve_workspace_file`` used to take the owner decision from the RAW path's
first segment while serving the RESOLVED path. Starlette percent-decodes the
path parameter, so ``/data/workspace/%2e/users/<victim>/...`` arrived as
``./users/<victim>/...``: the first segment was ``.``, the owner check was
skipped, and the resolved file (still inside the workspace) was served to any
signed-in member.

These tests drive the real app with two member sessions: alice generates an
image and a song through the real endpoints, bob (a non-admin member) tries to
read them through every dot-segment spelling. Literal ``.``/``..`` spellings
are sent as raw ASGI requests because httpx normalises them away client-side,
while a real server (uvicorn) passes them through verbatim.
"""
import base64
from unittest.mock import AsyncMock, patch
from urllib.parse import unquote

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient, Request as HttpxRequest, Response

from tinyagentos.app import create_app
from taos_test_csrf import csrf_event_hooks


def _backend(url: str, payload: bytes):
    resp = Response(
        status_code=200,
        json={"data": [{"b64_json": base64.b64encode(payload).decode()}]},
        request=HttpxRequest("POST", url),
    )
    inst = AsyncMock()
    inst.post.return_value = resp
    inst.__aenter__ = AsyncMock(return_value=inst)
    inst.__aexit__ = AsyncMock(return_value=False)
    return inst


async def _raw_get(app, raw_path: str, token: str):
    """Send a GET straight into the ASGI app with ``raw_path`` untouched.

    Mirrors what uvicorn hands the app: ``path`` is the percent-decoded
    raw path with NO dot-segment normalisation.
    """
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": unquote(raw_path),
        "raw_path": raw_path.encode(),
        "root_path": "",
        "query_string": b"",
        "headers": [
            (b"host", b"test"),
            (b"cookie", f"taos_session={token}".encode()),
        ],
        "client": ("127.0.0.1", 12345),
        "server": ("test", 80),
    }
    sent = {"status": None, "body": b""}
    received = False

    async def receive():
        nonlocal received
        if not received:
            received = True
            return {"type": "http.request", "body": b"", "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.start":
            sent["status"] = message["status"]
        elif message["type"] == "http.response.body":
            sent["body"] += message.get("body", b"")

    await app(scope, receive, send)
    return sent["status"], sent["body"]


@pytest_asyncio.fixture
async def world(tmp_data_dir):
    app = create_app(data_dir=tmp_data_dir)
    app.state.data_dir = str(tmp_data_dir)
    store = app.state.metrics
    if store._db is not None:
        await store.close()
    await store.init()
    await app.state.qmd_client.init()
    auth = app.state.auth
    auth.setup_user("admin", "Test Admin", "", "testpassword")
    for name in ("alice", "bob"):
        code = auth.add_user_invite(name, "admin")
        auth.complete_invite(name, code, f"Test {name.title()}", "", "testpassword")
    app.state._startup_complete = True

    ids = {n: auth.find_user(n)["id"] for n in ("admin", "alice", "bob")}
    tokens = {n: auth.create_session(user_id=ids[n], long_lived=True) for n in ids}
    clients = {
        n: AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            cookies={"taos_session": tokens[n]},
            event_hooks=csrf_event_hooks(),
        )
        for n in ("alice", "bob")
    }

    # Alice generates an image and a song through the real endpoints.
    with patch("tinyagentos.routes.images.httpx.AsyncClient") as MockImg:
        MockImg.return_value = _backend(
            "http://localhost:8080/v1/images/generations", b"alice-secret-image"
        )
        r = await clients["alice"].post(
            "/api/images/generate", json={"prompt": "secret", "seed": 12345}
        )
    assert r.status_code == 200, r.text
    image = r.json()

    app.state.config.server["music_backend_url"] = "http://localhost:9000"
    with (
        patch("tinyagentos.routes.music._http_backend_reachable", new=AsyncMock(return_value=True)),
        patch("tinyagentos.routes.music.httpx.AsyncClient") as MockMus,
    ):
        MockMus.return_value = _backend(
            "http://localhost:9000/v1/audio/generations", b"alice-secret-music"
        )
        r = await clients["alice"].post(
            "/api/music/compose", json={"prompt": "secret beat", "duration": 5}
        )
    assert r.status_code == 200, r.text
    music = r.json()

    yield {
        "app": app,
        "ids": ids,
        "tokens": tokens,
        "clients": clients,
        "image": image,
        "music": music,
        "workspace": tmp_data_dir / "workspace",
    }

    for c in clients.values():
        await c.aclose()
    await app.state.qmd_client.close()
    await app.state.http_client.aclose()
    await app.state.metrics.close()


def _rel(item, alice_id, kind):
    """``users/<alice>/<kind>/generated/<file>`` - the owner-scoped tail."""
    expected = f"/data/workspace/users/{alice_id}/{kind}/generated/{item['filename']}"
    assert item["path"] == expected
    return expected[len("/data/workspace/"):]


# Spellings that all resolve to alice's file inside the workspace.
# (form name, prefix-builder, sent-raw?) - raw forms bypass httpx normalisation.
_BYPASS_FORMS = [
    ("pct2e_leading", lambda rel, kind: f"%2e/{rel}", False),
    ("pct2e_upper", lambda rel, kind: f"%2E/{rel}", False),
    ("pct2e2e_after_legacy", lambda rel, kind: f"{kind}/%2e%2e/{rel}", False),
    ("pct2e2e_after_generated", lambda rel, kind: f"{kind}/generated/%2e%2e/%2e%2e/{rel}", False),
    ("literal_dot_leading", lambda rel, kind: f"./{rel}", True),
    ("literal_dotdot_after_legacy", lambda rel, kind: f"{kind}/../{rel}", True),
    ("pct2e_inside_users", lambda rel, kind: rel.replace("users/", "users/%2e/", 1), False),
]


_KINDS = [("images", b"alice-secret-image"), ("music", b"alice-secret-music")]


async def _fetch(world, who, url, raw):
    if raw:
        return await _raw_get(world["app"], url, world["tokens"][who])
    r = await world["clients"][who].get(url)
    return r.status_code, r.content


def _cases(world):
    for kind, payload in _KINDS:
        item = world["image"] if kind == "images" else world["music"]
        rel = _rel(item, world["ids"]["alice"], kind)
        for form, build, raw in _BYPASS_FORMS:
            yield kind, payload, form, "/data/workspace/" + build(rel, kind), raw


@pytest.mark.asyncio
async def test_other_member_cannot_read_via_dot_segment(world):
    """Every spelling x {images, music}; all failing forms are reported together."""
    leaks = []
    for kind, payload, form, url, raw in _cases(world):
        status, body = await _fetch(world, "bob", url, raw)
        if status not in (403, 404) or body == payload:
            leaks.append(f"{kind}/{form}: {url!r} -> {status}")
    assert not leaks, "CROSS-USER READ: bob fetched alice's media via:\n" + "\n".join(leaks)


@pytest.mark.asyncio
async def test_owner_still_reads_own_file_via_dot_segment(world):
    """The owner decision comes from the resolved path, so the owner is not locked out."""
    lost = []
    for kind, payload, form, url, raw in _cases(world):
        status, body = await _fetch(world, "alice", url, raw)
        if status != 200 or body != payload:
            lost.append(f"{kind}/{form}: {url!r} -> {status}")
    assert not lost, "owner lost access via:\n" + "\n".join(lost)


@pytest.mark.asyncio
async def test_owner_reads_canonical_path_and_other_member_is_refused(world):
    for kind, payload in _KINDS:
        item = world["image"] if kind == "images" else world["music"]
        r = await world["clients"]["alice"].get(item["path"])
        assert r.status_code == 200 and r.content == payload
        r = await world["clients"]["bob"].get(item["path"])
        assert r.status_code == 403


@pytest.mark.asyncio
async def test_double_encoded_dot_is_not_decoded_twice(world):
    """``%252e`` decodes once to a literal ``%2e`` directory, which does not exist."""
    for kind, payload in _KINDS:
        item = world["image"] if kind == "images" else world["music"]
        rel = _rel(item, world["ids"]["alice"], kind)
        for url in (
            f"/data/workspace/%252e/{rel}",
            f"/data/workspace/{kind}/%252e%252e/{rel}",
        ):
            r = await world["clients"]["bob"].get(url)
            assert r.status_code in (403, 404), f"{url!r} -> {r.status_code}"
            assert r.content != payload


@pytest.mark.asyncio
async def test_escape_outside_workspace_is_404(world):
    """Dot segments that climb OUT of the workspace stay a 404 (traversal guard)."""
    for url in (
        "/data/workspace/%2e%2e/%2e%2e/%2e%2e/%2e%2e/etc/passwd",
        "/data/workspace/users/%2e%2e/%2e%2e/config.yaml",
    ):
        r = await world["clients"]["bob"].get(url)
        assert r.status_code == 404, f"{url!r} -> {r.status_code}"
    status, _ = await _raw_get(
        world["app"], "/data/workspace/../../../../etc/passwd", world["tokens"]["bob"]
    )
    assert status == 404


@pytest.mark.asyncio
async def test_legacy_shared_path_stays_session_gated_only(world):
    """Legacy shared paths keep dev's behaviour: any signed-in member reads them."""
    for kind, _ in _KINDS:
        await _check_legacy(world, kind)


async def _check_legacy(world, kind):
    legacy = world["workspace"] / kind / "generated"
    legacy.mkdir(parents=True, exist_ok=True)
    (legacy / "legacy_file.bin").write_bytes(b"legacy-shared")
    for who in ("alice", "bob"):
        r = await world["clients"][who].get(f"/data/workspace/{kind}/generated/legacy_file.bin")
        assert r.status_code == 200 and r.content == b"legacy-shared"
        # Dot-segment spelling of a legacy path is still just the legacy path.
        r = await world["clients"][who].get(f"/data/workspace/%2e/{kind}/generated/legacy_file.bin")
        assert r.status_code == 200 and r.content == b"legacy-shared"
    anon = AsyncClient(transport=ASGITransport(app=world["app"]), base_url="http://test")
    try:
        r = await anon.get(f"/data/workspace/{kind}/generated/legacy_file.bin")
        assert r.status_code == 401
    finally:
        await anon.aclose()

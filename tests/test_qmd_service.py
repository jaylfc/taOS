"""qmd service safety net: capability probe, refusal on an old qmd, unit file, legacy owner."""
from __future__ import annotations

import json
import re
import sqlite3
import subprocess
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from taos_test_csrf import csrf_event_hooks
from tinyagentos import qmd_service

REPO = Path(__file__).resolve().parent.parent


def _state(handler):
    return SimpleNamespace(
        qmd_client=SimpleNamespace(base_url="http://qmd.test"),
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def _old_qmd(request: httpx.Request) -> httpx.Response:
    """qmd 2.6.0: no /delete-chunk or /ingest; every dbPath answers from ONE default index."""
    path = urlparse(str(request.url)).path
    if path in ("/delete-chunk", "/ingest"):
        return httpx.Response(404, json={"error": "Not found"})
    if path == "/browse":
        return httpx.Response(200, json={"chunks": [{"hash": "SHARED", "snippet": "DEFAULT-ONLY"}]})
    return httpx.Response(200, json={"results": []})


def _new_qmd(request: httpx.Request) -> httpx.Response:
    """qmd 2.8.3-taos.2: validates the body, honours dbPath."""
    path = urlparse(str(request.url)).path
    if path == "/delete-chunk":
        return httpx.Response(400, json={"error": "hash must be a hex content hash"})
    return httpx.Response(200, json={"chunks": [], "results": []})


# ----------------------------------------------------------- capability probe

@pytest.mark.asyncio
async def test_probe_old_new_and_unknown():
    assert await qmd_service.qmd_honours_dbpath(_state(_old_qmd)) is False
    assert await qmd_service.qmd_honours_dbpath(_state(_new_qmd)) is True

    def down(request):
        raise httpx.ConnectError("refused")
    assert await qmd_service.qmd_honours_dbpath(_state(down)) is None


@pytest.mark.asyncio
async def test_probe_is_cached_then_expires(monkeypatch):
    calls = []

    def handler(request):
        calls.append(1)
        return _new_qmd(request)
    st = _state(handler)
    await qmd_service.qmd_honours_dbpath(st)
    await qmd_service.qmd_honours_dbpath(st)
    assert len(calls) == 1
    st._qmd_dbpath_probe = (0.0, True)  # expired
    await qmd_service.qmd_honours_dbpath(st)
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_probe_sends_no_state_changing_request():
    seen = []

    def handler(request):
        seen.append((request.method, urlparse(str(request.url)).path, request.content))
        return _new_qmd(request)
    await qmd_service.qmd_honours_dbpath(_state(handler))
    assert seen == [("POST", "/delete-chunk", b"{}")]  # empty body: rejected, deletes nothing


# ------------------------------------------- routes refuse on a qmd that ignores dbPath

def _add_member(app, name):
    a = app.state.auth
    code = a.add_user_invite(name, invited_by_username="admin")
    a.complete_invite(username=name, invite_code=code, full_name=name,
                      email=f"{name}@t.local", password=f"{name}-pass-123")
    return a.find_user(name)["id"]


def _session(app, uid):
    tok = app.state.auth.create_session(user_id=uid, long_lived=True)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                       cookies={"taos_session": tok}, event_hooks=csrf_event_hooks())


@pytest_asyncio.fixture
async def old_qmd_world(client):
    app = client._transport.app
    admin = app.state.auth.find_user("admin")["id"]
    bob = _add_member(app, "bob")
    reg = app.state.agent_registry
    if reg._db is None:
        await reg.init()
    rec = await reg.register(framework="openclaw", display_name="bob-agent", handle="bob-agent", user_id=bob)
    app.state.config.agents.append({"name": "bob-agent", "registry_canonical_id": rec["canonical_id"]})
    app.state.qmd_client.base_url = "http://qmd.test"
    app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(_old_qmd))
    app.state._startup_complete = True
    yield {"app": app, "bob": bob, "admin": admin}
    await app.state.http_client.aclose()


@pytest.mark.asyncio
async def test_routes_refuse_on_old_qmd_and_create_nothing(old_qmd_world):
    app = old_qmd_world["app"]
    mem = app.state.agent_memory_dir
    root = mem.parent / "user-qmd-index"
    for who in ("bob", "admin"):
        async with _session(app, old_qmd_world[who]) as c:
            for agent in ("bob-agent", None):
                params = {"agent": agent} if agent else {}
                r = await c.get("/api/memory/browse", params=params)
                assert r.status_code == 503 and "SHARED" not in r.text, (who, agent, r.text)
                r = await c.post("/api/memory/search", json={"query": "x", "agent": agent})
                assert r.status_code == 503, (who, agent)
                r = await c.delete("/api/memory/chunk/abc", params=params)
                assert r.status_code == 503, (who, agent)
            assert (await c.get("/api/memory/collections/bob-agent")).status_code == 503
    assert not (mem / "bob-agent").exists()
    assert not root.exists() or not any(root.iterdir())


@pytest.mark.asyncio
async def test_routes_work_on_new_qmd(old_qmd_world):
    app = old_qmd_world["app"]
    app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(_new_qmd))
    app.state._qmd_dbpath_probe = (0.0, None)
    async with _session(app, old_qmd_world["bob"]) as c:
        assert (await c.get("/api/memory/browse", params={"agent": "bob-agent"})).status_code == 200
        assert (await c.get("/api/memory/browse")).status_code == 200


# ----------------------------------------------------- legacy single-password install

@pytest.mark.asyncio
async def test_legacy_session_is_the_install_owner(app):
    """No user records: the session has an empty user_id; it must keep using the existing index."""
    app.state.auth.set_password("legacy-password-1")  # the legacy single-password file
    tok = app.state.auth.create_session(user_id="", long_lived=True)
    app.state._startup_complete = True
    app.state.qmd_client.base_url = "http://qmd.test"
    app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(_new_qmd))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                           cookies={"taos_session": tok}, event_hooks=csrf_event_hooks()) as c:
        r = await c.get("/api/memory/browse")
        assert r.status_code == 200, r.text
        assert (await c.post("/api/memory/search", json={"query": "x"})).status_code == 200
    root = app.state.agent_memory_dir.parent / "user-qmd-index"
    assert (root / "index.sqlite").parent.is_dir()
    assert [p for p in root.iterdir() if p.is_dir()] == []  # no per-person directory
    await app.state.http_client.aclose()


@pytest.mark.asyncio
async def test_empty_user_id_is_not_owner_once_accounts_exist(client):
    from tinyagentos.auth_context import is_legacy_owner
    app = client._transport.app  # the fixture created an admin account
    req = SimpleNamespace(state=SimpleNamespace(via="session", user_id=""), app=SimpleNamespace(state=app.state))
    assert is_legacy_owner(req) is False


# ------------------------------------------------------------------- unit file

def test_unit_template_defaults():
    unit = (REPO / "scripts/systemd/qmd.service").read_text()
    assert re.search(r"^Environment=QMD_BIND=127\.0\.0\.1$", unit, re.M)
    assert "0.0.0.0" not in re.sub(r"#.*", "", unit)
    assert re.search(r"^Environment=QMD_SERVE_DB_ROOT=__TAOS_DATA_DIR__$", unit, re.M)
    assert "/etc/default/qmd" in unit  # documented override path


def test_installer_renders_data_dir_and_pins_isolating_qmd():
    sh = (REPO / "scripts/install-server.sh").read_text()
    assert "s|__TAOS_DATA_DIR__|${INSTALL_DIR}/data|g" in sh
    pins = re.findall(r'qmd_npm_version="\$\{TAOS_QMD_NPM_VERSION:-([^}]+)\}"', sh)
    assert pins and all(p == qmd_service.PINNED_QMD_VERSION for p in pins), pins


def test_upgrade_script_is_valid_and_matches_pin():
    script = REPO / "scripts/upgrade-qmd.sh"
    assert subprocess.run(["bash", "-n", str(script)]).returncode == 0
    assert qmd_service.PINNED_QMD_VERSION in script.read_text()
    assert subprocess.run(["bash", str(script)], capture_output=True, text=True).returncode != 0  # needs root


def test_bind_exposure_detection(tmp_path):
    unit, defaults = tmp_path / "qmd.service", tmp_path / "qmd"
    unit.write_text("[Service]\nEnvironment=QMD_BIND=0.0.0.0\n")
    assert qmd_service.qmd_bind_exposed(unit, defaults) is True
    defaults.write_text("QMD_BIND=127.0.0.1\n")  # EnvironmentFile overrides Environment=
    assert qmd_service.qmd_bind_exposed(unit, defaults) is False
    unit.write_text("[Service]\nEnvironment=QMD_BIND=127.0.0.1\n")
    defaults.write_text("QMD_BIND=0.0.0.0\n")
    assert qmd_service.qmd_bind_exposed(unit, defaults) is True
    assert qmd_service.qmd_bind_exposed(tmp_path / "missing", defaults) is False


# ------------------------------------------- probe fails closed on anything unexpected

def _answer(status=200, body=b"{}", headers=None):
    def handler(request):
        return httpx.Response(status, content=body, headers=headers or {})
    return handler


@pytest.mark.asyncio
@pytest.mark.parametrize("label,handler", [
    ("200 ok", _answer(200, b'{"status":"ok"}')),
    ("404 old qmd", _answer(404, b'{"error":"Not found"}')),
    ("html proxy page", _answer(400, b"<html><body>Bad request</body></html>", {"content-type": "text/html"})),
    ("html 200", _answer(200, b"<html>login</html>", {"content-type": "text/html"})),
    ("truncated json", _answer(400, b'{"error": "hash must')),
    ("json list", _answer(400, b'["x"]')),
    ("json without error", _answer(400, b'{"detail": "x"}')),
    ("empty error", _answer(400, b'{"error": ""}')),
    ("non-string error", _answer(400, b'{"error": 5}')),
    ("redirect", _answer(302, b"", {"location": "http://elsewhere/"})),
    ("500", _answer(500, b'{"error":"boom"}')),
    ("empty 400", _answer(400, b"")),
])
async def test_probe_refuses_anything_unrecognised(label, handler):
    assert await qmd_service.qmd_honours_dbpath(_state(handler)) is False, label


@pytest.mark.asyncio
async def test_probe_accepts_only_the_measured_new_answer():
    ok = _answer(400, b'{"error":"hash must be a hex content hash"}', {"content-type": "application/json"})
    assert await qmd_service.qmd_honours_dbpath(_state(ok)) is True
    older_multitenant = _answer(400, b'{"error":"hash is required"}')  # 2.1.1 wording
    assert await qmd_service.qmd_honours_dbpath(_state(older_multitenant)) is True


@pytest.mark.asyncio
async def test_probe_non_transport_exception_is_a_refusal_not_unknown():
    def boom(request):
        raise RuntimeError("unexpected")
    assert await qmd_service.qmd_honours_dbpath(_state(boom)) is False

    def timeout(request):
        raise httpx.ConnectTimeout("slow")
    assert await qmd_service.qmd_honours_dbpath(_state(timeout)) is None


@pytest.mark.asyncio
async def test_routes_503_on_unrecognised_answer_but_502_when_unreachable(old_qmd_world):
    app = old_qmd_world["app"]
    for handler, expected in ((_answer(200, b"<html>proxy</html>"), 503),
                              (_answer(400, b"{trunc"), 503),
                              (lambda r: (_ for _ in ()).throw(httpx.ConnectError("refused")), 502)):
        app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        app.state._qmd_dbpath_probe = (0.0, None)
        async with _session(app, old_qmd_world["bob"]) as c:
            r = await c.get("/api/memory/browse", params={"agent": "bob-agent"})
        assert r.status_code == expected, (r.status_code, r.text)


# --------------------------------------------- legacy owner: the refusing direction

def _legacy_app(app, *, password=True, users_file=None):
    if password:
        app.state.auth.set_password("legacy-password-1")
    if users_file is not None:
        app.state.auth._user_file.write_text(users_file)
    return app


def _legacy_request(app, via="session", user_id=""):
    return SimpleNamespace(state=SimpleNamespace(via=via, user_id=user_id), app=SimpleNamespace(state=app.state))


def test_legacy_owner_positive_only_for_empty_store_plus_password(app):
    from tinyagentos.auth_context import is_legacy_owner
    assert is_legacy_owner(_legacy_request(app)) is False  # no password file, nothing configured
    _legacy_app(app)
    assert is_legacy_owner(_legacy_request(app)) is True
    assert is_legacy_owner(_legacy_request(app, user_id="someone")) is False  # a real account id
    assert is_legacy_owner(_legacy_request(app, via="local_token")) is False


@pytest.mark.parametrize("store", [
    "{not json", "[]", "{}", '{"users": null}', '{"users": "x"}', "",
])
def test_legacy_owner_refused_when_account_store_is_unreadable_or_partial(app, store):
    from tinyagentos.auth_context import is_legacy_owner
    _legacy_app(app, users_file=store)
    assert is_legacy_owner(_legacy_request(app)) is False


def test_legacy_owner_refused_when_read_raises(app, monkeypatch):
    from tinyagentos.auth_context import is_legacy_owner
    _legacy_app(app)
    monkeypatch.setattr(app.state.auth, "_read_users", lambda: (_ for _ in ()).throw(OSError("disk")))
    assert is_legacy_owner(_legacy_request(app)) is False


@pytest.mark.asyncio
async def test_empty_user_id_session_is_refused_once_accounts_exist(client):
    """Through the real middleware: a stale empty-id session on an install with accounts is nobody."""
    app = client._transport.app
    app.state.auth.set_password("legacy-password-1")  # leftover legacy password file
    tok = app.state.auth.create_session(user_id="", long_lived=True)
    app.state._startup_complete = True
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                           cookies={"taos_session": tok}, event_hooks=csrf_event_hooks()) as c:
        assert (await c.get("/api/channels/agent/test-agent")).status_code in (401, 403)
        assert (await c.get("/api/memory/browse")).status_code in (401, 403)
        assert (await c.get("/api/memory/browse", params={"agent": "test-agent"})).status_code in (401, 403)
        assert (await c.post("/api/agents/test-agent/framework/update", json={})).status_code in (401, 403)


# ------------------------------- knowledge search must not expose agent memory to members

@pytest.mark.asyncio
async def test_knowledge_semantic_search_never_returns_default_index_memory_to_a_member(old_qmd_world):
    """Knowledge search queries qmd's DEFAULT index, where an old qmd put every agent's memory."""
    app = old_qmd_world["app"]
    ks = app.state.knowledge_store
    if ks._db is None:
        await ks.init()

    async def add(title, uid):
        return await ks.add_item(source_type="article", source_url=f"https://x/{title}", title=title,
                                 author="a", content=f"c {title}", summary="", categories=[], tags=[],
                                 metadata={}, user_id=uid)
    mine = await add("mine", old_qmd_world["bob"])
    admins = await add("admins", old_qmd_world["admin"])

    def qmd(request):
        if urlparse(str(request.url)).path == "/vsearch":
            return httpx.Response(200, json={"results": [
                {"body": "AGENT-MEMORY-SECRET", "hash": "h1"},      # agent memory: no knowledge id
                {"id": admins, "body": "ADMIN-ITEM"},               # somebody else's knowledge item
                {"id": mine, "body": "MY-ITEM"},
            ]})
        return httpx.Response(404)
    app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(qmd))
    async with _session(app, old_qmd_world["bob"]) as c:
        r = await c.get("/api/knowledge/search", params={"q": "x", "mode": "semantic"})
    assert r.status_code == 200
    assert "AGENT-MEMORY-SECRET" not in r.text and "ADMIN-ITEM" not in r.text
    assert "MY-ITEM" in r.text

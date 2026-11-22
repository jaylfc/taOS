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
    assert app.state.auth.get_primary_user() is None
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

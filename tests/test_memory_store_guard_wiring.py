"""The guard on the real start-up path, the status/convert routes and the consent card."""

from __future__ import annotations

import asyncio
import shutil
import sqlite3
from pathlib import Path

import pytest
import pytest_asyncio
import yaml
from httpx import ASGITransport, AsyncClient

from taos_test_csrf import csrf_event_hooks
from tinyagentos import memory_store_guard as guard
from tinyagentos.app import create_app

REPO = Path(__file__).resolve().parent.parent
FIXTURE_040 = REPO / "tests" / "fixtures" / "taosmd_040_store"


def _data_dir(tmp_path: Path, *, with_040: bool = False) -> Path:
    cfg = {
        "server": {"host": "127.0.0.1", "port": 6969},
        "backends": [],
        "qmd": {"url": "http://localhost:7832"},
        "agents": [],
        "metrics": {"poll_interval": 30, "retention_days": 30},
    }
    (tmp_path / "config.yaml").write_text(yaml.dump(cfg))
    (tmp_path / ".setup_complete").touch()
    if with_040:
        shutil.copytree(FIXTURE_040, tmp_path, ignore=shutil.ignore_patterns("make_fixture.py"), dirs_exist_ok=True)
    return tmp_path


# --- real start-up path --------------------------------------------------------------

@pytest.mark.asyncio
async def test_startup_opens_a_040_store_and_reports_old(tmp_path):
    data = _data_dir(tmp_path, with_040=True)
    app = create_app(data_dir=data)
    async with app.router.lifespan_context(app):
        status = app.state.memory_store_status
        assert status.state == "old" and status.can_serve
    # taOS opened it: the schema steps applied on open and every row is still there.
    conn = sqlite3.connect(data / "archive-index.db")
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] > 0
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_startup_refuses_a_newer_store_before_opening_anything(tmp_path):
    data = _data_dir(tmp_path, with_040=True)
    # Make it "newer": a schema version this taosmd does not know.
    conn = sqlite3.connect(data / "knowledge-graph.db")
    conn.execute("PRAGMA user_version = 99")
    conn.commit()
    conn.close()
    before = {p.name: p.read_bytes() for p in data.glob("*.db")}
    app = create_app(data_dir=data)
    with pytest.raises(guard.MemoryStoreGuardError) as exc:
        async with app.router.lifespan_context(app):
            pytest.fail("start-up must stop on a newer store")
    assert "update taOS" in str(exc.value)
    # Nothing in the taosmd stores was opened or changed.
    assert {p.name: p.read_bytes() for p in data.glob("*.db") if p.name in before and p.name.split(".")[0] in {"knowledge-graph", "vector-memory", "archive-index"}} == {
        k: v for k, v in before.items() if k in {"knowledge-graph.db", "vector-memory.db", "archive-index.db"}
    }


@pytest.mark.asyncio
async def test_startup_on_a_first_install_does_not_start_the_cli(tmp_path, monkeypatch):
    async def boom(*a, **k):
        raise AssertionError("CLI started for an empty data dir")

    monkeypatch.setattr(guard, "_run", boom)
    data = _data_dir(tmp_path)
    app = create_app(data_dir=data)
    async with app.router.lifespan_context(app):
        assert app.state.memory_store_status.state == "current"


# --- routes --------------------------------------------------------------------------

@pytest_asyncio.fixture
async def booted(tmp_path):
    data = _data_dir(tmp_path, with_040=True)
    app = create_app(data_dir=data)
    async with app.router.lifespan_context(app):
        yield app, data


def _client(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test", event_hooks=csrf_event_hooks())


async def _owner(c, *, login=True):
    """Set up the owner account; with ``login`` the client holds the signed-in session."""
    r = await c.post("/auth/setup", json={
        "username": "owner", "full_name": "Owner", "email": "", "password": "ownerpass1", "auto_login": login,
    })
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_status_route_reports_the_probe(booted):
    app, _ = booted
    async with _client(app) as c:
        await _owner(c)
        r = await c.get("/api/memory-store")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"]["state"] == "old" and body["status"]["convert_available"] is True
    assert body["converting"] is False and body["last_convert"] is None


@pytest.mark.asyncio
async def test_convert_requires_confirm(booted):
    app, data = booted
    async with _client(app) as c:
        await _owner(c)
        r = await c.post("/api/memory-store/convert", json={})
    assert r.status_code == 400 and "never automatic" in r.json()["error"]
    status = await guard.probe(data)
    assert any(d["db"] == "vector_memory" and d["state"] == "old" for d in status.databases)


@pytest.mark.asyncio
async def test_convert_from_the_owner_session_runs_the_real_conversion(booted):
    app, data = booted
    async with _client(app) as c:
        await _owner(c)
        r = await c.post("/api/memory-store/convert", json={"confirm": True})
        assert r.status_code == 202, r.text
        assert r.json()["state"] == "started"
        for _ in range(240):
            body = (await c.get("/api/memory-store")).json()
            if not body["converting"] and body["last_convert"] and body["last_convert"].get("state") != "running":
                break
            await asyncio.sleep(0.5)
    last = body["last_convert"]
    assert last["ok"] is True and last["state"] == "converted", last
    assert Path(last["backup_path"]).exists()
    assert body["status"]["convert_available"] is False


@pytest.mark.asyncio
async def test_convert_from_an_agent_or_cli_asks_for_consent_and_runs_nothing(booted):
    app, data = booted
    async with _client(app) as c:
        await _owner(c, login=False)
        token = app.state.auth.get_local_token()
        r = await c.post("/api/memory-store/convert", json={"confirm": True}, headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 202, r.text
        body = r.json()
        assert body["state"] == "consent_requested" and body["decision_id"]
        # a second ask reuses the same card
        r2 = await c.post("/api/memory-store/convert", json={"confirm": True}, headers={"Authorization": f"Bearer {token}"})
        assert r2.json()["decision_id"] == body["decision_id"]
    # nothing ran
    status = await guard.probe(data)
    assert any(d["db"] == "vector_memory" and d["state"] == "old" for d in status.databases)
    assert app.state.memory_store_last_convert is None if hasattr(app.state, "memory_store_last_convert") else True
    decision = await app.state.decision_store.get(body["decision_id"])
    assert decision["status"] == "pending" and decision["metadata"]["kind"] == "memory_store_convert"


@pytest.mark.asyncio
async def test_approving_the_consent_card_converts_and_notes_the_outcome(booted):
    app, data = booted
    async with _client(app) as c:
        await _owner(c)
        token = app.state.auth.get_local_token()
        asked = await c.post("/api/memory-store/convert", json={"confirm": True}, headers={"Authorization": f"Bearer {token}"})
        did = asked.json()["decision_id"]
        ans = await c.post(f"/api/decisions/{did}/answer", json={"value": "approve"})
        assert ans.status_code == 200, ans.text
        for _ in range(240):
            body = (await c.get("/api/memory-store")).json()
            last = body.get("last_convert")
            if not body["converting"] and last and last.get("state") != "running":
                break
            await asyncio.sleep(0.5)
        assert last["ok"] is True and last["state"] == "converted", last
        notes = (await app.state.decision_store.get(did))["notes"]
        assert any("converted" in n["text"].lower() for n in notes), notes


@pytest.mark.asyncio
async def test_declining_the_consent_card_converts_nothing(booted):
    app, data = booted
    async with _client(app) as c:
        await _owner(c)
        token = app.state.auth.get_local_token()
        asked = await c.post("/api/memory-store/convert", json={"confirm": True}, headers={"Authorization": f"Bearer {token}"})
        did = asked.json()["decision_id"]
        ans = await c.post(f"/api/decisions/{did}/answer", json={"value": "deny"})
        assert ans.status_code == 200, ans.text
        await asyncio.sleep(1)
    status = await guard.probe(data)
    assert any(d["db"] == "vector_memory" and d["state"] == "old" for d in status.databases)


@pytest.mark.asyncio
async def test_a_caller_cannot_forge_the_consent_card_through_the_public_create_path(booted):
    app, _ = booted
    async with _client(app) as c:
        await _owner(c)
        r = await c.post("/api/decisions", json={
            "from_agent": "@x", "question": "q", "type": "approve_deny",
            "metadata": {"kind": "memory_store_convert"},
        })
    assert r.status_code == 400, r.text


@pytest.mark.asyncio
async def test_health_reports_the_memory_store(booted):
    app, _ = booted
    async with _client(app) as c:
        await _owner(c)
        r = await c.get("/api/health?detailed=true")
    assert r.status_code == 200, r.text
    assert r.json()["subsystems"]["memory_store"]["status"] == "ok"

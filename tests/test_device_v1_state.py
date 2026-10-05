"""S2b: GET /api/device/v1/state (device bearer, scope agents:read).

RED-FIRST: this module is written BEFORE the route exists so the first run
must FAIL with 404. The FAIL block is captured in RED-PROOF.md.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

PLAIN = "http://testserver:6969"


def _bearer(tok):
    return {"Authorization": f"Bearer {tok}"}


def _client(app, base=PLAIN):
    return AsyncClient(transport=ASGITransport(app=app), base_url=base)


@pytest_asyncio.fixture
async def vapp(app, client):
    return app


async def _device(app, user_id="u1", platform="ios", scopes=("agents:read",)):
    st = app.state.device_store
    d = await st.register(user_id=user_id, platform=platform)
    if scopes is not None:
        await st.set_scopes(d["device_id"], list(scopes))
    return d["scoped_token"]


# (a) two owners, each with agents; a device paired to owner 1 sees only owner 1's agents.
@pytest.mark.asyncio
async def test_state_returns_only_owner_agents(vapp):
    app = vapp
    app.state.config.agents = [
        {"name": "alice-agent", "framework": "openclaw", "user_id": "owner-1"},
        {"name": "bob-agent", "framework": "hermes", "user_id": "owner-2"},
    ]

    tok1 = await _device(app, user_id="owner-1", scopes=("agents:read",))
    tok2 = await _device(app, user_id="owner-2", scopes=("agents:read",))

    async with _client(app) as c:
        r1 = await c.get("/api/device/v1/state", headers=_bearer(tok1))
        r2 = await c.get("/api/device/v1/state", headers=_bearer(tok2))

    assert r1.status_code == 200, r1.text
    assert r2.status_code == 200, r2.text

    names1 = {a["name"] for a in r1.json()["agents"]}
    names2 = {a["name"] for a in r2.json()["agents"]}

    assert names1 == {"alice-agent"}
    assert names2 == {"bob-agent"}


# (b) a 500-char recap arrives with length <= 180 and ends with the ellipsis.
@pytest.mark.asyncio
async def test_state_caps_long_strings(vapp):
    app = vapp
    long_status = "x" * 500
    long_recap = "r" * 500
    app.state.config.agents = [
        {"name": "cap-agent", "framework": "openclaw", "user_id": "u1", "status": long_status},
    ]

    await app.state.agent_messages.send(
        from_agent="cap-agent", to_agent="cap-agent", message=long_recap
    )

    tok = await _device(app, user_id="u1", scopes=("agents:read",))

    async with _client(app) as c:
        r = await c.get("/api/device/v1/state", headers=_bearer(tok))

    assert r.status_code == 200, r.text
    data = r.json()
    agent = next(a for a in data["agents"] if a["name"] == "cap-agent")
    assert len(agent["status"]) <= 120
    assert len(agent["last_recap"]) <= 180
    assert agent["status"].endswith("\u2026")
    assert agent["last_recap"].endswith("\u2026")


# (c) device token without agents:read -> 403, detail error device_scope_missing.
@pytest.mark.asyncio
async def test_state_requires_agents_read(vapp):
    app = vapp
    tok = await _device(app, user_id="u1", scopes=("chat:send",))

    async with _client(app) as c:
        r = await c.get("/api/device/v1/state", headers=_bearer(tok))

    assert r.status_code == 403, r.text
    assert r.json()["detail"] == {"error": "device_scope_missing", "scope": "agents:read"}


# (g) demo flag + unanswerable decision + lock-widgets name parity.
@pytest.mark.asyncio
async def test_state_demo_flag_and_unanswerable_decision(vapp, monkeypatch):
    from tinyagentos.demo_mode import write_demo_mode

    app = vapp
    monkeypatch.setenv("TAOS_LOCK_DEMO_AGENTS", "DemoA:openclaw:Drafting")
    monkeypatch.setenv("TAOS_LOCK_DEMO_DECISION", "Ship it?")
    monkeypatch.setenv("TAOS_LOCK_DEMO_DECISION_AGENT", "DemoA")
    write_demo_mode(app.state.data_dir, True)

    app.state.config.agents = [
        {"name": "real-agent", "framework": "openclaw", "user_id": "u1"},
    ]

    tok = await _device(app, user_id="u1", scopes=("agents:read",))

    async with _client(app) as c:
        r_state = await c.get("/api/device/v1/state", headers=_bearer(tok))
        r_widgets = await c.get("/auth/lock-widgets")

    assert r_state.status_code == 200, r_state.text
    assert r_widgets.status_code == 200, r_widgets.text

    state_data = r_state.json()
    widgets_data = r_widgets.json()

    assert state_data["demo"] is True
    state_names = {a["name"] for a in state_data["agents"]}
    widgets_names = {a["name"] for a in widgets_data["agents"] if not a.get("system")}
    assert state_names == widgets_names

    for a in state_data["agents"]:
        dec = a.get("decision")
        if dec and dec.get("id") == "":
            assert dec["id"] == ""
            break

    write_demo_mode(app.state.data_dir, False)

    async with _client(app) as c:
        r_off = await c.get("/api/device/v1/state", headers=_bearer(tok))

    assert r_off.json()["demo"] is False


# (h) Settings demo switch takes state down.
@pytest.mark.asyncio
async def test_settings_demo_switch_takes_state_down(vapp, monkeypatch):
    from tinyagentos.demo_mode import write_demo_mode

    app = vapp
    monkeypatch.setenv("TAOS_LOCK_DEMO_AGENTS", "DemoB:openclaw:Drafting")
    monkeypatch.setenv("TAOS_LOCK_DEMO_DECISION", "Ship it?")
    monkeypatch.setenv("TAOS_LOCK_DEMO_DECISION_AGENT", "DemoB")
    write_demo_mode(app.state.data_dir, True)

    tok = await _device(app, user_id="u1", scopes=("agents:read",))

    async with _client(app) as c:
        r_on = await c.get("/api/device/v1/state", headers=_bearer(tok))
        w_on = await c.get("/auth/lock-widgets")

    assert r_on.json()["demo"] is True
    state_names_on = {a["name"] for a in r_on.json()["agents"]}
    widgets_names_on = {a["name"] for a in w_on.json()["agents"] if not a.get("system")}
    assert state_names_on == widgets_names_on

    write_demo_mode(app.state.data_dir, False)

    async with _client(app) as c:
        r_off = await c.get("/api/device/v1/state", headers=_bearer(tok))
        w_off = await c.get("/auth/lock-widgets")

    assert r_off.json()["demo"] is False
    assert not any(a.get("demo") for a in w_off.json()["agents"])


# (j) avatar hash: null when no image, 16-hex when present, changes on rewrite.
@pytest.mark.asyncio
async def test_state_avatar_hash(vapp, monkeypatch):
    from tinyagentos import agent_avatars as avatars

    app = vapp
    app.state.config.agents = [
        {"name": "avatar-agent", "framework": "openclaw", "user_id": "u1"},
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr(avatars, "LOCK_AVATAR_DIR", tmpdir)
        tok = await _device(app, user_id="u1", scopes=("agents:read",))

        async with _client(app) as c:
            r = await c.get("/api/device/v1/state", headers=_bearer(tok))

        assert r.status_code == 200, r.text
        agent = r.json()["agents"][0]
        assert agent["avatar"]["hash"] is None

        slug = avatars._avatar_slug("avatar-agent")
        img_path = Path(tmpdir) / f"{slug}.jpg"
        img_path.write_bytes(b"first-image-content")
        async with _client(app) as c:
            r = await c.get("/api/device/v1/state", headers=_bearer(tok))

        assert r.status_code == 200, r.text
        agent = r.json()["agents"][0]
        h1 = agent["avatar"]["hash"]
        assert h1 is not None
        assert len(h1) == 16
        assert all(c in "0123456789abcdef" for c in h1)

        img_path.write_bytes(b"second-image-content-changed")
        async with _client(app) as c:
            r = await c.get("/api/device/v1/state", headers=_bearer(tok))

        assert r.status_code == 200, r.text
        agent = r.json()["agents"][0]
        h2 = agent["avatar"]["hash"]
        assert h2 is not None
        assert h2 != h1

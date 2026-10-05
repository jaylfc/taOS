"""P0 S3: GET /api/device/v1/events SSE.

RED-FIRST: this module is written BEFORE the route exists so the first run
must FAIL with 404. The FAIL block is captured in RED-PROOF.md.
"""
from __future__ import annotations

import asyncio
import json
import time
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


async def _collect_chunks(r, max_chunks=20, timeout=3.0):
    """Read at most *max_chunks* text chunks from an SSE response, or stop
    after *timeout* seconds of inactivity."""
    chunks = []
    start = time.monotonic()
    iterator = r.aiter_text()
    while len(chunks) < max_chunks:
        remaining = timeout - (time.monotonic() - start)
        if remaining <= 0:
            break
        try:
            task = asyncio.ensure_future(iterator.__anext__())
            done, pending = await asyncio.wait([task], timeout=min(remaining, 0.5))
            if pending:
                for p in pending:
                    p.cancel()
                try:
                    await asyncio.gather(*pending, return_exceptions=True)
                except Exception:
                    pass
                break
            chunk = done.pop().result()
            chunks.append(chunk)
        except (StopAsyncIteration, Exception):
            break
    return chunks


def _parse_events(chunks):
    """Return a list of (event_type, data_dict) from raw text chunks."""
    events = []
    for chunk in chunks:
        current_type = None
        current_data = {}
        for line in chunk.splitlines():
            line = line.strip()
            if not line:
                if current_type is not None:
                    events.append((current_type, current_data))
                    current_type = None
                    current_data = {}
                continue
            if line.startswith("event:"):
                current_type = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                try:
                    current_data = json.loads(line.split(":", 1)[1].strip())
                except Exception:
                    pass
            elif line.startswith("id:"):
                pass
            elif line.startswith(":"):
                pass
        if current_type is not None:
            events.append((current_type, current_data))
    return events


def _patch_intervals(monkeypatch):
    from tinyagentos.routes import device_state as ds_mod
    monkeypatch.setattr(ds_mod, "_POLL_INTERVAL_S", 0.1)
    monkeypatch.setattr(ds_mod, "_HEARTBEAT_INTERVAL_S", 0.2)


# (d) SSE route exists and emits agent.upsert events keyed by name.
@pytest.mark.asyncio
async def test_events_emits_upsert_keyed_by_name(vapp, monkeypatch):
    from tinyagentos.routes import auth as auth_mod
    from tinyagentos.demo_mode import write_demo_mode

    _patch_intervals(monkeypatch)

    app = vapp
    monkeypatch.setattr(auth_mod, "_request_is_console", lambda _r: True)

    write_demo_mode(app.state.data_dir, False)
    app.state.config.agents = [
        {"name": "alice", "framework": "openclaw", "user_id": "u1", "status": "running"},
    ]

    tok = await _device(app, user_id="u1", scopes=("agents:read",))

    async with _client(app) as c:
        async with c.stream("GET", "/api/device/v1/events", headers=_bearer(tok)) as r:
            assert r.status_code == 200
            chunks = await _collect_chunks(r, max_chunks=20, timeout=3.0)

    events = _parse_events(chunks)
    upsert_names = {data["name"] for evt, data in events if evt == "agent.upsert" and "name" in data}
    assert "alice" in upsert_names


# (e) Last-Event-ID resumes from the next event.
@pytest.mark.asyncio
async def test_events_last_event_id_resume(vapp, monkeypatch):
    from tinyagentos.routes import auth as auth_mod
    from tinyagentos.demo_mode import write_demo_mode

    _patch_intervals(monkeypatch)

    app = vapp
    monkeypatch.setattr(auth_mod, "_request_is_console", lambda _r: True)

    write_demo_mode(app.state.data_dir, False)
    app.state.config.agents = [
        {"name": "bob", "framework": "openclaw", "user_id": "u1", "status": "running"},
    ]

    tok = await _device(app, user_id="u1", scopes=("agents:read",))

    first_id = None
    async with _client(app) as c:
        async with c.stream("GET", "/api/device/v1/events", headers=_bearer(tok)) as r:
            assert r.status_code == 200
            chunks = await _collect_chunks(r, max_chunks=20, timeout=3.0)
            for line in "".join(chunks).splitlines():
                if line.startswith("id:"):
                    first_id = line.split(":", 1)[1].strip()

    assert first_id is not None

    resumed = False
    async with _client(app) as c:
        async with c.stream("GET", "/api/device/v1/events", headers={**_bearer(tok), "Last-Event-ID": first_id}) as r:
            assert r.status_code == 200
            chunks = await _collect_chunks(r, max_chunks=20, timeout=3.0)
            for line in "".join(chunks).splitlines():
                if line.startswith("id:"):
                    resumed = True
                    break

    assert resumed


# (e) Stale Last-Event-ID gets a snapshot event.
@pytest.mark.asyncio
async def test_events_stale_id_gets_snapshot(vapp, monkeypatch):
    from tinyagentos.routes import auth as auth_mod
    from tinyagentos.demo_mode import write_demo_mode

    _patch_intervals(monkeypatch)

    app = vapp
    monkeypatch.setattr(auth_mod, "_request_is_console", lambda _r: True)

    write_demo_mode(app.state.data_dir, False)
    app.state.config.agents = [
        {"name": "carol", "framework": "openclaw", "user_id": "u1", "status": "running"},
    ]

    tok = await _device(app, user_id="u1", scopes=("agents:read",))

    got_snapshot = False
    async with _client(app) as c:
        async with c.stream("GET", "/api/device/v1/events", headers={**_bearer(tok), "Last-Event-ID": "0"}) as r:
            assert r.status_code == 200
            chunks = await _collect_chunks(r, max_chunks=20, timeout=3.0)
            events = _parse_events(chunks)
            for evt, data in events:
                if evt == "snapshot":
                    got_snapshot = True
                    break

    assert got_snapshot


# (f) Revoking the device closes the stream.
@pytest.mark.asyncio
async def test_revoke_closes_stream(vapp, monkeypatch):
    from tinyagentos.routes import auth as auth_mod
    from tinyagentos.demo_mode import write_demo_mode

    _patch_intervals(monkeypatch)

    app = vapp
    monkeypatch.setattr(auth_mod, "_request_is_console", lambda _r: True)

    write_demo_mode(app.state.data_dir, False)
    app.state.config.agents = [
        {"name": "dave", "framework": "openclaw", "user_id": "u1", "status": "running"},
    ]

    st = app.state.device_store
    d = await st.register(user_id="u1", platform="ios")
    await st.set_scopes(d["device_id"], list(("agents:read",)))
    tok = d["scoped_token"]

    closed = False
    async with _client(app) as c:
        async with c.stream("GET", "/api/device/v1/events", headers=_bearer(tok)) as r:
            assert r.status_code == 200
            await st.revoke(d["device_id"])
            try:
                await _collect_chunks(r, max_chunks=5, timeout=2.0)
            except Exception:
                closed = True

    assert closed


# (i) Demo stops on switch-off: no demo agent or demo decision after the flip.
@pytest.mark.asyncio
async def test_stream_stops_demo_after_switch_off(vapp, monkeypatch):
    from tinyagentos.routes import auth as auth_mod
    from tinyagentos.demo_mode import write_demo_mode

    _patch_intervals(monkeypatch)

    app = vapp
    monkeypatch.setattr(auth_mod, "_request_is_console", lambda _r: True)
    monkeypatch.setattr(auth_mod, "_demo_task_clock", lambda: 0.0)

    monkeypatch.setenv("TAOS_LOCK_DEMO_AGENTS", "DemoX:openclaw:Drafting")
    monkeypatch.setenv("TAOS_LOCK_DEMO_DECISION", "Ship it?")
    monkeypatch.setenv("TAOS_LOCK_DEMO_DECISION_AGENT", "DemoX")
    write_demo_mode(app.state.data_dir, True)

    tok = await _device(app, user_id="u1", scopes=("agents:read",))

    post_flip_demo = False
    async with _client(app) as c:
        async with c.stream("GET", "/api/device/v1/events", headers=_bearer(tok)) as r:
            assert r.status_code == 200
            # consume initial events
            pre_flip = await _collect_chunks(r, max_chunks=20, timeout=2.0)
            # flip the switch off
            write_demo_mode(app.state.data_dir, False)
            # advance clock past any armed timer
            monkeypatch.setattr(auth_mod, "_demo_task_clock", lambda: 9999.0)
            post_flip = await _collect_chunks(r, max_chunks=20, timeout=2.0)
            for chunk in post_flip:
                for evt_line in chunk.splitlines():
                    if evt_line.startswith("event:"):
                        pass
                    elif evt_line.startswith("data:"):
                        try:
                            data = json.loads(evt_line.split(":", 1)[1].strip())
                        except Exception:
                            continue
                        if isinstance(data, dict):
                            if data.get("demo") is True:
                                post_flip_demo = True
                            dec = data.get("decision") or {}
                            if dec.get("demo") is True or dec.get("id") == "":
                                if data.get("name") == "DemoX":
                                    post_flip_demo = True

    assert not post_flip_demo


# (j2) Avatar change triggers agent.upsert with new hash.
@pytest.mark.asyncio
async def test_stream_upsert_on_avatar_change(vapp, monkeypatch):
    from tinyagentos.routes import auth as auth_mod
    from tinyagentos import agent_avatars as avatars
    from tinyagentos.demo_mode import write_demo_mode

    _patch_intervals(monkeypatch)

    app = vapp
    monkeypatch.setattr(auth_mod, "_request_is_console", lambda _r: True)

    write_demo_mode(app.state.data_dir, False)
    app.state.config.agents = [
        {"name": "eve-agent", "framework": "openclaw", "user_id": "u1", "status": "running"},
    ]

    tok = await _device(app, user_id="u1", scopes=("agents:read",))

    with monkeypatch.context() as mp:
        tmpdir = Path(app.state.data_dir) / "avatars"
        tmpdir.mkdir(parents=True, exist_ok=True)
        mp.setattr(avatars, "LOCK_AVATAR_DIR", str(tmpdir))

        slug = avatars._avatar_slug("eve-agent")
        img = tmpdir / f"{slug}.jpg"
        img.write_bytes(b"first-avatar-content")

        async with _client(app) as c:
            async with c.stream("GET", "/api/device/v1/events", headers=_bearer(tok)) as r:
                assert r.status_code == 200
                chunks1 = await _collect_chunks(r, max_chunks=20, timeout=2.0)
                events1 = _parse_events(chunks1)
                initial_hash = None
                for evt, data in events1:
                    if evt == "agent.upsert" and data.get("name") == "eve-agent":
                        av = data.get("avatar") or {}
                        initial_hash = av.get("hash")
                        break

        assert initial_hash is not None
        first_hash = initial_hash

        img.write_bytes(b"second-avatar-content-changed")

        async with _client(app) as c:
            async with c.stream("GET", "/api/device/v1/events", headers=_bearer(tok)) as r:
                assert r.status_code == 200
                chunks2 = await _collect_chunks(r, max_chunks=20, timeout=2.0)
                events2 = _parse_events(chunks2)
                got_new_hash = False
                for evt, data in events2:
                    if evt == "agent.upsert" and data.get("name") == "eve-agent":
                        av = data.get("avatar") or {}
                        new_hash = av.get("hash")
                        if new_hash is not None and new_hash != first_hash:
                            got_new_hash = True
                            break

        assert got_new_hash

"""P0 S3: GET /api/device/v1/events SSE.

Tests the SSE stream by calling `_events_stream` directly with a mock
request, bypassing the ASGI layer which blocks on infinite streams.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

import tinyagentos.routes.auth as auth_mod
from tinyagentos.demo_mode import DEMO_MODE_FILE, write_demo_mode
from tinyagentos.routes.device_state import _events_stream

PLAIN = "http://localhost:6969"


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


def _parse_events(chunks):
    """Return a list of (event_type, data_dict) from raw text chunks."""
    events = []
    for chunk in chunks:
        if isinstance(chunk, bytes):
            chunk = chunk.decode("utf-8")
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
    monkeypatch.setattr(ds_mod, "_SLEEP_STEP_S", 0.05)


class _MockRequest:
    def __init__(self, app, headers):
        self.app = app
        self.headers = headers
        self._disconnected = False

    def header(self, name, default=""):
        return self.headers.get(name.lower(), default)

    async def is_disconnected(self):
        return self._disconnected


async def _collect_from_stream(gen, max_chunks=20, timeout=3.0):
    """Collect chunks from an async generator with a timeout.
    
    Returns (chunks, finished) where finished is True if the generator
    raised StopAsyncIteration before the timeout.
    """
    chunks = []
    finished = False
    start = time.monotonic()
    while len(chunks) < max_chunks:
        remaining = timeout - (time.monotonic() - start)
        if remaining <= 0:
            break
        try:
            task = asyncio.ensure_future(gen.__anext__())
            done, pending = await asyncio.wait([task], timeout=min(remaining, 0.5))
            if pending:
                for p in pending:
                    p.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                break
            chunk = done.pop().result()
            chunks.append(chunk)
        except StopAsyncIteration:
            finished = True
            break
        except Exception:
            break
    return chunks, finished


def _ticking_clock(monkeypatch):
    # Every _clock() call advances 1.0s, so with the patched 0.1s poll and
    # 0.2s heartbeat EVERY loop tick polls once and then yields one ": ping".
    from tinyagentos.routes import device_state as ds_mod
    t = [0.0]
    def _tick():
        t[0] += 1.0
        return t[0]
    monkeypatch.setattr(ds_mod, "_clock", _tick)


async def _read_until_ping(gen, limit=50):
    # Reads frames up to and including the next heartbeat frame. Never cancels
    # the generator. Each __anext__ returns within one loop tick.
    frames = []
    for _ in range(limit):
        frame = await gen.__anext__()
        frames.append(frame)
        if b": ping" in frame:
            return frames
    raise AssertionError(f"no heartbeat within {limit} frames")


def _make_mock_request(app, headers, last_event_id="0"):
    req = _MockRequest(app, headers)
    req.headers["last-event-id"] = last_event_id
    return req


def _avatar_slug(n):
    from tinyagentos import agent_avatars as avatars
    return avatars._avatar_slug(n)


async def _open_stream(app, monkeypatch, agents, last_event_id="0"):
    from tinyagentos.routes import auth as auth_mod
    from tinyagentos.demo_mode import write_demo_mode
    _patch_intervals(monkeypatch)
    _ticking_clock(monkeypatch)
    monkeypatch.setattr(auth_mod, "_request_is_console", lambda _r: True)
    write_demo_mode(app.state.data_dir, False)
    app.state.config.agents = agents
    tok = await _device(app, user_id="u1", scopes=("agents:read",))
    req = _make_mock_request(app, {"authorization": f"Bearer {tok}"}, last_event_id=last_event_id)
    gen = _events_stream(req, {"user_id": "u1"})
    await _read_until_ping(gen)
    return gen


def _avatar_dir(app, monkeypatch, names):
    from tinyagentos import agent_avatars as avatars
    d = Path(app.state.data_dir) / "avatars"
    d.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(avatars, "LOCK_AVATAR_DIR", str(d))
    for n in names:
        (d / (_avatar_slug(n) + ".jpg")).write_bytes(b"orig-" + n.encode())
    return d


async def _ticks(gen, n=3):
    out = []
    for _ in range(n):
        out += _parse_events(await _read_until_ping(gen))
    return out


_TWO = [
    {"name": "a1", "framework": "openclaw", "user_id": "u1", "status": "running"},
    {"name": "a2", "framework": "openclaw", "user_id": "u1", "status": "running"},
]


@pytest_asyncio.fixture(autouse=True)
async def _clear_owner_buffers():
    from tinyagentos.routes import device_state as ds_mod
    ds_mod._owner_buffers.clear()
    yield


# (d) SSE route exists and emits agent.upsert events keyed by name.
@pytest.mark.asyncio
async def test_events_emits_upsert_keyed_by_name(vapp, monkeypatch):
    from tinyagentos.routes import auth as auth_mod
    from tinyagentos.demo_mode import write_demo_mode
    from tinyagentos.routes import device_state as ds_mod

    _patch_intervals(monkeypatch)

    app = vapp
    monkeypatch.setattr(auth_mod, "_request_is_console", lambda _r: True)

    write_demo_mode(app.state.data_dir, False)
    app.state.config.agents = [
        {"name": "alice", "framework": "openclaw", "user_id": "u1", "status": "running"},
    ]

    tok = await _device(app, user_id="u1", scopes=("agents:read",))
    headers = {"authorization": f"Bearer {tok}"}

    device = {"user_id": "u1"}
    gen = _events_stream(_make_mock_request(app, headers), device)
    chunks, _ = await _collect_from_stream(gen, max_chunks=20, timeout=3.0)

    events = _parse_events(chunks)
    upsert_names = {data["name"] for evt, data in events if evt == "agent.upsert" and "name" in data}
    assert "alice" in upsert_names


# (e) Last-Event-ID resumes from the next event.
@pytest.mark.asyncio
async def test_events_last_event_id_resume(vapp, monkeypatch):
    from tinyagentos.routes import auth as auth_mod
    from tinyagentos.demo_mode import write_demo_mode

    _patch_intervals(monkeypatch)
    _ticking_clock(monkeypatch)

    app = vapp
    monkeypatch.setattr(auth_mod, "_request_is_console", lambda _r: True)

    write_demo_mode(app.state.data_dir, False)
    app.state.config.agents = [
        {"name": "resume-bob", "framework": "openclaw", "user_id": "u1", "status": "running"},
    ]

    tok = await _device(app, user_id="u1", scopes=("agents:read",))
    headers = {"authorization": f"Bearer {tok}"}
    device = {"user_id": "u1"}

    # First connection: consume initial events and first heartbeat.
    gen1 = _events_stream(_make_mock_request(app, headers), device)
    first_chunks = await _read_until_ping(gen1)

    # Trigger one agent change on the next poll tick.
    app.state.config.agents = [
        {"name": "resume-bob", "framework": "hermes", "user_id": "u1", "status": "running"},
    ]

    # Read the next poll diff and heartbeat from stream 1.
    second_chunks = await _read_until_ping(gen1)

    change_id = None
    for chunk in second_chunks:
        text = chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk
        for line in text.splitlines():
            if line.startswith("id:"):
                change_id = line.split(":", 1)[1].strip()

    assert change_id is not None
    change_id_int = int(change_id)

    # Second connection with Last-Event-ID just before the change.
    gen2 = _events_stream(
        _make_mock_request(app, headers, last_event_id=str(change_id_int - 1)),
        device,
    )
    resume_chunks = await _read_until_ping(gen2)

    # First frame must be the buffered upsert, not a snapshot.
    first_text = resume_chunks[0].decode("utf-8") if isinstance(resume_chunks[0], bytes) else resume_chunks[0]
    assert "event: agent.upsert" in first_text
    assert "event: snapshot" not in first_text

    resume_id = None
    for line in first_text.splitlines():
        if line.startswith("id:"):
            resume_id = line.split(":", 1)[1].strip()
            break

    assert resume_id == change_id

    for chunk in resume_chunks:
        text = chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk
        assert "event: snapshot" not in text

    await gen1.aclose()
    await gen2.aclose()


# (e) Stale Last-Event-ID gets a snapshot event.
@pytest.mark.asyncio
async def test_events_stale_id_gets_snapshot(vapp, monkeypatch):
    from tinyagentos.routes import auth as auth_mod
    from tinyagentos.demo_mode import write_demo_mode
    from tinyagentos.routes import device_state as ds_mod

    _patch_intervals(monkeypatch)
    _ticking_clock(monkeypatch)

    app = vapp
    monkeypatch.setattr(auth_mod, "_request_is_console", lambda _r: True)

    write_demo_mode(app.state.data_dir, False)
    app.state.config.agents = [
        {"name": "carol", "framework": "openclaw", "user_id": "u1", "status": "running"},
    ]

    tok = await _device(app, user_id="u1", scopes=("agents:read",))
    headers = {"authorization": f"Bearer {tok}"}

    device = {"user_id": "u1"}

    got_snapshot = False
    gen = _events_stream(_make_mock_request(app, headers, last_event_id="1"), device)
    chunks, _ = await _collect_from_stream(gen, max_chunks=20, timeout=3.0)
    events = _parse_events(chunks)
    for evt, data in events:
        if evt == "snapshot":
            got_snapshot = True
            break

    assert got_snapshot


# (n) Heartbeat carries no id line.
@pytest.mark.asyncio
async def test_heartbeat_has_no_id(vapp, monkeypatch):
    from tinyagentos.routes import auth as auth_mod
    from tinyagentos.demo_mode import write_demo_mode

    _patch_intervals(monkeypatch)
    _ticking_clock(monkeypatch)

    app = vapp
    monkeypatch.setattr(auth_mod, "_request_is_console", lambda _r: True)

    write_demo_mode(app.state.data_dir, False)
    app.state.config.agents = [
        {"name": "heartbeat-agent", "framework": "openclaw", "user_id": "u1", "status": "running"},
    ]

    tok = await _device(app, user_id="u1", scopes=("agents:read",))
    headers = {"authorization": f"Bearer {tok}"}
    device = {"user_id": "u1"}

    gen = _events_stream(_make_mock_request(app, headers), device)
    chunks = await _read_until_ping(gen)

    for chunk in chunks:
        text = chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk
        if ": ping" in text:
            for line in text.splitlines():
                assert not line.startswith("id:"), "heartbeat frame must not contain an id line"
            break

    await gen.aclose()


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

    headers = {"authorization": f"Bearer {tok}"}
    device = {"user_id": "u1"}

    closed = False
    gen = _events_stream(_make_mock_request(app, headers), device)

    # Read initial events.
    try:
        await gen.__anext__()
    except StopAsyncIteration:
        closed = True

    # Revoke the device while the stream is open.
    await st.revoke(d["device_id"])

    # The next read should detect the revocation and close the stream.
    try:
        await asyncio.wait_for(gen.__anext__(), timeout=5.0)
    except StopAsyncIteration:
        closed = True

    assert closed


# (i) Demo stops on switch-off: no demo agent or demo decision after the flip.
@pytest.mark.asyncio
async def test_stream_stops_demo_after_switch_off(vapp, monkeypatch):
    from tinyagentos.routes import auth as auth_mod
    from tinyagentos.routes import device_state as ds_mod
    from tinyagentos.demo_mode import write_demo_mode

    _patch_intervals(monkeypatch)

    app = vapp
    monkeypatch.setattr(auth_mod, "_request_is_console", lambda _r: True)

    _ticking_clock(monkeypatch)

    monkeypatch.setenv("TAOS_LOCK_DEMO_AGENTS", "DemoX:openclaw:Drafting")
    monkeypatch.setenv("TAOS_LOCK_DEMO_DECISION", "Ship it?")
    monkeypatch.setenv("TAOS_LOCK_DEMO_DECISION_AGENT", "DemoX")
    write_demo_mode(app.state.data_dir, True)

    tok = await _device(app, user_id="u1", scopes=("agents:read",))
    headers = {"authorization": f"Bearer {tok}"}
    device = {"user_id": "u1"}

    gen = _events_stream(_make_mock_request(app, headers), device)
    before = _parse_events(await _read_until_ping(gen))
    assert ("agent.upsert", "DemoX") in [(e, d.get("name")) for e, d in before]   # positive control
    write_demo_mode(app.state.data_dir, False)
    after = _parse_events(await _read_until_ping(gen))
    assert ("agent.remove", {"name": "DemoX"}) in after
    assert not [d for e, d in after if e == "agent.upsert" and d.get("name") == "DemoX"]
    await gen.aclose()


# (j2) Avatar change triggers agent.upsert with new hash.
@pytest.mark.asyncio
async def test_stream_upsert_on_avatar_change(vapp, monkeypatch):
    from tinyagentos.routes import auth as auth_mod
    from tinyagentos import agent_avatars as avatars
    from tinyagentos.demo_mode import write_demo_mode

    _patch_intervals(monkeypatch)

    _ticking_clock(monkeypatch)

    app = vapp
    monkeypatch.setattr(auth_mod, "_request_is_console", lambda _r: True)

    write_demo_mode(app.state.data_dir, False)
    app.state.config.agents = [
        {"name": "eve-agent", "framework": "openclaw", "user_id": "u1", "status": "running"},
    ]

    tok = await _device(app, user_id="u1", scopes=("agents:read",))
    headers = {"authorization": f"Bearer {tok}"}
    device = {"user_id": "u1"}

    with monkeypatch.context() as mp:
        tmpdir = Path(app.state.data_dir) / "avatars"
        tmpdir.mkdir(parents=True, exist_ok=True)
        mp.setattr(avatars, "LOCK_AVATAR_DIR", str(tmpdir))

        slug = avatars._avatar_slug("eve-agent")
        img = tmpdir / f"{slug}.jpg"
        img.write_bytes(b"first-avatar-content")

        gen = _events_stream(_make_mock_request(app, headers), device)
        first = _parse_events(await _read_until_ping(gen))
        initial_hash = None
        for evt, data in first:
            if evt == "agent.upsert" and data.get("name") == "eve-agent":
                av = data.get("avatar") or {}
                initial_hash = av.get("hash")
                break

        assert initial_hash is not None
        first_hash = initial_hash

        img.write_bytes(b"second-avatar-content-changed")

        second = _parse_events(await _read_until_ping(gen))
        got_new_hash = False
        for evt, data in second:
            if evt == "agent.upsert" and data.get("name") == "eve-agent":
                av = data.get("avatar") or {}
                new_hash = av.get("hash")
                if new_hash is not None and new_hash != first_hash:
                    got_new_hash = True
                    break

    assert got_new_hash
    await gen.aclose()


# (k) HTTP route: device without agents:read scope is denied.
@pytest.mark.asyncio
async def test_events_route_refuses_device_without_agents_read(vapp):
    app = vapp
    tok = await _device(app, user_id="u1", scopes=())
    async with _client(app) as c:
        r = await c.get("/api/device/v1/events", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 403, r.text
    assert "device_scope_missing" in r.text


# (l) Scope loss (AGENTS_READ removed) closes stream.
@pytest.mark.asyncio
async def test_scope_loss_closes_stream(vapp, monkeypatch):
    from tinyagentos.routes import auth as auth_mod
    from tinyagentos.demo_mode import write_demo_mode

    _patch_intervals(monkeypatch)
    _ticking_clock(monkeypatch)

    app = vapp
    monkeypatch.setattr(auth_mod, "_request_is_console", lambda _r: True)

    write_demo_mode(app.state.data_dir, False)
    app.state.config.agents = [
        {"name": "scope-test-agent", "framework": "openclaw", "user_id": "u1", "status": "running"},
    ]

    # Create device with agents:read scope
    st = app.state.device_store
    d = await st.register(user_id="u1", platform="ios")
    await st.set_scopes(d["device_id"], list(("agents:read",)))
    tok = d["scoped_token"]

    headers = {"authorization": f"Bearer {tok}"}
    device = {"user_id": "u1"}

    gen = _events_stream(_make_mock_request(app, headers), device)

    # Read initial events.
    await _read_until_ping(gen)

    # Remove agents:read scope from the device
    await st.set_scopes(d["device_id"], [])

    # The next read should detect the scope loss and close the stream.
    with pytest.raises(StopAsyncIteration):
        await gen.__anext__()


# (RED) First agent avatar change emits agent.upsert keyed by name.
@pytest.mark.asyncio
async def test_first_agent_change_emits_upsert(vapp, monkeypatch):
    d = _avatar_dir(vapp, monkeypatch, ["a1", "a2"])
    gen = await _open_stream(vapp, monkeypatch, list(_TWO))
    (d / (_avatar_slug("a1") + ".jpg")).write_bytes(b"changed-a1")
    evs = await _ticks(gen)
    assert any(e == "agent.upsert" and x.get("name") == "a1" for e, x in evs)
    await gen.aclose()


# (RED) Second agent avatar change emits agent.upsert keyed by name.
@pytest.mark.asyncio
async def test_second_agent_change_emits_upsert(vapp, monkeypatch):
    d = _avatar_dir(vapp, monkeypatch, ["a1", "a2"])
    gen = await _open_stream(vapp, monkeypatch, list(_TWO))
    (d / (_avatar_slug("a2") + ".jpg")).write_bytes(b"changed-a2")
    evs = await _ticks(gen)
    assert any(e == "agent.upsert" and x.get("name") == "a2" for e, x in evs)
    await gen.aclose()


# (RED) Removing one of two agents emits agent.remove for the removed one.
@pytest.mark.asyncio
async def test_one_of_two_removed_emits_remove(vapp, monkeypatch):
    gen = await _open_stream(vapp, monkeypatch, list(_TWO))
    vapp.state.config.agents = [_TWO[0]]
    evs = await _ticks(gen)
    assert any(e == "agent.remove" and x.get("name") == "a2" for e, x in evs)
    await gen.aclose()


# (RED) Removing all agents emits agent.remove for the last remaining one.
@pytest.mark.asyncio
async def test_all_agents_removed_emits_remove(vapp, monkeypatch):
    gen = await _open_stream(vapp, monkeypatch, [_TWO[0]])
    vapp.state.config.agents = []
    evs = await _ticks(gen)
    assert any(e == "agent.remove" and x.get("name") == "a1" for e, x in evs)
    await gen.aclose()


@pytest.mark.asyncio
async def test_poll_tick_rechecks_auth_between_frames(vapp, monkeypatch):
    gen = await _open_stream(vapp, monkeypatch, list(_TWO))
    vapp.state.config.agents = []   # next poll tick yields TWO agent.remove frames
    # Read frames until we get an agent.remove event (skip ': ping' frames)
    while True:
        chunk = await asyncio.wait_for(gen.__anext__(), timeout=5.0)
        event = _parse_events([chunk])
        if event and event[0][0] == "agent.remove":
            break
    # Now simulate device revocation
    async def _revoked(*_a, **_k):
        return False
    import tinyagentos.routes.device_state as ds_mod
    monkeypatch.setattr(ds_mod, "_device_still_authorized", _revoked)
    # The next read should raise StopAsyncIteration
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(gen.__anext__(), timeout=5.0)


# (m) Decision replaced (different id) emits close then open.
@pytest.mark.asyncio
async def test_decision_replace_emits_close_then_open(vapp, monkeypatch):
    gen = await _open_stream(vapp, monkeypatch, [{"name": "dec-agent", "framework": "openclaw", "user_id": "u1", "status": "running"}])
    store = vapp.state.decision_store
    opts = [{"label": "Approve", "value": "approve"}, {"label": "Deny", "value": "deny"}]
    d1 = await store.create(from_agent="dec-agent", question="first?", type="approve_deny", user_id="u1", options=opts)
    evs = await _ticks(gen)
    opened = [x for e, x in evs if e == "decision.open" and x.get("name") == "dec-agent"]
    assert opened and opened[0]["decision"]["id"] == d1["id"]
    await store.answer(d1["id"], "approve", answered_by="u1")
    d2 = await store.create(from_agent="dec-agent", question="second?", type="approve_deny", user_id="u1", options=opts)
    evs = await _ticks(gen)
    kinds = [(e, x.get("decision_id") or (x.get("decision") or {}).get("id")) for e, x in evs if e.startswith("decision.")]
    assert kinds == [("decision.close", d1["id"]), ("decision.open", d2["id"])]
    await store.answer(d2["id"], "deny", answered_by="u1")
    evs = await _ticks(gen)
    assert [e for e, _ in evs if e.startswith("decision.")] == ["decision.close"]
    await gen.aclose()


async def _resume_after_disconnect(vapp, monkeypatch, change):
    from tinyagentos.routes import device_state as ds_mod
    from tinyagentos.routes import auth as auth_mod
    from tinyagentos.demo_mode import write_demo_mode
    d = _avatar_dir(vapp, monkeypatch, ["a1"])
    gen = await _open_stream(vapp, monkeypatch, [_TWO[0]])
    await _read_until_ping(gen)
    last = max(e for e, _, _ in ds_mod._owner_buffers["u1"]["events"])
    await gen.aclose()
    if change:
        (d / (_avatar_slug("a1") + ".jpg")).write_bytes(b"changed-while-offline")
    # Use low-level stream to capture first poll after resume
    _patch_intervals(monkeypatch)
    _ticking_clock(monkeypatch)
    monkeypatch.setattr(auth_mod, "_request_is_console", lambda _r: True)
    write_demo_mode(vapp.state.data_dir, False)
    vapp.state.config.agents = [_TWO[0]]
    tok = await _device(vapp, user_id="u1", scopes=("agents:read",))
    req = _make_mock_request(vapp, {"authorization": f"Bearer {tok}"}, last_event_id=str(last))
    gen2 = _events_stream(req, {"user_id": "u1"})
    frames = await _read_until_ping(gen2)  # captures replay + first poll + heartbeat
    evs = _parse_events(frames)
    # Read additional ticks for consistency
    evs += await _ticks(gen2)
    await gen2.aclose()
    return evs


@pytest.mark.asyncio
async def test_resume_delivers_change_made_while_disconnected(vapp, monkeypatch):
    evs = await _resume_after_disconnect(vapp, monkeypatch, change=True)
    assert any(e == "agent.upsert" and x.get("name") == "a1" for e, x in evs)


@pytest.mark.asyncio
async def test_resume_quiet_when_nothing_changed(vapp, monkeypatch):
    evs = await _resume_after_disconnect(vapp, monkeypatch, change=False)
    assert not any(e in ("agent.upsert", "snapshot") for e, _ in evs)


@pytest.mark.asyncio
async def test_resume_gets_change_recorded_by_other_stream_mid_replay(vapp, monkeypatch):
    from tinyagentos.routes import device_state as ds_mod
    from tinyagentos.routes import auth as auth_mod
    from tinyagentos.demo_mode import write_demo_mode
    d = _avatar_dir(vapp, monkeypatch, ["a1", "a2"])
    gen_a = await _open_stream(vapp, monkeypatch, list(_TWO))
    first = min(e for e, _, _ in ds_mod._owner_buffers["u1"]["events"])
    _patch_intervals(monkeypatch)
    _ticking_clock(monkeypatch)
    monkeypatch.setattr(auth_mod, "_request_is_console", lambda _r: True)
    write_demo_mode(vapp.state.data_dir, False)
    tok = await _device(vapp, user_id="u1", scopes=("agents:read",))
    req = _make_mock_request(vapp, {"authorization": f"Bearer {tok}"}, last_event_id=str(first))
    gen_b = _events_stream(req, {"user_id": "u1"})
    frames = [await asyncio.wait_for(gen_b.__anext__(), timeout=5.0)]  # one replayed frame; gen_b is suspended mid-replay
    (d / (_avatar_slug("a1") + ".jpg")).write_bytes(b"changed-mid-replay")
    await _read_until_ping(gen_a)  # the OTHER stream records the a1 change and advances last_by_name
    frames += await _read_until_ping(gen_b)
    evs = _parse_events(frames) + await _ticks(gen_b)
    await gen_a.aclose()
    await gen_b.aclose()
    assert any(e == "agent.upsert" and x.get("name") == "a1" for e, x in evs), evs


@pytest.mark.asyncio
async def test_plain_decision_close_carries_decision_id(monkeypatch, vapp):
    from tinyagentos.routes import device_state as ds_mod
    _patch_intervals(monkeypatch)
    _ticking_clock(monkeypatch)
    gen = await _open_stream(vapp, monkeypatch, [{"name": "dec-agent", "framework": "openclaw", "user_id": "u1", "status": "running"}])
    store = vapp.state.decision_store
    opts = [{"label": "Approve", "value": "approve"}, {"label": "Deny", "value": "deny"}]
    d1 = await store.create(from_agent="dec-agent", question="first?", type="approve_deny", user_id="u1", options=opts)
    evs = await _ticks(gen)
    opened = [x for e, x in evs if e == "decision.open" and x.get("decision") and x.get("decision", {}).get("id") == d1["id"]]
    assert opened and opened[0]["decision"]["id"] == d1["id"]
    await store.answer(d1["id"], "approve", answered_by="u1")
    evs = await _ticks(gen)
    kinds = [(e, x.get("decision_id") or (x.get("decision") or {}).get("id")) for e, x in evs if e.startswith("decision.")]
    assert kinds == [("decision.close", d1["id"])], kinds
    await gen.aclose()


async def _revoked_before_first_frame(app, monkeypatch, last_event_id):
    from tinyagentos.routes import auth as auth_mod
    from tinyagentos.demo_mode import write_demo_mode
    _patch_intervals(monkeypatch)
    _ticking_clock(monkeypatch)
    monkeypatch.setattr(auth_mod, "_request_is_console", lambda _r: True)
    write_demo_mode(app.state.data_dir, False)
    app.state.config.agents = _TWO
    st = app.state.device_store
    d = await st.register(user_id="u1", platform="ios")
    await st.set_scopes(d["device_id"], ["agents:read"])
    req = _make_mock_request(app, {"authorization": f"Bearer {d['scoped_token']}"}, last_event_id=last_event_id)
    gen = _events_stream(req, {"user_id": "u1"})
    await st.revoke(d["device_id"])
    frames = []
    try:
        while True:
            frames.append(await asyncio.wait_for(gen.__anext__(), timeout=5.0))
    except StopAsyncIteration:
        pass
    return [k for k, _ in _parse_events(frames)] if frames else []


@pytest.mark.asyncio
async def test_revoked_before_first_frame_initial_sends_nothing(vapp, monkeypatch):
    assert await _revoked_before_first_frame(vapp, monkeypatch, "0") == []


@pytest.mark.asyncio
async def test_revoked_before_first_frame_snapshot_sends_nothing(vapp, monkeypatch):
    assert await _revoked_before_first_frame(vapp, monkeypatch, "999") == []


@pytest.mark.asyncio
async def test_emit_event_ids_recorded_in_order():
    from tinyagentos.routes import device_state as ds
    ids = await asyncio.gather(*[ds._emit_event("order-owner", "agent.upsert", "{}") for _ in range(50)])
    recorded = [eid for eid, _, _ in ds._get_owner_buffer("order-owner")["events"]]
    assert sorted(ids) == list(range(ids[0], ids[0] + 50))
    assert recorded == sorted(recorded)


@pytest.mark.asyncio
async def test_seed_id_epoch_survives_clock_regression(monkeypatch, tmp_path):
    from tinyagentos.routes import device_state as ds
    import time as _time
    # Write a high-water mark far ahead of the wall clock (simulating regression)
    stored_val = str(int(_time.time() * 1000) + 10_000_000)
    with monkeypatch.context() as mp:
        tmpdir = tmp_path
        mp.setattr(ds, "_HWM_PATH", tmpdir / "device_event_hwm")
        mp.setattr(ds, "_ID_EPOCH", int(_time.time() * 1000))
        # Write stored value that simulates clock going backwards
        (tmpdir / "device_event_hwm").write_text(stored_val)
        # Clear owner buffers as if a restart occurred
        ds._owner_buffers.clear()
        ds.seed_id_epoch(tmpdir)
        # Emit one event and assert its id > the stored value
        eid = await ds._emit_event("test-owner", "agent.upsert", "{}")
        assert eid > int(stored_val), f"event id {eid} not > stored {stored_val}"


# (RED) IDs after restart exceed IDs from previous process.
@pytest.mark.asyncio
async def test_ids_after_restart_exceed_ids_from_previous_process(monkeypatch):
    from tinyagentos.routes import device_state as ds

    # Emit first event and record its id.
    first_id = await ds._emit_event("restart-owner", "agent.upsert", "{}")

    # Simulate a restart: clear buffers and set _ID_EPOCH to a later value.
    ds._owner_buffers.clear()
    later_epoch = ds._ID_EPOCH + 1000
    monkeypatch.setattr(ds, "_ID_EPOCH", later_epoch)

    # Emit again after "restart".
    second_id = await ds._emit_event("restart-owner", "agent.upsert", "{}")

    # The new id must be greater than the old one.
    assert second_id > first_id


# (RED) Unreadable HWM file logs warning, not raising, and stores 0.
@pytest.mark.asyncio
async def test_seed_id_epoch_logs_unreadable_hwm(monkeypatch, caplog, tmp_path):
    from tinyagentos.routes import device_state as ds
    import time as _time
    import logging
    
    caplog.set_level(logging.WARNING)
    with monkeypatch.context() as mp:
        # Write a non-integer value to the HWM file to simulate corruption
        (tmp_path / "device_event_hwm").write_bytes(b"not-a-number")
        
        # Clear module state before test
        mp.setattr(ds, "_HWM_PATH", tmp_path / "device_event_hwm")
        mp.setattr(ds, "_ID_EPOCH", int(_time.time() * 1000))
        
        # Call seed_id_epoch - should log warning but not raise
        ds.seed_id_epoch(tmp_path)
        
        # Verify warning was logged with the expected message
        assert "device_event_hwm unreadable" in caplog.text
        # Verify _ID_EPOCH was set to a recent time (within 5 seconds)
        assert ds._ID_EPOCH >= int(_time.time() * 1000) - 5000


# (RED) Silent when HWM file is missing.
@pytest.mark.asyncio
async def test_seed_id_epoch_silent_when_hwm_missing(monkeypatch, caplog, tmp_path):
    from tinyagentos.routes import device_state as ds
    import time as _time
    import logging
    
    caplog.set_level(logging.WARNING)
    with monkeypatch.context() as mp:
        # Do not create the HWM file - it should be silently missing
        
        # Clear module state before test
        mp.setattr(ds, "_HWM_PATH", tmp_path / "device_event_hwm")
        mp.setattr(ds, "_ID_EPOCH", int(_time.time() * 1000))
        
        # Call seed_id_epoch - should not log any unreadable warning
        ds.seed_id_epoch(tmp_path)
        
        # Verify no "unreadable" warning was logged
        assert "unreadable" not in caplog.text


# (RED) seed_id_epoch advances existing buffers whose next_id is below new epoch.
@pytest.mark.asyncio
async def test_seed_id_epoch_advances_existing_buffers(monkeypatch, tmp_path):
    from tinyagentos.routes import device_state as ds
    import time as _time
    
    with monkeypatch.context() as mp:
        # Clear owner buffers as if a restart occurred
        ds._owner_buffers.clear()
        
        # Create an owner buffer with an old next_id
        owner_id = "owner-a"
        buf = ds._get_owner_buffer(owner_id)
        
        # Write a HWM file with a value far in the future (epoch + 10 * _HWM_STEP)
        future_epoch = int(_time.time() * 1000) + 10 * ds._HWM_STEP
        mp.setattr(ds, "_HWM_PATH", tmp_path / "device_event_hwm")
        (tmp_path / "device_event_hwm").write_text(str(future_epoch))
        
        # Set the current _ID_EPOCH to something in the past
        current_epoch = int(_time.time() * 1000) - 1000
        mp.setattr(ds, "_ID_EPOCH", current_epoch)
        
        # Call seed_id_epoch with the tmp_path containing our HWM file
        ds.seed_id_epoch(tmp_path)
        
        # Verify that the owner's buffer next_id was advanced to the new _ID_EPOCH
        assert ds._owner_buffers[owner_id]["next_id"] == ds._ID_EPOCH


# (RED) _emit_event logs failed HWM write but still emits the event.
@pytest.mark.asyncio
async def test_emit_event_failed_hwm_write_still_emits(monkeypatch, caplog, tmp_path):
    from tinyagentos.routes import device_state as ds
    import time as _time
    import logging
    
    caplog.set_level(logging.WARNING)
    
    with monkeypatch.context() as mp:
        # Mock atomic_write_text to always raise OSError
        original_atomic_write_text = ds.atomic_write_text
        def mock_atomic_write_text(path, content):
            raise OSError("Mocked write failure")
        
        mp.setattr(ds, "atomic_write_text", mock_atomic_write_text)
        
        # Setup a temporary directory for _HWM_PATH
        mp.setattr(ds, "_HWM_PATH", tmp_path / "device_event_hwm")
        
        # Clear owner buffers
        ds._owner_buffers.clear()
        
        # Get owner buffer so we have a known next_id
        owner_id = "owner-b"
        buf = ds._get_owner_buffer(owner_id)
        
        # Set next_id to a multiple of _HWM_STEP to trigger HWM write
        current_id = buf["next_id"]
        while current_id % ds._HWM_STEP != 0:
            current_id += 1
        buf["next_id"] = current_id
        
        # Emit event - should log warning but still succeed
        eid = await ds._emit_event(owner_id, "upsert", "{}")
        
        # Verify the event was still emitted and is in the buffer
        assert eid == current_id
        assert len(ds._owner_buffers[owner_id]["events"]) == 1
        assert ds._owner_buffers[owner_id]["events"][0][0] == eid
        
        # Verify warning was logged
        assert "failed to write event hwm" in caplog.text

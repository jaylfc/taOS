"""HTTP-layer tests for the taOSusb Bluetooth pairing (S1) routes
(routes/cluster_ble.py): GET /api/cluster/ble/scan, POST .../pair/start,
.../pair/confirm, .../pair/cancel.

Hostile cases first: non-admin 403 on every route, malformed bodies, bad
address, unpairable/already-paired boards, a BLE timeout, a third
concurrent session, confirm twice, a board-rejected provision, and -- the
one that matters most -- the serialized JSON of every response never
contains the minted key's hex. Then the happy path end to end through the
routes: scan -> start -> confirm -> the node is listed with kind=device.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

import tinyagentos.cluster.ble.pairing as pairing_mod
from tinyagentos.cluster.ble.pairing import BlePairingManager
from cluster.conftest import FakeBoard, FakeTransport


@pytest.fixture(autouse=True)
def fast_timeouts(monkeypatch):
    monkeypatch.setattr(pairing_mod, "_HELLO_TIMEOUT_S", 0.3)
    monkeypatch.setattr(pairing_mod, "_PROVISION_TIMEOUT_S", 0.3)
    monkeypatch.setattr(pairing_mod, "_CONNECT_TIMEOUT_S", 0.3)


def _wire_fake_manager(app, boards, **conn_kwargs) -> FakeTransport:
    """Replace app.state.ble_pairing with one backed by a FakeTransport."""
    transport = FakeTransport(boards, **conn_kwargs)
    app.state.ble_pairing = BlePairingManager(
        data_dir=app.state.data_dir,
        cluster_manager=app.state.cluster_manager,
        pairing_store=app.state.cluster_pairing,
        bind_port=6969,
        transport=transport,
    )
    return transport


@pytest_asyncio.fixture
async def unauthed_client(app, tmp_data_dir):
    """Client with no session cookie (mirrors test_routes_cluster_pairing.py)."""
    store = app.state.cluster_pairing
    await store.init()
    app.state._startup_complete = True
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await store.close()


# ---------------------------------------------------------------------------
# Admin gate: every route 403s a non-admin session
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_scan_requires_admin(unauthed_client):
    resp = await unauthed_client.get("/api/cluster/ble/scan")
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_pair_start_requires_admin(unauthed_client):
    resp = await unauthed_client.post("/api/cluster/ble/pair/start", json={"address": "addr1"})
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_pair_confirm_requires_admin(unauthed_client):
    resp = await unauthed_client.post("/api/cluster/ble/pair/confirm", json={"session": "x"})
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_pair_cancel_requires_admin(unauthed_client):
    resp = await unauthed_client.post("/api/cluster/ble/pair/cancel", json={"session": "x"})
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_scan_non_admin_session_gets_403(client, app, monkeypatch):
    monkeypatch.setattr(app.state.auth, "session_user", lambda token, user_agent=None: {"is_admin": False})
    resp = await client.get("/api/cluster/ble/scan")
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Malformed input
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pair_start_missing_address_field_is_422(client, app):
    await app.state.cluster_pairing.init()
    resp = await client.post("/api/cluster/ble/pair/start", json={})
    assert resp.status_code == 422
    await app.state.cluster_pairing.close()


@pytest.mark.asyncio
async def test_pair_start_empty_address_is_400(client, app):
    await app.state.cluster_pairing.init()
    _wire_fake_manager(app, {})
    resp = await client.post("/api/cluster/ble/pair/start", json={"address": "  "})
    assert resp.status_code == 400
    await app.state.cluster_pairing.close()


@pytest.mark.asyncio
async def test_pair_confirm_missing_session_field_is_422(client, app):
    await app.state.cluster_pairing.init()
    resp = await client.post("/api/cluster/ble/pair/confirm", json={})
    assert resp.status_code == 422
    await app.state.cluster_pairing.close()


# ---------------------------------------------------------------------------
# Board / transport failure modes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pair_start_unknown_address_is_504(client, app):
    await app.state.cluster_pairing.init()
    _wire_fake_manager(app, {})
    resp = await client.post("/api/cluster/ble/pair/start", json={"address": "ghost"})
    assert resp.status_code == 504
    await app.state.cluster_pairing.close()


@pytest.mark.asyncio
async def test_pair_start_not_pairable_is_409(client, app):
    await app.state.cluster_pairing.init()
    board = FakeBoard(board_id="NP", state="unpaired", pairable=False)
    _wire_fake_manager(app, {"addr1": board})
    resp = await client.post("/api/cluster/ble/pair/start", json={"address": "addr1"})
    assert resp.status_code == 409
    await app.state.cluster_pairing.close()


@pytest.mark.asyncio
async def test_pair_start_already_paired_is_409(client, app):
    await app.state.cluster_pairing.init()
    board = FakeBoard(board_id="AP", state="paired")
    _wire_fake_manager(app, {"addr1": board})
    resp = await client.post("/api/cluster/ble/pair/start", json={"address": "addr1"})
    assert resp.status_code == 409
    await app.state.cluster_pairing.close()


@pytest.mark.asyncio
async def test_pair_confirm_unknown_session_is_404(client, app):
    await app.state.cluster_pairing.init()
    _wire_fake_manager(app, {})
    resp = await client.post("/api/cluster/ble/pair/confirm", json={"session": "ghost"})
    assert resp.status_code == 404
    await app.state.cluster_pairing.close()


@pytest.mark.asyncio
async def test_third_concurrent_session_is_429(client, app):
    await app.state.cluster_pairing.init()
    boards = {f"addr{i}": FakeBoard(board_id=f"C{i}") for i in range(3)}
    _wire_fake_manager(app, boards)
    r1 = await client.post("/api/cluster/ble/pair/start", json={"address": "addr0"})
    r2 = await client.post("/api/cluster/ble/pair/start", json={"address": "addr1"})
    assert r1.status_code == 200 and r2.status_code == 200
    r3 = await client.post("/api/cluster/ble/pair/start", json={"address": "addr2"})
    assert r3.status_code == 429
    await app.state.cluster_pairing.close()


@pytest.mark.asyncio
async def test_pair_confirm_twice_second_call_404s(client, app):
    await app.state.cluster_pairing.init()
    board = FakeBoard(board_id="TW2")
    _wire_fake_manager(app, {"addr1": board})
    started = await client.post("/api/cluster/ble/pair/start", json={"address": "addr1"})
    session = started.json()["session"]
    r1 = await client.post("/api/cluster/ble/pair/confirm", json={"session": session})
    assert r1.status_code == 200
    r2 = await client.post("/api/cluster/ble/pair/confirm", json={"session": session})
    assert r2.status_code == 404
    await app.state.cluster_pairing.close()


@pytest.mark.asyncio
async def test_board_rejected_provision_rolls_back_and_returns_502(client, app):
    await app.state.cluster_pairing.init()
    board = FakeBoard(board_id="REJ2")
    _wire_fake_manager(app, {"addr1": board})
    started = await client.post("/api/cluster/ble/pair/start", json={"address": "addr1"})
    assert started.status_code == 200
    session = started.json()["session"]
    board.responder.window_s = 0  # force the board to reject the provision

    resp = await client.post("/api/cluster/ble/pair/confirm", json={"session": session})
    assert resp.status_code == 502
    body = resp.json()
    assert body["error"] == "board_rejected"
    assert "why" in body

    workers = (await client.get("/api/cluster/workers")).json()
    assert not any(w["name"] == board.name for w in workers)
    await app.state.cluster_pairing.close()


@pytest.mark.asyncio
async def test_scan_unavailable_returns_503(client, app):
    """No adapter/bleak -- BlePairingManager with the real (lazy) transport
    on a box with no bleak installed answers bluetooth_unavailable."""
    await app.state.cluster_pairing.init()
    # Leave the real (lazy, bleak-backed) manager in place; this dev/CI box
    # has no bleak installed, so the first scan() call surfaces the 503.
    resp = await client.get("/api/cluster/ble/scan")
    assert resp.status_code == 503
    assert resp.json() == {"error": "bluetooth_unavailable"}
    await app.state.cluster_pairing.close()


# ---------------------------------------------------------------------------
# No response ever carries the minted key
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_response_body_ever_contains_the_minted_key_hex(client, app):
    await app.state.cluster_pairing.init()
    board = FakeBoard(board_id="SEC1")
    _wire_fake_manager(app, {"addr1": board})

    scan_resp = await client.get("/api/cluster/ble/scan")
    start_resp = await client.post("/api/cluster/ble/pair/start", json={"address": "addr1"})
    session = start_resp.json()["session"]
    confirm_resp = await client.post("/api/cluster/ble/pair/confirm", json={"session": session})
    assert confirm_resp.status_code == 200

    node_key_hex = confirm_resp.json()["node"].get("node_key")
    assert node_key_hex is None  # the route must never echo it back

    worker = app.state.cluster_manager.get_worker(board.name)
    minted_hex = worker.signing_key.hex()

    for resp in (scan_resp, start_resp, confirm_resp):
        assert minted_hex not in resp.text

    workers_resp = await client.get("/api/cluster/workers")
    assert minted_hex not in workers_resp.text
    await app.state.cluster_pairing.close()


# ---------------------------------------------------------------------------
# Happy path, through the routes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_scan_start_confirm_lists_device_node(client, app):
    await app.state.cluster_pairing.init()
    board = FakeBoard(board_id="HTTP1", name="taOSusb-HTTP1")
    _wire_fake_manager(app, {"addr1": board})

    scan_resp = await client.get("/api/cluster/ble/scan?seconds=3")
    assert scan_resp.status_code == 200
    devices = scan_resp.json()["devices"]
    assert len(devices) == 1
    assert devices[0]["board_id"] == "HTTP1"
    assert devices[0]["pairable"] is True

    start_resp = await client.post("/api/cluster/ble/pair/start", json={"address": "addr1"})
    assert start_resp.status_code == 200
    body = start_resp.json()
    assert body["board_id"] == "HTTP1"
    assert len(body["code"]) == 6

    confirm_resp = await client.post(
        "/api/cluster/ble/pair/confirm", json={"session": body["session"]}
    )
    assert confirm_resp.status_code == 200
    node = confirm_resp.json()["node"]
    assert node == {"name": "taOSusb-HTTP1", "kind": "device", "board_id": "HTTP1"}

    workers = (await client.get("/api/cluster/workers")).json()
    me = next(w for w in workers if w["name"] == "taOSusb-HTTP1")
    assert me["kind"] == "device"
    await app.state.cluster_pairing.close()


@pytest.mark.asyncio
async def test_pair_cancel_returns_204_and_drops_session(client, app):
    await app.state.cluster_pairing.init()
    board = FakeBoard(board_id="CAN1")
    _wire_fake_manager(app, {"addr1": board})
    started = await client.post("/api/cluster/ble/pair/start", json={"address": "addr1"})
    session = started.json()["session"]

    resp = await client.post("/api/cluster/ble/pair/cancel", json={"session": session})
    assert resp.status_code == 204

    confirm_resp = await client.post("/api/cluster/ble/pair/confirm", json={"session": session})
    assert confirm_resp.status_code == 404
    await app.state.cluster_pairing.close()


@pytest.mark.asyncio
async def test_pair_cancel_unknown_session_still_204s(client, app):
    await app.state.cluster_pairing.init()
    _wire_fake_manager(app, {})
    resp = await client.post("/api/cluster/ble/pair/cancel", json={"session": "ghost"})
    assert resp.status_code == 204
    await app.state.cluster_pairing.close()


# ---------------------------------------------------------------------------
# BLE label PIN (admin-typed, never board-decided)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pin_bound_board_right_pin_pairs(client, app):
    """A board advertising pin-bound pairs when the admin supplies the PIN it
    typed; the PIN never appears in any response and "pin-bound" never reaches
    the registered device capabilities."""
    await app.state.cluster_pairing.init()
    board = FakeBoard(board_id="PINOK", label_pin="123456")
    _wire_fake_manager(app, {"addr1": board})

    start_resp = await client.post("/api/cluster/ble/pair/start", json={"address": "addr1"})
    assert start_resp.status_code == 200
    start_body = start_resp.json()
    assert start_body["pin_required"] is True
    assert "123456" not in start_resp.text

    confirm_resp = await client.post(
        "/api/cluster/ble/pair/confirm",
        json={"session": start_body["session"], "label_pin": "123456"},
    )
    assert confirm_resp.status_code == 200
    assert "123456" not in confirm_resp.text
    node = confirm_resp.json()["node"]
    assert node["board_id"] == "PINOK"

    workers = (await client.get("/api/cluster/workers")).json()
    me = next(w for w in workers if w["name"] == board.name)
    assert "pin-bound" not in me["capabilities"]
    await app.state.cluster_pairing.close()


@pytest.mark.asyncio
async def test_pin_bound_board_wrong_pin_rolls_back(client, app):
    """A wrong PIN derives a different session key; the board rejects the
    provision and the minted credential is rolled back."""
    await app.state.cluster_pairing.init()
    board = FakeBoard(board_id="PINWRONG", label_pin="123456")
    _wire_fake_manager(app, {"addr1": board})

    started = await client.post("/api/cluster/ble/pair/start", json={"address": "addr1"})
    assert started.status_code == 200
    session = started.json()["session"]

    resp = await client.post(
        "/api/cluster/ble/pair/confirm",
        json={"session": session, "label_pin": "654321"},
    )
    assert resp.status_code in (502, 504)

    workers = (await client.get("/api/cluster/workers")).json()
    assert not any(w["name"] == board.name for w in workers)
    assert await app.state.cluster_pairing.get_signing_key(board.name) is None
    await app.state.cluster_pairing.close()


@pytest.mark.asyncio
async def test_pin_bound_board_missing_pin_is_400_and_session_survives(client, app):
    """A pin-bound board asked to confirm without a PIN returns 400 and keeps
    the session alive so the admin can retry with the PIN."""
    await app.state.cluster_pairing.init()
    board = FakeBoard(board_id="PINREQ", label_pin="123456")
    _wire_fake_manager(app, {"addr1": board})

    started = await client.post("/api/cluster/ble/pair/start", json={"address": "addr1"})
    assert started.status_code == 200
    session = started.json()["session"]

    resp = await client.post("/api/cluster/ble/pair/confirm", json={"session": session})
    assert resp.status_code == 400
    assert resp.json() == {"error": "label_pin_required"}

    resp2 = await client.post(
        "/api/cluster/ble/pair/confirm",
        json={"session": session, "label_pin": "123456"},
    )
    assert resp2.status_code == 200
    await app.state.cluster_pairing.close()


@pytest.mark.asyncio
async def test_admin_pin_binds_even_when_board_omits_cap(client, app):
    """Binding is decided by the admin's input, not the board's claim: a board
    with no pin-bound cap is still bound when the admin types a PIN, and since it
    does not expect one the provision is rejected and rolled back."""
    await app.state.cluster_pairing.init()
    board = FakeBoard(board_id="ADMINBIN")
    _wire_fake_manager(app, {"addr1": board})

    started = await client.post("/api/cluster/ble/pair/start", json={"address": "addr1"})
    assert started.status_code == 200
    session = started.json()["session"]

    resp = await client.post(
        "/api/cluster/ble/pair/confirm",
        json={"session": session, "label_pin": "123456"},
    )
    assert resp.status_code != 200

    workers = (await client.get("/api/cluster/workers")).json()
    assert not any(w["name"] == board.name for w in workers)
    await app.state.cluster_pairing.close()


@pytest.mark.asyncio
async def test_invalid_label_pin_is_400(client, app):
    """A PIN with non-digit characters is rejected at the route layer."""
    await app.state.cluster_pairing.init()
    board = FakeBoard(board_id="BADPIN")
    _wire_fake_manager(app, {"addr1": board})

    started = await client.post("/api/cluster/ble/pair/start", json={"address": "addr1"})
    assert started.status_code == 200
    session = started.json()["session"]

    resp = await client.post(
        "/api/cluster/ble/pair/confirm",
        json={"session": session, "label_pin": "12ab"},
    )
    assert resp.status_code == 400
    assert resp.json() == {"error": "invalid_label_pin"}
    await app.state.cluster_pairing.close()

"""A paired device (kind="device", a taOSusb board) can never promote itself
to a worker through its own signed register/heartbeat requests.

The board holds its node_key after pairing, so it can sign
POST /api/cluster/workers under its own name. That route built WorkerInfo
without a kind (default "worker") and the upsert overwrote the stored
kind, so the board then passed every device exclusion and was routed chat
jobs. These drive the real routes: pair over the BLE routes (FakeTransport
backed by a real PairResponder), then act as the board with the key the
board itself unsealed.
"""
from __future__ import annotations

import json

import pytest

import tinyagentos.cluster.ble.pairing as pairing_mod
from tinyagentos.browser_sessions import _capable_workers
from tinyagentos.cluster.ble.pairing import BlePairingManager
from cluster.conftest import FakeBoard, FakeTransport
from test_routes_cluster import pair_worker, sign_worker_request


@pytest.fixture(autouse=True)
def fast_timeouts(monkeypatch):
    monkeypatch.setattr(pairing_mod, "_HELLO_TIMEOUT_S", 0.3)
    monkeypatch.setattr(pairing_mod, "_PROVISION_TIMEOUT_S", 0.3)
    monkeypatch.setattr(pairing_mod, "_CONNECT_TIMEOUT_S", 0.3)


def _wire(app, boards):
    app.state.ble_pairing = BlePairingManager(
        data_dir=app.state.data_dir,
        cluster_manager=app.state.cluster_manager,
        pairing_store=app.state.cluster_pairing,
        bind_port=6969,
        transport=FakeTransport(boards),
    )


async def _pair_board(client, app, board_id="DV01") -> tuple[str, bytes]:
    board = FakeBoard(board_id=board_id, name=f"taOSusb-{board_id}")
    _wire(app, {"addr": board})
    started = await client.post("/api/cluster/ble/pair/start", json={"address": "addr"})
    assert started.status_code == 200, started.text
    confirmed = await client.post("/api/cluster/ble/pair/confirm",
                                  json={"session": started.json()["session"]})
    assert confirmed.status_code == 200, confirmed.text
    return board.name, bytes.fromhex(board.responder.provisioned["node_key"])


async def _signed_post(client, key, name, path, payload):
    body = json.dumps(payload).encode()
    headers = sign_worker_request(key, name, "POST", path, body)
    return await client.post(path, content=body,
                             headers={**headers, "content-type": "application/json"})


def _as_worker(name):
    return {
        "name": name,
        "url": "http://10.66.66.66:6970",
        "platform": "linux",
        "capabilities": ["chat", "embed", "image-generation", "browser"],
        "hardware": {"ram_mb": 65536, "cpu": {"cores": 32},
                     "gpu": {"cuda": True, "vram_mb": 49152}},
    }


def _assert_never_a_job_candidate(cluster, name):
    for cap in ("chat", "embed", "image-generation"):
        assert name not in [w.name for w in cluster.get_workers_for_capability(cap)], cap
    assert name not in [w.name for w in _capable_workers(cluster, 0, 0)]


@pytest.mark.asyncio
async def test_device_register_and_heartbeat_as_worker_stays_device(client, app):
    await app.state.cluster_pairing.init()
    cluster = app.state.cluster_manager
    await cluster._registry_store.init()  # noqa: SLF001 -- persist for real
    name, key = await _pair_board(client, app)
    assert cluster.get_worker(name).kind == "device"

    resp = await _signed_post(client, key, name, "/api/cluster/workers", _as_worker(name))
    assert resp.status_code in (200, 403), resp.text
    w = cluster.get_worker(name)
    assert w is not None and w.kind == "device"
    _assert_never_a_job_candidate(cluster, name)

    hb = await _signed_post(client, key, name, "/api/cluster/heartbeat",
                            {"name": name, "load": 0.0,
                             "capabilities": ["chat", "embed", "image-generation", "browser"]})
    assert hb.status_code in (200, 403, 404), hb.text
    assert cluster.get_worker(name).kind == "device"
    _assert_never_a_job_candidate(cluster, name)

    # And the persisted registry row did not flip either.
    rows = {r["name"]: r for r in await cluster._registry_store.load_all()}  # noqa: SLF001
    assert rows[name]["kind"] == "device"
    await cluster._registry_store.close()  # noqa: SLF001
    await app.state.cluster_pairing.close()


@pytest.mark.asyncio
async def test_device_removed_from_registry_cannot_come_back_as_worker(client, app):
    """An admin removes the device's registry row (DELETE), but its node key is
    still in the pairing store. Re-registering with that key must not produce
    a kind=worker node: the kind is bound to the key at issuance."""
    await app.state.cluster_pairing.init()
    cluster = app.state.cluster_manager
    name, key = await _pair_board(client, app, board_id="DV02")
    resp = await client.delete(f"/api/cluster/workers/{name}")
    assert resp.status_code == 200, resp.text
    assert cluster.get_worker(name) is None

    resp = await _signed_post(client, key, name, "/api/cluster/workers", _as_worker(name))
    assert resp.status_code in (200, 403), resp.text
    w = cluster.get_worker(name)
    assert w is None or w.kind == "device"
    _assert_never_a_job_candidate(cluster, name)
    await app.state.cluster_pairing.close()


@pytest.mark.asyncio
async def test_genuine_worker_still_registers_as_worker(client, app):
    """Control: a worker paired through the worker flow registers as kind=worker
    and IS a chat candidate, so the device assertions above are the kind lock,
    not a broken register route."""
    await app.state.cluster_pairing.init()
    cluster = app.state.cluster_manager
    key = await pair_worker(client, app, "real-gpu", "http://10.0.0.9:6970")
    resp = await _signed_post(client, key, "real-gpu", "/api/cluster/workers", _as_worker("real-gpu"))
    assert resp.status_code == 200, resp.text
    assert cluster.get_worker("real-gpu").kind == "worker"
    assert "real-gpu" in [w.name for w in cluster.get_workers_for_capability("chat")]
    await app.state.cluster_pairing.close()


@pytest.mark.asyncio
async def test_ble_confirm_over_existing_worker_name_is_409(client, app):
    """M2 through the route: a board advertising a registered worker's name gets
    409 from /pair/confirm and the worker keeps its key and kind."""
    await app.state.cluster_pairing.init()
    cluster = app.state.cluster_manager
    key = await pair_worker(client, app, "gpu-box", "http://10.0.0.7:6970")
    resp = await _signed_post(client, key, "gpu-box", "/api/cluster/workers", _as_worker("gpu-box"))
    assert resp.status_code == 200, resp.text

    board = FakeBoard(board_id="7K3Q", name="gpu-box")
    _wire(app, {"addr": board})
    started = await client.post("/api/cluster/ble/pair/start", json={"address": "addr"})
    assert started.status_code == 200
    confirmed = await client.post("/api/cluster/ble/pair/confirm",
                                  json={"session": started.json()["session"]})
    assert confirmed.status_code == 409, confirmed.text
    assert await app.state.cluster_pairing.get_signing_key("gpu-box") == key
    assert cluster.get_worker("gpu-box").kind == "worker"
    await app.state.cluster_pairing.close()

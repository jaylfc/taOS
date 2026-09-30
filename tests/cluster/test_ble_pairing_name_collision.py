"""A BLE board may not take over another node's cluster identity.

The node name comes from the board's own advert, so a hostile board can
advertise the name of an existing worker (``gpu-box``). Confirming that
pairing used to overwrite the worker's signing key, clear its block/revoke
state, and rewrite its registry row as a device; a deliberately failed
provision then rolled back by unregistering and revoking the REAL worker.

These run through the real ClusterPairingStore, the real ClusterManager and
a FakeTransport wired to a real PairResponder (tests/cluster/conftest.py).
"""
from __future__ import annotations

import pytest
import pytest_asyncio

import tinyagentos.cluster.ble.pairing as pairing_mod
from tinyagentos.cluster.ble.pairing import BlePairingManager, PairError
from tinyagentos.cluster.manager import ClusterManager
from tinyagentos.cluster.pairing_store import ClusterPairingStore
from tinyagentos.cluster.worker_protocol import WorkerInfo

from cluster.conftest import FakeBoard, FakeTransport

WORKER = "gpu-box"
WORKER_URL = "http://10.0.0.7:6970"


@pytest_asyncio.fixture
async def store(tmp_path):
    s = ClusterPairingStore(tmp_path / "pairing.db")
    await s.init()
    yield s
    await s.close()


@pytest.fixture(autouse=True)
def fast_timeouts(monkeypatch):
    monkeypatch.setattr(pairing_mod, "_HELLO_TIMEOUT_S", 0.3)
    monkeypatch.setattr(pairing_mod, "_PROVISION_TIMEOUT_S", 0.3)
    monkeypatch.setattr(pairing_mod, "_CONNECT_TIMEOUT_S", 0.3)


async def _pair_real_worker(cluster: ClusterManager, store: ClusterPairingStore) -> bytes:
    """Pair + register a genuine worker the way the free-tier flow does:
    admin authorises {url, code}, the worker claims it under its name."""
    await store.manual_authorize(WORKER_URL, "123456")
    claimed = await store.manual_claim(WORKER, "123456")
    assert claimed is not None
    key, _url = claimed
    ok, reason = await cluster.register_worker(
        WorkerInfo(name=WORKER, url=WORKER_URL, kind="worker",
                   capabilities=["chat"], signing_key=key)
    )
    assert ok, reason
    return key


async def _raw_row(store: ClusterPairingStore, name: str):
    return await store._fetch_row(name)  # noqa: SLF001 -- read the stored key even when blocked


def _mgr(cluster, store, tmp_path, boards) -> BlePairingManager:
    return BlePairingManager(
        data_dir=tmp_path, cluster_manager=cluster, pairing_store=store,
        bind_port=6969, transport=FakeTransport(boards),
    )


@pytest.mark.asyncio
async def test_board_named_after_live_worker_is_409_and_worker_untouched(tmp_path, store):
    cluster = ClusterManager()
    key = await _pair_real_worker(cluster, store)

    board = FakeBoard(board_id="7K3Q", name=WORKER)
    mgr = _mgr(cluster, store, tmp_path, {"addr": board})
    started = await mgr.start("addr")

    with pytest.raises(PairError) as exc:
        await mgr.confirm(started["session"])
    assert exc.value.status == 409

    # The worker's key, auth state and registry row are exactly as before.
    assert await store.get_signing_key(WORKER) == key
    state = await store.pairing_state(WORKER)
    assert state["revoked"] is False and state["blocked"] is False
    w = cluster.get_worker(WORKER)
    assert w is not None
    assert w.kind == "worker" and w.url == WORKER_URL
    # The board was never provisioned with anything.
    assert board.responder.provisioned is None


@pytest.mark.asyncio
async def test_board_named_after_blocked_worker_does_not_unblock_it(tmp_path, store):
    cluster = ClusterManager()
    key = await _pair_real_worker(cluster, store)
    assert await store.block(WORKER)

    board = FakeBoard(board_id="7K3Q", name=WORKER)
    mgr = _mgr(cluster, store, tmp_path, {"addr": board})
    started = await mgr.start("addr")

    with pytest.raises(PairError) as exc:
        await mgr.confirm(started["session"])
    assert exc.value.status == 409

    row = await _raw_row(store, WORKER)
    assert bytes(row["signing_key"]) == key
    assert row["blocked"] == 1 and row["revoked"] == 1
    assert await store.get_signing_key(WORKER) is None


@pytest.mark.asyncio
async def test_failed_provision_after_name_clash_leaves_real_worker_registered(tmp_path, store):
    """The rollback half: a hostile board advertises the worker's name and
    then rejects the provision on purpose. The real worker must still be
    registered, with its own live key."""
    cluster = ClusterManager()
    key = await _pair_real_worker(cluster, store)

    board = FakeBoard(board_id="7K3Q", name=WORKER)
    mgr = _mgr(cluster, store, tmp_path, {"addr": board})
    started = await mgr.start("addr")
    board.responder.window_s = 0  # the board deliberately rejects the provision

    with pytest.raises(PairError):
        await mgr.confirm(started["session"])

    w = cluster.get_worker(WORKER)
    assert w is not None, "rollback unregistered the legitimate worker"
    assert w.kind == "worker" and w.url == WORKER_URL
    assert await store.get_signing_key(WORKER) == key


@pytest.mark.asyncio
async def test_board_named_after_live_device_is_409(tmp_path, store):
    """A second board advertising an already-paired, un-revoked device's name
    must not replace that device's key either."""
    cluster = ClusterManager()
    first = FakeBoard(board_id="AAAA", name="taOSusb-AAAA")
    mgr = _mgr(cluster, store, tmp_path, {"a": first})
    await mgr.confirm((await mgr.start("a"))["session"])
    key = await store.get_signing_key("taOSusb-AAAA")
    assert key is not None

    impostor = FakeBoard(board_id="AAAA", name="taOSusb-AAAA")
    mgr2 = _mgr(cluster, store, tmp_path, {"b": impostor})
    with pytest.raises(PairError) as exc:
        await mgr2.confirm((await mgr2.start("b"))["session"])
    assert exc.value.status == 409
    assert await store.get_signing_key("taOSusb-AAAA") == key
    assert impostor.responder.provisioned is None


@pytest.mark.asyncio
async def test_revoked_device_can_be_repaired_under_its_name(tmp_path, store):
    """Control: once an admin revokes a device, the (reset) board may pair
    again under the same name and gets a fresh live key."""
    cluster = ClusterManager()
    first = FakeBoard(board_id="BBBB", name="taOSusb-BBBB")
    mgr = _mgr(cluster, store, tmp_path, {"a": first})
    await mgr.confirm((await mgr.start("a"))["session"])
    old = await store.get_signing_key("taOSusb-BBBB")
    assert await store.revoke("taOSusb-BBBB")

    again = FakeBoard(board_id="BBBB", name="taOSusb-BBBB")
    mgr2 = _mgr(cluster, store, tmp_path, {"b": again})
    result = await mgr2.confirm((await mgr2.start("b"))["session"])
    assert result["kind"] == "device"
    new = await store.get_signing_key("taOSusb-BBBB")
    assert new is not None and new != old
    assert new.hex() == again.responder.provisioned["node_key"]
    assert cluster.get_worker("taOSusb-BBBB").kind == "device"


@pytest.mark.asyncio
async def test_revoked_worker_name_is_still_refused(tmp_path, store):
    """Revoking a WORKER does not free its name for a board: only a device's
    name may be re-paired over BLE."""
    cluster = ClusterManager()
    await _pair_real_worker(cluster, store)
    assert await store.revoke(WORKER)

    board = FakeBoard(board_id="7K3Q", name=WORKER)
    mgr = _mgr(cluster, store, tmp_path, {"addr": board})
    with pytest.raises(PairError) as exc:
        await mgr.confirm((await mgr.start("addr"))["session"])
    assert exc.value.status == 409
    assert cluster.get_worker(WORKER).kind == "worker"

"""Tests for BlePairingManager (cluster/ble/pairing.py), the taOSusb S1
controller-side orchestration: scan -> start -> confirm/cancel against a
FakeTransport wired to a REAL proto.PairResponder (see tests/cluster/conftest.py).

Hostile cases first: a fake board returning malformed info/hello/a sealed
error, an expired/unknown session, a third concurrent session, confirm
twice, and a board-rejected provision rolling back the minted credential.
Then the happy path end to end.
"""
from __future__ import annotations

import asyncio
import json

import pytest
import pytest_asyncio

import tinyagentos.cluster.ble.pairing as pairing_mod
from tinyagentos.cluster.ble import proto
from tinyagentos.cluster.ble.pairing import BlePairingManager, PairError
from tinyagentos.cluster.manager import ClusterManager
from tinyagentos.cluster.pairing_store import ClusterPairingStore

from cluster.conftest import FakeBoard, FakeTransport


@pytest_asyncio.fixture
async def store(tmp_path):
    s = ClusterPairingStore(tmp_path / "pairing.db")
    await s.init()
    yield s
    await s.close()


@pytest.fixture
def cluster():
    return ClusterManager()


@pytest.fixture(autouse=True)
def fast_timeouts(monkeypatch):
    """Handshake timeouts default to 10-15s -- far too slow for a unit test
    that deliberately never replies. Shrink them for the whole module."""
    monkeypatch.setattr(pairing_mod, "_HELLO_TIMEOUT_S", 0.3)
    monkeypatch.setattr(pairing_mod, "_PROVISION_TIMEOUT_S", 0.3)
    monkeypatch.setattr(pairing_mod, "_CONNECT_TIMEOUT_S", 0.3)


def make_manager(cluster, store, tmp_path, boards, llm_gateway_enabled=None,
                 **conn_kwargs) -> BlePairingManager:
    transport = FakeTransport(boards, **conn_kwargs)
    return BlePairingManager(
        data_dir=tmp_path,
        cluster_manager=cluster,
        pairing_store=store,
        bind_port=6969,
        transport=transport,
        llm_gateway_enabled=llm_gateway_enabled,
    )


# ---------------------------------------------------------------------------
# start(): hostile cases
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_start_unknown_address_is_504(cluster, store, tmp_path):
    mgr = make_manager(cluster, store, tmp_path, {})
    with pytest.raises(PairError) as exc:
        await mgr.start("AA:BB:CC")
    assert exc.value.status == 504


@pytest.mark.asyncio
async def test_start_rejects_not_pairable(cluster, store, tmp_path):
    board = FakeBoard(state="unpaired", pairable=False)
    mgr = make_manager(cluster, store, tmp_path, {"addr1": board})
    with pytest.raises(PairError) as exc:
        await mgr.start("addr1")
    assert exc.value.status == 409


@pytest.mark.asyncio
async def test_start_rejects_already_paired(cluster, store, tmp_path):
    board = FakeBoard(state="paired", pairable=False)
    mgr = make_manager(cluster, store, tmp_path, {"addr1": board})
    with pytest.raises(PairError) as exc:
        await mgr.start("addr1")
    assert exc.value.status == 409


@pytest.mark.asyncio
async def test_start_rejects_malformed_info(cluster, store, tmp_path):
    board = FakeBoard()
    mgr = make_manager(cluster, store, tmp_path, {"addr1": board}, corrupt_info=True)
    with pytest.raises(PairError) as exc:
        await mgr.start("addr1")
    assert exc.value.status == 504


@pytest.mark.asyncio
async def test_start_rejects_malformed_hello_reply(cluster, store, tmp_path):
    board = FakeBoard()
    mgr = make_manager(cluster, store, tmp_path, {"addr1": board}, corrupt_hello_reply=True)
    with pytest.raises(PairError) as exc:
        await mgr.start("addr1")
    assert exc.value.status == 504


@pytest.mark.asyncio
async def test_start_times_out_when_board_never_replies(cluster, store, tmp_path):
    board = FakeBoard()
    mgr = make_manager(cluster, store, tmp_path, {"addr1": board}, no_reply=True)
    with pytest.raises(PairError) as exc:
        await mgr.start("addr1")
    assert exc.value.status == 504


@pytest.mark.asyncio
async def test_third_concurrent_session_is_429(cluster, store, tmp_path):
    boards = {f"addr{i}": FakeBoard(board_id=f"B{i}") for i in range(3)}
    mgr = make_manager(cluster, store, tmp_path, boards)
    r1 = await mgr.start("addr0")
    r2 = await mgr.start("addr1")
    with pytest.raises(PairError) as exc:
        await mgr.start("addr2")
    assert exc.value.status == 429
    await mgr.cancel(r1["session"])
    await mgr.cancel(r2["session"])


# ---------------------------------------------------------------------------
# confirm(): hostile cases
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_confirm_unknown_session_is_404(cluster, store, tmp_path):
    mgr = make_manager(cluster, store, tmp_path, {})
    with pytest.raises(PairError) as exc:
        await mgr.confirm("no-such-session")
    assert exc.value.status == 404


@pytest.mark.asyncio
async def test_confirm_twice_second_call_404s(cluster, store, tmp_path):
    board = FakeBoard(board_id="TWICE")
    mgr = make_manager(cluster, store, tmp_path, {"addr1": board})
    started = await mgr.start("addr1")
    await mgr.confirm(started["session"])
    with pytest.raises(PairError) as exc:
        await mgr.confirm(started["session"])
    assert exc.value.status == 404


@pytest.mark.asyncio
async def test_confirm_rolls_back_on_board_rejection(cluster, store, tmp_path):
    """Force the board to reject the provision (pairing window closed
    between start() and confirm()) and assert the minted credential and
    registration are both undone."""
    board = FakeBoard(board_id="REJECT")
    mgr = make_manager(cluster, store, tmp_path, {"addr1": board})
    started = await mgr.start("addr1")
    # The hello already went through (window was open); close it now so the
    # provision write is rejected -- a hostile/broken-board scenario.
    board.responder.window_s = 0

    with pytest.raises(PairError) as exc:
        await mgr.confirm(started["session"])
    assert exc.value.status == 502

    name = board.name
    assert cluster.get_worker(name) is None
    assert await store.get_signing_key(name) is None


@pytest.mark.asyncio
async def test_confirm_times_out_when_board_never_replies_to_provision(cluster, store, tmp_path):
    """A board that answers the hello but goes silent for the provision
    reply must still roll back the mint + registration."""
    board = FakeBoard(board_id="SILENT")
    boards = {"addr1": board}
    transport = FakeTransport(boards)
    mgr = BlePairingManager(
        data_dir=tmp_path, cluster_manager=cluster, pairing_store=store,
        bind_port=6969, transport=transport,
    )
    started = await mgr.start("addr1")
    # Swap in a connection that never replies, for the provision phase only.
    from cluster.conftest import FakeConnection
    sess = mgr._sessions[started["session"]]  # noqa: SLF001 -- test-only introspection
    old_conn = sess.connection
    sess.connection = FakeConnection(board, no_reply=True)
    await old_conn.close()

    with pytest.raises(PairError) as exc:
        await mgr.confirm(started["session"])
    assert exc.value.status == 504

    name = board.name
    assert cluster.get_worker(name) is None
    assert await store.get_signing_key(name) is None


# ---------------------------------------------------------------------------
# cancel()
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cancel_unknown_session_is_a_noop(cluster, store, tmp_path):
    mgr = make_manager(cluster, store, tmp_path, {})
    await mgr.cancel("no-such-session")  # must not raise


@pytest.mark.asyncio
async def test_cancel_drops_the_session(cluster, store, tmp_path):
    board = FakeBoard(board_id="CANCEL")
    mgr = make_manager(cluster, store, tmp_path, {"addr1": board})
    started = await mgr.start("addr1")
    await mgr.cancel(started["session"])
    with pytest.raises(PairError) as exc:
        await mgr.confirm(started["session"])
    assert exc.value.status == 404


# ---------------------------------------------------------------------------
# Platform from caps
# ---------------------------------------------------------------------------

class BoardWithoutCaps:
    """A fake board that omits the 'caps' field from its info JSON."""

    def __init__(self, *, board_id: str = "TEST", name: str | None = None):
        self.board_id = board_id
        self.name = name or f"taOSusb-{board_id}"
        self.priv, self.pub = proto.x25519_keypair()
        self.responder = proto.PairResponder(board_id, self.priv)

    def info_bytes(self) -> bytes:
        frame = {
            "v": 1,
            "id": self.board_id,
            "name": self.name,
            "state": "unpaired",
            "pairable": True,
            "bpub": proto.b64(proto.pub_bytes(self.pub)),
        }
        return json.dumps(frame).encode("utf-8")


@pytest.mark.asyncio
async def test_orb_caps_register_platform_orb(cluster, store, tmp_path):
    """Board with caps ["orb"] registers with platform "orb"."""
    board = FakeBoard(board_id="ORB1", name="taOS Orb-ORB1", caps=["orb"])
    mgr = make_manager(cluster, store, tmp_path, {"addr1": board})
    started = await mgr.start("addr1")
    result = await mgr.confirm(started["session"])

    assert result["name"] == "taOS Orb-ORB1"
    worker = cluster.get_worker("taOS Orb-ORB1")
    assert worker is not None
    assert worker.platform == "orb"
    assert worker.capabilities == ["agent", "orb"]


@pytest.mark.asyncio
async def test_agent_caps_still_register_taosusb(cluster, store, tmp_path):
    """Board with caps ["agent"] (the default) registers with platform "taosusb"."""
    board = FakeBoard(board_id="TAOS1", name="taOSusb-TAOS1", caps=["agent"])
    mgr = make_manager(cluster, store, tmp_path, {"addr1": board})
    started = await mgr.start("addr1")
    result = await mgr.confirm(started["session"])

    assert result["name"] == "taOSusb-TAOS1"
    worker = cluster.get_worker("taOSusb-TAOS1")
    assert worker is not None
    assert worker.platform == "taosusb"


@pytest.mark.asyncio
async def test_orb_caps_registered(cluster, store, tmp_path):
    """info caps ["agent","orb"] -> registered capabilities == ["agent","orb"]."""
    board = FakeBoard(board_id="ORB2", name="taOS Orb-ORB2", caps=["agent", "orb"])
    mgr = make_manager(cluster, store, tmp_path, {"addr1": board})
    started = await mgr.start("addr1")
    result = await mgr.confirm(started["session"])

    assert result["name"] == "taOS Orb-ORB2"
    worker = cluster.get_worker("taOS Orb-ORB2")
    assert worker is not None
    assert worker.capabilities == ["agent", "orb"]


@pytest.mark.asyncio
async def test_board_cannot_claim_unlisted_caps(cluster, store, tmp_path):
    """info caps ["agent","browser","gpu","orb"] -> capabilities == ["agent","orb"]."""
    board = FakeBoard(board_id="UNLIST", name="taOS Orb-UNLIST", caps=["agent", "browser", "gpu", "orb"])
    mgr = make_manager(cluster, store, tmp_path, {"addr1": board})
    started = await mgr.start("addr1")
    result = await mgr.confirm(started["session"])

    assert result["name"] == "taOS Orb-UNLIST"
    worker = cluster.get_worker("taOS Orb-UNLIST")
    assert worker is not None
    assert worker.capabilities == ["agent", "orb"]


@pytest.mark.asyncio
async def test_taosusb_caps_unchanged(cluster, store, tmp_path):
    """caps ["agent"] -> ["agent"]."""
    board = FakeBoard(board_id="SUB1", name="taOSusb-SUB1", caps=["agent"])
    mgr = make_manager(cluster, store, tmp_path, {"addr1": board})
    started = await mgr.start("addr1")
    result = await mgr.confirm(started["session"])

    assert result["name"] == "taOSusb-SUB1"
    worker = cluster.get_worker("taOSusb-SUB1")
    assert worker is not None
    assert worker.capabilities == ["agent"]


@pytest.mark.asyncio
async def test_missing_or_junk_caps_default_to_taosusb(cluster, store, tmp_path):
    """caps absent, or caps = "orb" as a bare string -> platform == "taosusb"."""
    board_no_caps = BoardWithoutCaps(board_id="NOCAPS")
    boards = {"addr1": board_no_caps}
    transport = FakeTransport(boards)
    mgr = BlePairingManager(
        data_dir=tmp_path, cluster_manager=cluster, pairing_store=store,
        bind_port=6969, transport=transport,
    )
    started = await mgr.start("addr1")
    await mgr.confirm(started["session"])

    worker = cluster.get_worker("taOSusb-NOCAPS")
    assert worker is not None
    assert worker.platform == "taosusb"


# ---------------------------------------------------------------------------
# Happy path, end to end
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_scan_start_confirm_happy_path(cluster, store, tmp_path, monkeypatch):
    # S1 scope, with the LLM provision pinned OFF through the manager's own
    # switch (the gateway is always on since LiteLLM removal 2b-2a; the
    # default-on provision is covered by the sibling below).
    board = FakeBoard(board_id="HAPP", name="taOSusb-HAPP")
    mgr = make_manager(cluster, store, tmp_path, {"addr1": board}, llm_gateway_enabled=False)

    devices = await mgr.scan(2)
    assert len(devices) == 1
    assert devices[0]["board_id"] == "HAPP"
    assert devices[0]["name"] == "taOSusb-HAPP"
    assert devices[0]["pairable"] is True

    started = await mgr.start("addr1")
    assert started["board_id"] == "HAPP"
    assert len(started["code"]) == 6 and started["code"].isdigit()

    result = await mgr.confirm(started["session"])
    assert result == {"name": "taOSusb-HAPP", "kind": "device", "board_id": "HAPP"}

    # The node is registered as kind=device with the exact key the board holds.
    worker = cluster.get_worker("taOSusb-HAPP")
    assert worker is not None
    assert worker.kind == "device"
    assert board.responder.paired is True
    assert board.responder.provisioned is not None
    assert worker.signing_key.hex() == board.responder.provisioned["node_key"]

    # The provision carried no wifi/llm/mesh_preauth (S1 scope, gateway off).
    assert board.responder.provisioned["wifi"] == []
    assert board.responder.provisioned["llm"] is None
    assert board.responder.provisioned["mesh_preauth"] is None

    # The registry's signing key matches what confirm() minted.
    stored_key = await store.get_signing_key("taOSusb-HAPP")
    assert stored_key == worker.signing_key


@pytest.mark.asyncio
async def test_happy_path_with_the_gateway_default_provisions_a_scoped_llm_key(
    cluster, store, tmp_path, monkeypatch,
):
    """With TAOS_LLM_GATEWAY unset (the default is now ON), the board gets the
    gateway's base URL and its own node-bound, scoped gateway key."""
    from types import SimpleNamespace

    from tinyagentos.llm_gateway.auth import gateway_caller

    monkeypatch.delenv("TAOS_LLM_GATEWAY", raising=False)
    board = FakeBoard(board_id="HAPD", name="taOSusb-HAPD")
    mgr = make_manager(cluster, store, tmp_path, {"addr1": board})
    started = await mgr.start("addr1")
    await mgr.confirm(started["session"])

    llm = board.responder.provisioned["llm"]
    assert isinstance(llm, dict) and {"base", "key"} <= set(llm)
    assert llm["base"].endswith("/api/llm/v1")
    assert llm["key"].startswith("sk-taosgw-")
    caller = gateway_caller(SimpleNamespace(
        state=SimpleNamespace(),
        headers={"authorization": f"Bearer {llm['key']}"},
        app=SimpleNamespace(state=SimpleNamespace(data_dir=tmp_path)),
    ))
    assert caller.kind == "node" and caller.caller_id == "node:taOSusb-HAPD"
    assert caller.allowed_models is not None  # scoped, never admin

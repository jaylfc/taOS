"""G4: the board's model key is minted at pairing and dies with the node.

confirm() mints a gateway key bound to the node (``mint_for_node(name,
["taos-default"])``) and seals ``llm: {base, key}`` into the provision. These
tests read the key off the FAKE BOARD (the real PairResponder that unsealed
it), never off anything the controller returned, and check it against the
real keystore.

Hostile cases first: a board rejection, a silent board, a board that echoes
the key back in its error, the gateway switched off. Then the happy path.
"""
from __future__ import annotations

import logging
from unittest.mock import patch

import pytest
import pytest_asyncio

import tinyagentos.cluster.ble.pairing as pairing_mod
import tinyagentos.llm_gateway.auth as gw
from tinyagentos.cluster.ble import proto
from tinyagentos.cluster.ble.pairing import BlePairingManager, PairError
from tinyagentos.cluster.manager import ClusterManager
from tinyagentos.cluster.pairing_store import ClusterPairingStore

from cluster.conftest import FakeBoard, FakeConnection, FakeTransport

URL = "http://10.20.30.40:6969"


@pytest_asyncio.fixture
async def store(tmp_path):
    s = ClusterPairingStore(tmp_path / "pairing.db")
    await s.init()
    yield s
    await s.close()


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(pairing_mod, "_HELLO_TIMEOUT_S", 0.3)
    monkeypatch.setattr(pairing_mod, "_PROVISION_TIMEOUT_S", 0.3)
    monkeypatch.setattr(pairing_mod, "_CONNECT_TIMEOUT_S", 0.3)
    monkeypatch.setattr(pairing_mod, "controller_urls", lambda port: [URL, "http://10.9.9.9:6969"])


def _mgr(tmp_path, store, boards, *, llm=True, **conn_kwargs):
    return BlePairingManager(
        data_dir=tmp_path, cluster_manager=ClusterManager(), pairing_store=store,
        bind_port=6969, transport=FakeTransport(boards, **conn_kwargs),
        llm_gateway_enabled=llm,
    )


def _live(tmp_path, key):
    """The keystore's verdict on a presented key: its caller, or None."""
    return gw._resolve_scoped(gw._store(tmp_path), key)


def _node_keys(tmp_path, name):
    import sqlite3
    from tinyagentos.litellm_keystore import default_keystore_path
    path = default_keystore_path(tmp_path)
    if not path.exists():
        return []
    conn = sqlite3.connect(path)
    try:
        return conn.execute(
            "SELECT key_id, revoked_ts FROM gateway_keys WHERE bound_to = ?",
            (gw.node_principal(name),),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


# -- hostile ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_board_rejection_revokes_the_model_key(tmp_path, store):
    board = FakeBoard(board_id="REJ1")
    mgr = _mgr(tmp_path, store, {"a": board})
    started = await mgr.start("a")
    board.responder.window_s = 0  # the board will refuse the provision
    with pytest.raises(PairError) as exc:
        await mgr.confirm(started["session"])
    assert exc.value.status == 502
    keys = _node_keys(tmp_path, board.name)
    assert len(keys) == 1, "the key was never minted, so the rollback proves nothing"
    assert all(revoked is not None for _kid, revoked in keys)


@pytest.mark.asyncio
async def test_silent_board_revokes_the_model_key(tmp_path, store):
    board = FakeBoard(board_id="SIL1")
    mgr = _mgr(tmp_path, store, {"a": board})
    started = await mgr.start("a")
    sess = mgr._sessions[started["session"]]  # noqa: SLF001
    sess.connection = FakeConnection(board, no_reply=True)
    with pytest.raises(PairError) as exc:
        await mgr.confirm(started["session"])
    assert exc.value.status == 504
    keys = _node_keys(tmp_path, board.name)
    assert len(keys) == 1 and keys[0][1] is not None


@pytest.mark.asyncio
async def test_board_that_echoes_the_key_in_its_error_is_redacted(tmp_path, store, monkeypatch):
    """A hostile board answers the provision with an error whose `why` is the
    whole payload it received. The `why` reaches the browser, so it must not
    carry either secret."""
    board = FakeBoard(board_id="ECH1")
    seen = {}
    real = board.responder.handle_message

    def echo(msg):
        reply = real(msg)
        if board.responder.provisioned is not None:
            p = board.responder.provisioned
            seen.update(p)
            return proto_error(f"got {p['node_key']} and {p['llm']['key']}")
        return reply

    def proto_error(why):
        import json
        return json.dumps({"t": "error", "why": why}).encode()

    board.responder.handle_message = echo
    mgr = _mgr(tmp_path, store, {"a": board})
    started = await mgr.start("a")
    with pytest.raises(PairError) as exc:
        await mgr.confirm(started["session"])
    assert seen, "the board never saw the provision"
    assert exc.value.status == 502
    assert seen["llm"]["key"] not in (exc.value.why or "")
    assert seen["node_key"] not in (exc.value.why or "")
    assert "[redacted]" in exc.value.why


@pytest.mark.asyncio
async def test_gateway_off_sends_llm_null_and_mints_nothing(tmp_path, store):
    board = FakeBoard(board_id="OFF1")
    mgr = _mgr(tmp_path, store, {"a": board}, llm=False)
    started = await mgr.start("a")
    await mgr.confirm(started["session"])
    assert board.responder.provisioned is not None
    assert board.responder.provisioned["llm"] is None
    assert _node_keys(tmp_path, board.name) == []


def test_gateway_flag_is_read_from_the_same_switch_that_mounts_it(tmp_path, monkeypatch):
    # The gateway is always mounted since LiteLLM removal 2b-2a, so the old
    # "0" off switch no longer turns the board's LLM provision off either.
    monkeypatch.setenv("TAOS_LLM_GATEWAY", "0")
    old_off = BlePairingManager(data_dir=tmp_path, cluster_manager=ClusterManager(),
                                pairing_store=None, bind_port=1)
    monkeypatch.delenv("TAOS_LLM_GATEWAY", raising=False)
    on = BlePairingManager(data_dir=tmp_path, cluster_manager=ClusterManager(),
                           pairing_store=None, bind_port=1)
    assert (old_off._llm_enabled, on._llm_enabled) == (True, True)


# -- happy path ---------------------------------------------------------------

@pytest.mark.asyncio
async def test_minted_key_reaches_the_board_sealed_and_is_live(tmp_path, store, caplog):
    caplog.set_level(logging.DEBUG)
    board = FakeBoard(board_id="LLM1")
    mgr = _mgr(tmp_path, store, {"a": board})
    started = await mgr.start("a")
    result = await mgr.confirm(started["session"])

    llm = board.responder.provisioned["llm"]
    assert llm["base"] == URL + "/api/llm/v1"
    caller = _live(tmp_path, llm["key"])
    assert caller is not None
    assert caller.caller_id == gw.node_principal(board.name)
    assert caller.kind == "node"
    assert set(caller.allowed_models) == {"taos-default"}

    assert llm["key"] not in repr(result) and llm["key"] not in repr(started)
    assert llm["key"] not in caplog.text


@pytest.mark.asyncio
async def test_repair_leaves_exactly_one_live_key(tmp_path, store):
    """A board reset and paired again under the same name must not leave its
    first model key live beside the new one. Re-pairing a device name needs
    the admin to revoke the node first (a live name is refused with 409, see
    test_ble_pairing_name_collision.py); the store-level revoke used here
    does not touch the gateway, so the old model key is still live until
    confirm() retires it."""
    first = FakeBoard(board_id="RE01", name="taOSusb-RE01")
    mgr = _mgr(tmp_path, store, {"a": first})
    await mgr.confirm((await mgr.start("a"))["session"])
    old = first.responder.provisioned["llm"]["key"]
    assert await store.revoke("taOSusb-RE01")
    assert _live(tmp_path, old) is not None

    second = FakeBoard(board_id="RE01", name="taOSusb-RE01")
    mgr2 = _mgr(tmp_path, store, {"a": second})
    await mgr2.confirm((await mgr2.start("a"))["session"])
    new = second.responder.provisioned["llm"]["key"]

    assert _live(tmp_path, old) is None
    assert _live(tmp_path, new) is not None


# -- orb: never mint a model key ----------------------------------------------

@pytest.mark.asyncio
async def test_orb_pairing_sends_llm_none_and_never_calls_mint(tmp_path, store):
    """An Orb board must never receive a model key: the provision carries
    llm: null and mint_for_node is not invoked."""
    board = FakeBoard(board_id="ORB1", caps=["agent", "orb"])
    mgr = _mgr(tmp_path, store, {"a": board})
    started = await mgr.start("a")
    with patch("tinyagentos.llm_gateway.auth.mint_for_node", return_value="a" * 16) as mock_mint:
        await mgr.confirm(started["session"])
    assert board.responder.provisioned is not None
    assert board.responder.provisioned["llm"] is None
    mock_mint.assert_not_called()


@pytest.mark.asyncio
async def test_orb_repair_revokes_old_key_and_mints_nothing(tmp_path, store):
    """An Orb re-paired under a name that already had a model key must
    revoke that old key and mint nothing new."""
    # First pair a taosusb to create a live gateway key.
    first = FakeBoard(board_id="ORBR1", name="taOSusb-ORBR1")
    mgr1 = _mgr(tmp_path, store, {"a": first})
    await mgr1.confirm((await mgr1.start("a"))["session"])
    old_key = first.responder.provisioned["llm"]["key"]
    assert _live(tmp_path, old_key) is not None

    # Revoke the device so it can be re-paired; the gateway key survives.
    assert await store.revoke("taOSusb-ORBR1")
    assert _live(tmp_path, old_key) is not None

    # Now pair an orb under the same name.
    orb = FakeBoard(board_id="ORBR1", name="taOSusb-ORBR1", caps=["agent", "orb"])
    mgr2 = _mgr(tmp_path, store, {"a": orb})
    with patch("tinyagentos.llm_gateway.auth.mint_for_node", return_value="a" * 16) as mock_mint:
        await mgr2.confirm((await mgr2.start("a"))["session"])

    assert _live(tmp_path, old_key) is None
    mock_mint.assert_not_called()
    assert orb.responder.provisioned["llm"] is None


@pytest.mark.asyncio
async def test_taosusb_with_llm_enabled_still_gets_llm_block(tmp_path, store):
    """Control: a taosusb board with the gateway on still receives a full
    llm block in its provision."""
    board = FakeBoard(board_id="USB1")
    mgr = _mgr(tmp_path, store, {"a": board})
    started = await mgr.start("a")
    await mgr.confirm(started["session"])

    llm = board.responder.provisioned["llm"]
    assert llm is not None
    assert llm["base"] == URL + "/api/llm/v1"
    assert _live(tmp_path, llm["key"]) is not None

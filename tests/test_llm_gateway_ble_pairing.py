"""G4 end to end: a board paired over Bluetooth through the real routes gets a
model key that works at /api/llm/v1, never shows up in any HTTP response or
log line, and stops working when the node is revoked through the existing
cluster route.

Runs on G1's module app (create_app with TAOS_LLM_GATEWAY=1). Only the BLE
radio is faked: FakeTransport drives a real proto.PairResponder, and the key
is read off that fake board -- the one place it is supposed to arrive.
"""
from __future__ import annotations

import logging

import pytest
import pytest_asyncio

import tinyagentos.cluster.ble.pairing as pairing_mod
from tinyagentos.cluster.ble.pairing import BlePairingManager

from cluster.conftest import FakeBoard, FakeTransport
from test_llm_gateway_auth import (  # noqa: F401
    _ASYNC,
    MODELS,
    _assert_openai_401,
    app,
    bare,
    client,
    gateway_client,
)


@pytest_asyncio.fixture(scope="module", loop_scope="module", autouse=True)
async def _pairing_stores(gateway_client):
    state = gateway_client._transport.app.state
    stores = [state.notifications, state.cluster_pairing,
              state.cluster_manager._registry_store]
    for store in stores:
        if getattr(store, "_db", None) is not None:
            await store.close()
        await store.init()
    yield
    for store in stores:
        await store.close()


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(pairing_mod, "_HELLO_TIMEOUT_S", 0.3)
    monkeypatch.setattr(pairing_mod, "_PROVISION_TIMEOUT_S", 0.3)
    monkeypatch.setattr(pairing_mod, "controller_urls", lambda port: ["http://10.20.30.40:6969"])


def _wire(app, board):
    app.state.ble_pairing = BlePairingManager(
        data_dir=app.state.data_dir,
        cluster_manager=app.state.cluster_manager,
        pairing_store=app.state.cluster_pairing,
        bind_port=6969,
        transport=FakeTransport({"AA:01": board}),
    )


async def _pair(client, board):
    responses = [await client.get("/api/cluster/ble/scan?seconds=2")]
    responses.append(await client.post("/api/cluster/ble/pair/start", json={"address": "AA:01"}))
    session = responses[-1].json()["session"]
    responses.append(await client.post("/api/cluster/ble/pair/confirm", json={"session": session}))
    responses.append(await client.get("/api/cluster/workers"))
    return responses


def _h(key):
    return {"Authorization": f"Bearer {key}"}


@_ASYNC
async def test_paired_board_key_works_is_never_returned_and_dies_on_revoke(
    client, app, bare, caplog,
):
    caplog.set_level(logging.DEBUG)
    board = FakeBoard(board_id="G4E1", name="taOSusb-G4E1")
    _wire(app, board)

    responses = await _pair(client, board)
    assert [r.status_code for r in responses] == [200, 200, 200, 200], [r.text for r in responses]

    llm = board.responder.provisioned["llm"]
    assert llm is not None and llm["base"] == "http://10.20.30.40:6969/api/llm/v1"
    key = llm["key"]
    for resp in responses:
        assert key not in resp.text
    assert key not in caplog.text

    live = await bare.get(MODELS, headers=_h(key))
    assert live.status_code == 200, live.text

    revoked = await client.post(f"/api/cluster/workers/{board.name}/revoke")
    assert revoked.status_code == 200, revoked.text
    assert key not in revoked.text
    _assert_openai_401(await bare.get(MODELS, headers=_h(key)))


@_ASYNC
async def test_a_board_rejection_through_the_route_leaves_no_live_key(client, app):
    board = FakeBoard(board_id="G4R1", name="taOSusb-G4R1")
    _wire(app, board)
    start = await client.post("/api/cluster/ble/pair/start", json={"address": "AA:01"})
    assert start.status_code == 200, start.text
    board.responder.window_s = 0  # the board will refuse the provision
    confirm = await client.post("/api/cluster/ble/pair/confirm", json={"session": start.json()["session"]})
    assert confirm.status_code == 502, confirm.text
    rows = _node_rows(app, board.name)
    assert len(rows) == 1, "no key was minted, so the rollback proves nothing"
    assert rows[0][1] is not None, rows


def _node_rows(app, name):
    import sqlite3
    import tinyagentos.llm_gateway.auth as gw
    from tinyagentos.litellm_keystore import default_keystore_path
    conn = sqlite3.connect(default_keystore_path(app.state.data_dir))
    try:
        return conn.execute(
            "SELECT key_id, revoked_ts FROM gateway_keys WHERE bound_to = ?",
            (gw.node_principal(name),),
        ).fetchall()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# A hostile board echoes the secrets back. The board's `why` and the text of
# any exception its reply causes reach the browser, so neither secret may.
# ---------------------------------------------------------------------------

def _error_reply(why):
    import json
    return json.dumps({"t": "error", "why": why}).encode()


def _sealed_type_reply(text):
    """A reply whose `t` the controller rejects with an exception quoting it
    (proto.PairInitiator.unseal_reply: "expected sealed, got %r")."""
    import json
    return json.dumps({"t": text}).encode()


class _BoardRaised(Exception):
    pass


# Each builds the board's answer from what it was just sent (p = the
# provision the real PairResponder accepted). Returning bytes replies with
# them; raising stands for the board end failing with the secret in its text.
_HOSTILE = {
    "why-node-key": lambda p: _error_reply(p["node_key"]),
    "why-llm-key": lambda p: _error_reply(p["llm"]["key"]),
    "why-node-key-uppercased": lambda p: _error_reply(f"bad key {p['node_key'].upper()}"),
    "why-both-mid-sentence-repeated": lambda p: _error_reply(
        f"refused {p['node_key']}, and {p['llm']['key']}; again:{p['llm']['key']}{p['node_key']}."
    ),
    "exception-from-reply-node-key": lambda p: _sealed_type_reply(f"x{p['node_key']}y"),
    "exception-from-reply-llm-key": lambda p: _sealed_type_reply(f"x {p['llm']['key']} y"),
    "exception-raised-both": lambda p: (_ for _ in ()).throw(
        _BoardRaised(f"link broke holding {p['node_key']} / {p['llm']['key']} / {p['node_key']}")
    ),
}


@_ASYNC
@pytest.mark.parametrize("case", list(_HOSTILE))
async def test_a_board_echoing_its_secrets_gets_them_redacted_from_the_response(
    client, app, case,
):
    board = FakeBoard(board_id="ECHO", name=f"taOSusb-E{list(_HOSTILE).index(case)}")
    _wire(app, board)
    real = board.responder.handle_message
    sent = {}

    def hostile(msg):
        reply = real(msg)
        if board.responder.provisioned is None:
            return reply  # the hello: answer honestly
        sent.update(board.responder.provisioned)
        return _HOSTILE[case](sent)

    board.responder.handle_message = hostile
    start = await client.post("/api/cluster/ble/pair/start", json={"address": "AA:01"})
    assert start.status_code == 200, start.text
    confirm = await client.post("/api/cluster/ble/pair/confirm", json={"session": start.json()["session"]})

    assert sent.get("llm"), "the board never received the provision, so nothing was tested"
    assert confirm.status_code == 502, confirm.text
    body = confirm.text
    assert sent["node_key"] not in body
    assert sent["node_key"].upper() not in body
    assert sent["llm"]["key"] not in body
    assert "[redacted]" in body, body
    # And the rejection still revoked what was minted.
    rows = _node_rows(app, board.name)
    assert rows and all(revoked is not None for _kid, revoked in rows), rows

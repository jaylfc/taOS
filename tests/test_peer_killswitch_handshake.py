"""The collaboration kill switch covers the handshake route and outbound delivery (tsk-zr6chz).

``POST /api/peer/handshake`` lives on ``handshake_router``, outside the bearer
router's dependencies, and ``peer.deliver_to_peer`` is the shared outbound
path.  Both must honour ``collaboration.json`` exactly as the bearer routes do:
disabled is 503 before any work, a paused contact is 403 before its nonce is
recorded, and nothing is sent to a paused contact or while disabled.
"""
from __future__ import annotations

import json

import httpx
import pytest
import pytest_asyncio

from helpers.two_nodes import close_node, make_node, node_env
from test_peer_handshake_two_controller import (
    _handshake_envelope,
    _post_handshake,
    _record_request_out,
)


@pytest_asyncio.fixture
async def nodes(tmp_path):
    a = await make_node("node-a", tmp_path / "a")
    b = await make_node("node-b", tmp_path / "b")
    try:
        yield a, b
    finally:
        for n in (a, b):
            hs = getattr(n.app.state, "hub_store", None)
            if hs is not None and getattr(hs, "_db", None) is not None:
                await hs.close()
        await close_node(a)
        await close_node(b)


def _write_switch(node, data):
    (node.data_dir / "collaboration.json").write_text(json.dumps(data))


def _count_record_nonce(monkeypatch, node):
    store = node.app.state.contacts_store
    real = store.record_nonce
    calls: list[tuple] = []

    async def _spy(*args, **kwargs):
        calls.append(args)
        return await real(*args, **kwargs)

    monkeypatch.setattr(store, "record_nonce", _spy)
    return calls


@pytest.mark.asyncio
class TestHandshakeRoute:
    async def test_valid_handshake_is_503_while_disabled(self, nodes, monkeypatch):
        a, b = nodes
        await _record_request_out(a, b)
        _write_switch(a, {"enabled": False})
        calls = _count_record_nonce(monkeypatch, a)
        resp = await _post_handshake(a, _handshake_envelope(b, a))
        assert resp.status_code == 503, resp.text
        assert resp.json()["detail"] == "collaboration disabled"
        assert calls == []
        assert await a.app.state.contacts_store.get_contact(b.contact_id) is None

    async def test_paused_contact_handshake_is_403_and_burns_no_nonce(self, nodes, monkeypatch):
        a, b = nodes
        await _record_request_out(a, b)
        _write_switch(a, {"enabled": True, "paused_contacts": [b.contact_id]})
        calls = _count_record_nonce(monkeypatch, a)
        resp = await _post_handshake(a, _handshake_envelope(b, a))
        assert resp.status_code == 403, resp.text
        assert resp.json()["detail"] == "contact paused"
        assert calls == []
        assert await a.app.state.contacts_store.get_contact(b.contact_id) is None

    async def test_reenabled_handshake_passes(self, nodes):
        a, b = nodes
        await _record_request_out(a, b)
        _write_switch(a, {"enabled": False})
        assert (await _post_handshake(a, _handshake_envelope(b, a))).status_code == 503
        _write_switch(a, {"enabled": True, "paused_contacts": []})
        resp = await _post_handshake(a, _handshake_envelope(b, a))
        assert resp.status_code == 200, resp.text


def _recording_client(seen: dict) -> httpx.AsyncClient:
    async def handler(request: httpx.Request) -> httpx.Response:
        seen.setdefault("urls", []).append(str(request.url))
        return httpx.Response(200, json={"status": "received"})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def _link(node, peer):
    store = node.app.state.contacts_store
    await store.add_contact(
        contact_id=peer.contact_id, hub_username="peer", display_name="Peer",
        ed25519_pub=peer.signing_pub, x25519_pub=peer.encryption_pub,
        peer_fingerprint=peer.fingerprint,
    )
    await store.establish_peer_link(
        contact_id=peer.contact_id, inbound_token="i" * 64, outbound_token="o" * 64,
        endpoints=[{"kind": "hub", "url": "http://93.184.216.34", "priority": 0}],
    )
    return store


async def _deliver(node, peer, seen):
    from tinyagentos.peer import deliver_to_peer

    store = node.app.state.contacts_store
    client = _recording_client(seen)
    try:
        # No data_dir argument: the switch is resolved from TAOS_DATA_DIR,
        # the way a caller without a request in hand reaches it.
        with node_env(node.data_dir):
            return await deliver_to_peer(
                store, peer.contact_id, "/api/peer/inbox", {"kind": "chat"},
                http_client=client,
            )
    finally:
        await client.aclose()


@pytest.mark.asyncio
class TestDeliverToPeer:
    async def test_delivers_by_default(self, nodes):
        a, b = nodes
        await _link(a, b)
        seen: dict = {}
        assert await _deliver(a, b, seen) is True
        assert seen.get("urls") == ["http://93.184.216.34/api/peer/inbox"]

    async def test_sends_nothing_while_disabled(self, nodes):
        a, b = nodes
        await _link(a, b)
        _write_switch(a, {"enabled": False})
        seen: dict = {}
        assert await _deliver(a, b, seen) is False
        assert seen.get("urls", []) == []

    async def test_sends_nothing_to_paused_contact(self, nodes):
        a, b = nodes
        await _link(a, b)
        _write_switch(a, {"enabled": True, "paused_contacts": [b.contact_id]})
        seen: dict = {}
        assert await _deliver(a, b, seen) is False
        assert seen.get("urls", []) == []

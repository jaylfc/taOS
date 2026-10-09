"""Friend-accept completes the peer token exchange between two real nodes (tsk-n6gfyk).

Node A asked to befriend node B (a ``friend_request_out`` edge on A).  B accepts:
its accept route delivers a signed handshake to A's recorded endpoint, A's
``POST /api/peer/handshake`` pins B's keys and answers with the token it mints,
and both ends finish with a usable bearer for the other.  Delivery is routed
in-process through ``app.state.peer_http_client`` so the CGNAT literal endpoint
``http://100.64.0.2:6969`` resolves to node A without a socket.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from helpers.two_nodes import close_node, make_node, node_env
from httpx import ASGITransport, AsyncClient
from taos_test_csrf import csrf_event_hooks

A_ENDPOINT = "http://100.64.0.2:6969"


@pytest_asyncio.fixture
async def nodes(tmp_path):
    a = await make_node("node-a", tmp_path / "a")
    b = await make_node("node-b", tmp_path / "b")
    assert a.contact_id != b.contact_id
    try:
        yield a, b
    finally:
        await close_node(a)
        await close_node(b)


async def _hub_store(node):
    from tinyagentos.hub import store as hub_store

    existing = getattr(node.app.state, "hub_store", None)
    if existing is not None:
        return existing
    with node_env(node.data_dir):
        store = hub_store.HubStore(Path(hub_store.default_db_path()))
    await store.init()
    node.app.state.hub_store = store
    return store


async def _record_request_out(a, b):
    from tinyagentos.hub import relationships

    store = await _hub_store(a)
    await store.put_relationship(b.fingerprint, relationships.REL_REQUEST_OUT)


def _route_to(node):
    """An http client whose every POST lands on *node* in-process."""
    return AsyncClient(transport=ASGITransport(app=node.asgi))


async def _accept_on(b, a, monkeypatch, *, endpoints=None):
    """Drive B's accept route with the directory stubbed to describe A."""
    import tinyagentos.routes.hub as hub_mod

    directory = {
        "peer": a.fingerprint,
        "username": "node-a",
        "display_name": "Node A",
        "signing_pubkey": a.signing_pub,
        "encryption_pubkey": a.encryption_pub,
        "endpoints": [A_ENDPOINT] if endpoints is None else endpoints,
    }

    async def fake_forward(request, method, path, *, body=None):
        from fastapi.responses import Response

        return Response(
            content=json.dumps(directory).encode("utf-8"),
            status_code=200,
            media_type="application/json",
        )

    monkeypatch.setattr(hub_mod, "_forward_to", fake_forward)
    b.app.state.peer_http_client = _route_to(a)

    b.app.state.auth.setup_user("admin", "Admin", "", "testpass")
    uid = b.app.state.auth.find_user("admin")["id"]
    token = b.app.state.auth.create_session(user_id=uid, long_lived=True)
    async with AsyncClient(
        transport=ASGITransport(app=b.asgi),
        base_url="http://node-b",
        cookies={"taos_session": token},
        event_hooks=csrf_event_hooks(),
    ) as c:
        resp = await c.post(
            "/api/hub/friends/requests/rid-1/accept",
            json={"peer_fingerprint": a.fingerprint},
        )
    await b.app.state.peer_http_client.aclose()
    return resp


async def _inbox(node, envelope, token):
    async with node.client() as c:
        return await c.post(
            "/api/peer/inbox", json={"envelope": envelope},
            headers={"Authorization": f"Bearer {token}"},
        )


def _handshake_from(sender, to_node, *, signing_pub=None, from_id=None, token="t" * 64):
    body = {
        "inbound_token": token,
        "endpoints": [],
        "signing_pubkey": signing_pub or sender.signing_pub,
        "encryption_pubkey": sender.encryption_pub,
    }
    return sender.sign_envelope(
        to=to_node.contact_id, kind="handshake", body=body, from_id=from_id,
    )


@pytest.mark.asyncio
async def test_accept_exchanges_tokens_both_ways(nodes, monkeypatch):
    a, b = nodes
    await _record_request_out(a, b)

    resp = await _accept_on(b, a, monkeypatch)
    assert resp.status_code == 200, resp.text

    b_link = await b.app.state.contacts_store.get_peer_link(a.contact_id)
    a_link = await a.app.state.contacts_store.get_peer_link(b.contact_id)
    a_contact = await a.app.state.contacts_store.get_contact(b.contact_id)
    assert a_contact is not None, "A has no contact row for B after the accept"
    assert a_contact["ed25519_pub"] == b.signing_pub
    assert b_link is not None and a_link is not None
    assert b_link["outbound_token"], "B never received A's token"
    assert a_link["outbound_token"], "A never received B's token"

    from tinyagentos.contacts_store import _hash_token

    assert _hash_token(b_link["outbound_token"]) == a_link["inbound_token_hash"]
    assert _hash_token(a_link["outbound_token"]) == b_link["inbound_token_hash"]

    # A can now reach B's authenticated inbox with the bearer B minted.
    envelope = a.sign_envelope(to=b.contact_id, kind="ack", body={"x": 1})
    r = await _inbox(b, envelope, a_link["outbound_token"])
    assert r.status_code == 200, r.text
    # And B reaches A with the bearer A minted.
    envelope = b.sign_envelope(to=a.contact_id, kind="ack", body={"y": 2})
    r = await _inbox(a, envelope, b_link["outbound_token"])
    assert r.status_code == 200, r.text

    from tinyagentos.hub import relationships

    assert await (await _hub_store(a)).has_edge(b.fingerprint, relationships.REL_FRIEND)


@pytest.mark.asyncio
async def test_handshake_refused_without_friend_request(nodes):
    a, b = nodes
    envelope = _handshake_from(b, a)
    async with a.client() as c:
        r = await c.post("/api/peer/handshake", json={"envelope": envelope})
    assert r.status_code == 403, r.text
    assert await a.app.state.contacts_store.get_contact(b.contact_id) is None


@pytest.mark.asyncio
async def test_handshake_refused_when_pubkey_does_not_match_from(nodes):
    a, b = nodes
    await _record_request_out(a, b)
    # Signed by B, but the body claims A's key: fingerprint(body) != from.
    envelope = _handshake_from(b, a, signing_pub=a.signing_pub)
    async with a.client() as c:
        r = await c.post("/api/peer/handshake", json={"envelope": envelope})
    assert r.status_code == 403, r.text
    assert await a.app.state.contacts_store.get_contact(b.contact_id) is None


@pytest.mark.asyncio
async def test_handshake_replay_is_409(nodes):
    a, b = nodes
    await _record_request_out(a, b)
    envelope = _handshake_from(b, a)
    async with a.client() as c:
        first = await c.post("/api/peer/handshake", json={"envelope": envelope})
        second = await c.post("/api/peer/handshake", json={"envelope": envelope})
    assert first.status_code == 200, first.text
    assert second.status_code == 409, second.text


class _Recorder:
    """Stand-in http client: records URLs; connecting is the failure."""

    def __init__(self):
        self.urls = []

    async def post(self, url, **kw):
        self.urls.append(url)
        return httpx.Response(200, json={"envelope": {}})


@pytest.mark.asyncio
async def test_delivery_refuses_unrecorded_private_endpoints():
    from tinyagentos.peer import deliver_handshake, known_hostports

    recorded = [{"kind": "hub", "url": A_ENDPOINT, "priority": 0}]
    known = known_hostports(recorded)
    assert known == frozenset({("100.64.0.2", 6969)})

    cases = {
        "arbitrary CGNAT url": "http://100.64.0.9:6969",
        "recorded host, other port": "http://100.64.0.2:7000",
        "loopback": "http://127.0.0.1:6969",
        "metadata link-local": "http://169.254.169.254",
    }
    for label, url in cases.items():
        rec = _Recorder()
        got = await deliver_handshake({}, [url], http_client=rec, known_endpoints=known)
        assert got is None and rec.urls == [], f"{label} was contacted: {rec.urls}"

    # Loopback and link-local stay blocked even when recorded.
    for url in ("http://127.0.0.1:6969", "http://169.254.169.254:80"):
        rec = _Recorder()
        got = await deliver_handshake(
            {}, [url], http_client=rec, known_endpoints=known_hostports([url]),
        )
        assert got is None and rec.urls == [], f"recorded {url} was contacted"

    # The recorded CGNAT endpoint itself is reachable.
    rec = _Recorder()
    await deliver_handshake({}, recorded, http_client=rec, known_endpoints=known)
    assert rec.urls == [A_ENDPOINT + "/api/peer/handshake"]

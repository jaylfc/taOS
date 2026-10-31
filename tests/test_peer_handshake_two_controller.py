"""Friend-accept completes the peer handshake between two real controllers.

Node A sent B a friend request (A holds a ``friend_request_out`` edge for B).
B accepts: B pins A, mints the token A must present, and delivers a signed
handshake to A's ``POST /api/peer/handshake``.  A verifies it against the key
in the envelope body, checks the outgoing request edge, pins B, mints the
token B must present, and replies with a signed ``handshake_reply``.  B pins
that token as its outbound token.  Both directions then authenticate.

Delivery is routed in-process: the CGNAT literal endpoint the directory
returns for A (``http://100.64.0.2:6969``) is served by A's app through an
``ASGITransport``.  The guarded client factory is swapped for that ASGI
client, and the swap asserts the known-endpoint set it was handed, so the
SSRF allowance is proven to come from the stored peer link.
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from fastapi.responses import Response
from httpx import ASGITransport, AsyncClient

from helpers.two_nodes import close_node, make_node, node_env
from taos_test_csrf import csrf_event_hooks

_A_ENDPOINT = "http://100.64.0.2:6969"


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


async def _hub_store(node):
    """Open the node's hub store under its own data dir and cache it on the app."""
    from tinyagentos.hub import store as hub_store

    existing = getattr(node.app.state, "hub_store", None)
    if existing is not None:
        return existing
    with node_env(node.data_dir):
        store = hub_store.HubStore(Path(hub_store.default_db_path()))
    await store.init()
    node.app.state.hub_store = store
    return store


async def _record_request_out(host, peer):
    from tinyagentos.hub import relationships

    store = await _hub_store(host)
    await store.put_relationship(peer.fingerprint, relationships.REL_REQUEST_OUT)


def _signed_in_client(node) -> AsyncClient:
    auth = node.app.state.auth
    with node_env(node.data_dir):
        auth.setup_user("admin", "Test Admin", "", "testpass")
        rec = auth.find_user("admin")
        token = auth.create_session(user_id=rec["id"] if rec else "", long_lived=True)
    return AsyncClient(
        transport=ASGITransport(app=node.asgi),
        base_url=f"http://{node.name}",
        cookies={"taos_session": token},
        event_hooks=csrf_event_hooks(),
    )


def _patch_directory(monkeypatch, peer):
    """The directory accept answers with *peer*'s identity and CGNAT endpoint."""
    directory = {
        "peer": peer.fingerprint,
        "username": "alice",
        "display_name": "Alice",
        "signing_pubkey": peer.signing_pub,
        "encryption_pubkey": peer.encryption_pub,
        "endpoints": [_A_ENDPOINT],
    }

    async def fake_forward(request, method, path, *, body=None):
        return Response(
            content=json.dumps(directory).encode("utf-8"),
            status_code=200,
            media_type="application/json",
        )

    monkeypatch.setattr("tinyagentos.routes.hub._forward_to", fake_forward)


def _route_delivery_to(monkeypatch, target, seen: dict):
    """Replace the guarded client factory with an in-process client to *target*."""
    from tinyagentos.routes.desktop_browser import ssrf

    def factory(*, allow_hostports=frozenset(), **kw):
        seen["allow_hostports"] = allow_hostports
        return httpx.AsyncClient(transport=ASGITransport(app=target.asgi))

    monkeypatch.setattr(ssrf, "guarded_async_client", factory)


async def _accept(b, rid="rid-1"):
    """Accept as node *b*, then run the background network exchange.

    The route starts the exchange as a background task.  Node identity here is
    selected by the process-global ``TAOS_DATA_DIR``, which the per-request ASGI
    shim sets and restores, so an exchange overlapping the tail of B's own
    request would have A's handler run under the wrong environment (the harness
    requires sequential requests).  Capture the supervised task's coroutine
    instead of starting it, then run it to completion after the accept has
    returned, holding *b*'s environment.  In production one process is one
    node and the environment never changes.
    """
    from unittest import mock

    from tinyagentos import task_utils
    from tinyagentos.routes import hub as hub_routes

    deferred: list = []

    def _defer(coro, task_set):
        assert task_set is hub_routes._BACKGROUND_TASKS
        deferred.append(coro)

    with mock.patch.object(task_utils, "_create_supervised_task", _defer):
        async with _signed_in_client(b) as c:
            resp = await c.post(f"/api/hub/friends/requests/{rid}/accept", json={})
    with node_env(b.data_dir):
        for coro in deferred:
            await coro
    return resp


def _handshake_envelope(sender, receiver, *, inbound_token="t" * 64, from_id=None,
                        signing_pub=None, endpoints=None):
    return sender.sign_envelope(
        to=receiver.contact_id,
        kind="handshake",
        from_id=from_id,
        body={
            "inbound_token": inbound_token,
            "endpoints": endpoints if endpoints is not None else [f"http://{sender.name}"],
            "signing_pubkey": signing_pub if signing_pub is not None else sender.signing_pub,
            "encryption_pubkey": sender.encryption_pub,
        },
    )


async def _post_handshake(node, envelope):
    async with node.client() as c:
        return await c.post("/api/peer/handshake", json={"envelope": envelope})


# ---------------------------------------------------------------------------
# The full exchange on friend-accept
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_accept_exchanges_tokens_both_ways(nodes, monkeypatch):
    from tinyagentos.contacts_store import _hash_token

    a, b = nodes
    await _record_request_out(a, b)
    await _hub_store(b)
    _patch_directory(monkeypatch, a)
    seen: dict = {}
    _route_delivery_to(monkeypatch, a, seen)

    resp = await _accept(b)
    assert resp.status_code == 200, resp.text
    assert resp.json()["state"] == "accepted"

    a_store = a.app.state.contacts_store
    b_store = b.app.state.contacts_store
    b_link = await b_store.get_peer_link(a.contact_id)
    assert b_link is not None
    assert b_link["outbound_token"] != "", "B never received A's inbound token"

    a_contact = await a_store.get_contact(b.contact_id)
    assert a_contact is not None, "A has no contact row for B"
    assert a_contact["ed25519_pub"] == b.signing_pub
    a_link = await a_store.get_peer_link(b.contact_id)
    assert a_link is not None
    # Each side's outbound token is the token the other side minted for it.
    assert _hash_token(a_link["outbound_token"]) == b_link["inbound_token_hash"]
    assert _hash_token(b_link["outbound_token"]) == a_link["inbound_token_hash"]
    # The SSRF allowance came from B's stored peer link for A, nothing else.
    assert seen["allow_hostports"] == frozenset({("100.64.0.2", 6969)})

    # A -> B with the token B minted.
    env = a.sign_envelope(to=b.contact_id, kind="chat", body={"text": "hi"})
    async with b.client() as c:
        r = await c.post(
            "/api/peer/inbox", json={"envelope": env},
            headers={"Authorization": f"Bearer {a_link['outbound_token']}"},
        )
    assert r.status_code == 200, f"A->B: {r.status_code}: {r.text}"

    # B -> A with the token A minted.
    env = b.sign_envelope(to=a.contact_id, kind="chat", body={"text": "hi"})
    async with a.client() as c:
        r = await c.post(
            "/api/peer/inbox", json={"envelope": env},
            headers={"Authorization": f"Bearer {b_link['outbound_token']}"},
        )
    assert r.status_code == 200, f"B->A: {r.status_code}: {r.text}"


@pytest.mark.asyncio
async def test_accept_succeeds_when_delivery_fails(nodes, monkeypatch):
    """A refused handshake never fails the accept; outbound stays empty."""
    a, b = nodes
    # No friend_request_out edge on A: A refuses the handshake with 403.
    await _hub_store(a)
    await _hub_store(b)
    _patch_directory(monkeypatch, a)
    _route_delivery_to(monkeypatch, a, {})

    resp = await _accept(b)
    assert resp.status_code == 200, resp.text
    b_link = await b.app.state.contacts_store.get_peer_link(a.contact_id)
    assert b_link is not None
    assert b_link["outbound_token"] == ""
    assert await a.app.state.contacts_store.get_contact(b.contact_id) is None


# ---------------------------------------------------------------------------
# POST /api/peer/handshake refusals
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handshake_without_request_out_edge_is_403(nodes):
    a, b = nodes
    await _hub_store(a)
    resp = await _post_handshake(a, _handshake_envelope(b, a))
    assert resp.status_code == 403, resp.text
    assert await a.app.state.contacts_store.get_contact(b.contact_id) is None


@pytest.mark.asyncio
async def test_handshake_from_blocked_peer_is_403(nodes):
    from tinyagentos.hub import relationships

    a, b = nodes
    await _record_request_out(a, b)
    store = await _hub_store(a)
    await store.put_relationship(b.fingerprint, relationships.REL_BLOCK)
    resp = await _post_handshake(a, _handshake_envelope(b, a))
    assert resp.status_code == 403, resp.text
    assert await a.app.state.contacts_store.get_contact(b.contact_id) is None


@pytest.mark.asyncio
async def test_handshake_body_pubkey_not_matching_from_is_403(nodes):
    a, b = nodes
    await _record_request_out(a, b)
    # Signed by B, from B, but the body claims A's signing key.
    env = _handshake_envelope(b, a, signing_pub=a.signing_pub)
    resp = await _post_handshake(a, env)
    assert resp.status_code == 403, resp.text
    assert await a.app.state.contacts_store.get_contact(b.contact_id) is None


@pytest.mark.asyncio
async def test_handshake_signed_by_another_key_is_403(nodes):
    a, b = nodes
    await _record_request_out(a, b)
    # Body and from name B, but A signed it.
    env = a.sign_envelope(
        to=a.contact_id, kind="handshake", from_id=b.contact_id,
        body={"inbound_token": "t" * 64, "endpoints": [],
              "signing_pubkey": b.signing_pub, "encryption_pubkey": b.encryption_pub},
    )
    resp = await _post_handshake(a, env)
    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_handshake_replayed_nonce_is_409(nodes):
    a, b = nodes
    await _record_request_out(a, b)
    env = _handshake_envelope(b, a)
    first = await _post_handshake(a, env)
    assert first.status_code == 200, first.text
    second = await _post_handshake(a, env)
    assert second.status_code == 409, second.text


@pytest.mark.asyncio
async def test_handshake_reply_is_signed_and_carries_minted_token(nodes):
    from tinyagentos.contacts_store import _hash_token
    from tinyagentos.peer import verify_envelope, verify_envelope_signature

    a, b = nodes
    await _record_request_out(a, b)
    env = _handshake_envelope(b, a, inbound_token="b" * 64)
    resp = await _post_handshake(a, env)
    assert resp.status_code == 200, resp.text
    reply = resp.json()["envelope"]
    assert verify_envelope(reply, expected_kind="handshake_reply") == (True, "")
    assert reply["from"] == a.contact_id
    assert reply["to"] == b.contact_id
    assert verify_envelope_signature(reply, a.signing_pub)

    link = await a.app.state.contacts_store.get_peer_link(b.contact_id)
    assert link["outbound_token"] == "b" * 64
    assert link["inbound_token_hash"] == _hash_token(reply["body"]["inbound_token"])


# ---------------------------------------------------------------------------
# deliver_handshake: the known-endpoint SSRF allowance
# ---------------------------------------------------------------------------


def _recording_client(seen: dict) -> httpx.AsyncClient:
    def handler(req: httpx.Request) -> httpx.Response:
        seen.setdefault("urls", []).append(str(req.url))
        return httpx.Response(200, json={})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def _deliver(endpoints, known):
    from tinyagentos.peer import deliver_handshake

    seen: dict = {}
    client = _recording_client(seen)
    try:
        ok = await deliver_handshake(
            {"kind": "handshake"}, endpoints,
            http_client=client, known_endpoints=known,
        )
    finally:
        await client.aclose()
    return ok, seen.get("urls", [])


@pytest.mark.asyncio
async def test_unrecorded_cgnat_endpoint_is_not_contacted():
    ok, urls = await _deliver([_A_ENDPOINT], frozenset())
    assert ok is False
    assert urls == []


@pytest.mark.asyncio
async def test_recorded_cgnat_endpoint_is_delivered_to_handshake_route():
    from tinyagentos.peer import known_hostports

    known = known_hostports([{"kind": "hub", "url": _A_ENDPOINT, "priority": 0}])
    ok, urls = await _deliver([_A_ENDPOINT], known)
    assert ok is True
    assert urls == [f"{_A_ENDPOINT}/api/peer/handshake"]


@pytest.mark.asyncio
async def test_recorded_cgnat_host_on_another_port_is_blocked():
    from tinyagentos.peer import known_hostports

    known = known_hostports([_A_ENDPOINT])
    ok, urls = await _deliver(["http://100.64.0.2:7000"], known)
    assert ok is False
    assert urls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("url", ["http://127.0.0.1:6969", "http://169.254.169.254:80"])
async def test_loopback_and_link_local_stay_blocked_when_recorded(url):
    from tinyagentos.peer import known_hostports

    known = known_hostports([url])
    assert known, "the endpoint must actually be in the known set"
    ok, urls = await _deliver([url], known)
    assert ok is False
    assert urls == []


def test_guard_allowance_is_exact_host_and_port():
    from tinyagentos.routes.desktop_browser.ssrf import (
        SsrfBlockedError,
        validate_url_or_raise,
    )

    allow = frozenset({("100.64.0.2", 6969), ("10.0.0.5", 80)})
    assert validate_url_or_raise("http://100.64.0.2:6969/x", allow_hostports=allow)
    assert validate_url_or_raise("http://10.0.0.5/x", allow_hostports=allow)
    for bad in ("http://100.64.0.2:6970/", "http://100.64.0.3:6969/",
                "https://10.0.0.5/", "http://100.64.0.2/"):
        with pytest.raises(SsrfBlockedError):
            validate_url_or_raise(bad, allow_hostports=allow)
    for always in ("http://127.0.0.1:80/", "http://169.254.169.254:80/"):
        host_port = (always.split("//")[1].split(":")[0], 80)
        with pytest.raises(SsrfBlockedError):
            validate_url_or_raise(always, allow_hostports=frozenset({host_port}))


@pytest.mark.asyncio
async def test_deliver_to_peer_skips_link_without_outbound_token(nodes):
    """A link whose handshake never completed is not delivered to at all."""
    from tinyagentos.peer import deliver_to_peer

    a, b = nodes
    store = a.app.state.contacts_store
    await store.add_contact(
        contact_id=b.contact_id, hub_username="bob", display_name="Bob",
        ed25519_pub=b.signing_pub, x25519_pub=b.encryption_pub,
        peer_fingerprint=b.fingerprint,
    )
    await store.establish_peer_link(
        contact_id=b.contact_id, inbound_token="i" * 64, outbound_token="",
        endpoints=[{"kind": "hub", "url": "http://93.184.216.34", "priority": 0}],
    )
    seen: dict = {}
    client = _recording_client(seen)
    try:
        ok = await deliver_to_peer(
            store, b.contact_id, "/api/peer/inbox", {"kind": "chat"},
            http_client=client,
        )
    finally:
        await client.aclose()
    assert ok is False
    assert seen.get("urls", []) == []

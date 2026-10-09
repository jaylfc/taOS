"""Peer envelopes carry ONE identity form: ``hub:<signing fingerprint>``.

Two real nodes (``helpers.two_nodes``), each with its own data dir and key.
Node B pins node A from A's PUBLIC keys only, exactly as friend-accept does
(routes/hub.py keys the contact row on the peer fingerprint).  An envelope A
signs must then be accepted by B's inbox; every mismatch between the
envelope, the pinned row and the signing key must be refused.
"""
from __future__ import annotations

import pytest
import pytest_asyncio

from helpers.two_nodes import close_node, make_node, node_env, pin_contact

_ZERO_FP = "0" * 64


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


async def _post(node, path, envelope, token):
    async with node.client() as c:
        return await c.post(
            path, json={"envelope": envelope},
            headers={"Authorization": f"Bearer {token}"},
        )


@pytest.mark.asyncio
class TestRealEnvelopeAccepted:
    async def test_inbox_accepts_envelope_signed_by_pinned_peer(self, nodes):
        a, b = nodes
        token = await pin_contact(b, a, hub_username="alice", display_name="Alice")
        env = a.sign_envelope(to=b.contact_id, kind="handshake", body={"hello": "b"})
        resp = await _post(b, "/api/peer/inbox", env, token)
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        data = resp.json()
        assert data["status"] == "received"
        assert data["nonce"] == env["nonce"]

    async def test_chat_accepts_envelope_signed_by_pinned_peer(self, nodes):
        a, b = nodes
        token = await pin_contact(b, a, hub_username="alice", display_name="Alice")
        env = a.sign_envelope(to=b.contact_id, kind="chat", body={"text": "hi"})
        resp = await _post(b, "/api/peer/chat", env, token)
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
        assert resp.json()["status"] == "received"

    async def test_build_envelope_stamps_fingerprint_identity(self, nodes):
        """``peer.build_envelope`` must produce what the harness produces."""
        from tinyagentos.peer import build_envelope, local_contact_id, verify_envelope_signature

        a, b = nodes
        with node_env(a.data_dir):
            assert local_contact_id() == a.contact_id
            env = build_envelope(to_contact_id=b.contact_id, kind="handshake", body={"x": 1})
        assert env["from"] == a.contact_id
        assert env["to"] == b.contact_id
        assert verify_envelope_signature(env, a.signing_pub)

    async def test_build_envelope_output_is_accepted_by_peer_inbox(self, nodes):
        from tinyagentos.peer import build_envelope

        a, b = nodes
        token = await pin_contact(b, a, hub_username="alice", display_name="Alice")
        with node_env(a.data_dir):
            env = build_envelope(to_contact_id=b.contact_id, kind="handshake")
        resp = await _post(b, "/api/peer/inbox", env, token)
        assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"


@pytest.mark.asyncio
class TestRefusingDirection:
    async def test_signature_by_another_key_is_rejected(self, nodes):
        """from = A's id, but signed with B's key: 403 invalid signature."""
        a, b = nodes
        token = await pin_contact(b, a)
        env = b.sign_envelope(to=b.contact_id, kind="handshake", from_id=a.contact_id)
        resp = await _post(b, "/api/peer/inbox", env, token)
        assert resp.status_code == 403, f"{resp.status_code}: {resp.text}"
        assert "signature" in resp.text

    async def test_wrong_recipient_is_rejected(self, nodes):
        a, b = nodes
        token = await pin_contact(b, a)
        env = a.sign_envelope(to=f"hub:{_ZERO_FP}", kind="handshake")
        resp = await _post(b, "/api/peer/inbox", env, token)
        assert resp.status_code == 403, f"{resp.status_code}: {resp.text}"

    async def test_username_form_from_is_rejected(self, nodes):
        a, b = nodes
        token = await pin_contact(b, a, hub_username="alice")
        env = a.sign_envelope(to=b.contact_id, kind="handshake", from_id="hub:alice")
        resp = await _post(b, "/api/peer/inbox", env, token)
        assert resp.status_code == 403, f"{resp.status_code}: {resp.text}"

    async def test_legacy_username_keyed_row_is_rejected(self, nodes):
        """A contact row keyed hub:<username> is a legacy row: refused, never matched by name."""
        a, b = nodes
        token = await pin_contact(b, a, contact_id="hub:alice", hub_username="alice")
        env = a.sign_envelope(to=b.contact_id, kind="handshake", from_id="hub:alice")
        resp = await _post(b, "/api/peer/inbox", env, token)
        assert resp.status_code == 403, f"{resp.status_code}: {resp.text}"
        assert "fingerprint" in resp.text

    async def test_legacy_row_rejected_on_chat_too(self, nodes):
        a, b = nodes
        token = await pin_contact(b, a, contact_id="hub:alice", hub_username="alice")
        env = a.sign_envelope(to=b.contact_id, kind="chat", from_id="hub:alice", body={"text": "x"})
        resp = await _post(b, "/api/peer/chat", env, token)
        assert resp.status_code == 403, f"{resp.status_code}: {resp.text}"


class _FakeDecisions:
    def __init__(self):
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return {"id": "dec-1"}


@pytest.mark.asyncio
async def test_collab_invite_names_inviter_from_contact_row(nodes):
    """The Decisions question shows the contact's display name, not the 64-hex id."""
    a, b = nodes
    token = await pin_contact(b, a, hub_username="alice", display_name="Alice Example")
    fake = _FakeDecisions()
    b.app.state.decision_store = fake
    body = {
        "invite_id": "inv-1",
        "project_id": "prj-1",
        "project_name": "Garden",
        "inviter": a.contact_id,
        "pin_required": False,
    }
    env = a.sign_envelope(to=b.contact_id, kind="collab_invite", body=body)
    resp = await _post(b, "/api/peer/inbox", env, token)
    assert resp.status_code == 200, f"{resp.status_code}: {resp.text}"
    assert resp.json()["dispatched"] is True
    (call,) = fake.calls
    assert call["question"].startswith("Alice Example invites you")
    assert a.fingerprint not in call["question"]
    assert call["metadata"]["inviter"] == a.contact_id
    assert call["metadata"]["contact_id"] == a.contact_id

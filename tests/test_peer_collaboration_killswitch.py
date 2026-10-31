"""The collaboration kill switch (tsk-skvthj).

``collaboration.enabled`` false: every /api/peer route answers 503 before the
bearer lookup and before any signature work.  A contact in
``paused_contacts``: 403 after the bearer check, before its nonce is
recorded.  Both are flipped through the admin-only
GET/PUT /api/settings/collaboration.

Real nodes from ``helpers.two_nodes``: node B pins node A, A signs envelopes
with its own key, and B's switch is flipped over HTTP with B's local token.
"""
from __future__ import annotations

import json

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from helpers.two_nodes import close_node, make_node, pin_contact


@pytest_asyncio.fixture
async def nodes(tmp_path):
    a = await make_node("node-a", tmp_path / "a")
    b = await make_node("node-b", tmp_path / "b")
    c = await make_node("node-c", tmp_path / "c")
    try:
        yield a, b, c
    finally:
        await close_node(a)
        await close_node(b)
        await close_node(c)


async def _put_collab(node, body):
    token = node.app.state.auth.get_local_token()
    async with node.client() as cl:
        return await cl.put(
            "/api/settings/collaboration", json=body,
            headers={"Authorization": f"Bearer {token}"},
        )


async def _get_collab(node):
    token = node.app.state.auth.get_local_token()
    async with node.client() as cl:
        return await cl.get(
            "/api/settings/collaboration",
            headers={"Authorization": f"Bearer {token}"},
        )


async def _post(node, path, json_body, token):
    async with node.client() as cl:
        return await cl.post(
            path, json=json_body, headers={"Authorization": f"Bearer {token}"},
        )


async def _inbox(sender, host, token, kind="handshake"):
    env = sender.sign_envelope(to=host.contact_id, kind=kind, body={"hello": "x"})
    return await _post(host, "/api/peer/inbox", {"envelope": env}, token)


def _write_switch(node, data):
    """Write the switch file directly, the shape GET/PUT read and write."""
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
class TestDefaultAndControl:
    async def test_default_state_passes_and_reads_enabled(self, nodes):
        a, b, _ = nodes
        token = await pin_contact(b, a)
        resp = await _inbox(a, b, token)
        assert resp.status_code == 200, resp.text
        got = await _get_collab(b)
        assert got.status_code == 200, got.text
        assert got.json() == {"enabled": True, "paused_contacts": []}

    async def test_reenabling_restores_service(self, nodes):
        a, b, _ = nodes
        token = await pin_contact(b, a)
        assert (await _put_collab(b, {"enabled": False})).status_code == 200
        assert (await _inbox(a, b, token)).status_code == 503
        assert (await _put_collab(b, {"enabled": True})).status_code == 200
        resp = await _inbox(a, b, token)
        assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
class TestDisabled:
    async def test_inbox_valid_request_is_503(self, nodes):
        a, b, _ = nodes
        token = await pin_contact(b, a)
        _write_switch(b, {"enabled": False})
        resp = await _inbox(a, b, token)
        assert resp.status_code == 503, resp.text
        assert resp.json()["detail"] == "collaboration disabled"

    async def test_invalid_bearer_is_503_not_401(self, nodes):
        a, b, _ = nodes
        await pin_contact(b, a)
        _write_switch(b, {"enabled": False})
        resp = await _inbox(a, b, "not-a-real-token")
        assert resp.status_code == 503, resp.text

    async def test_chat_is_503(self, nodes):
        a, b, _ = nodes
        token = await pin_contact(b, a)
        _write_switch(b, {"enabled": False})
        env = a.sign_envelope(to=b.contact_id, kind="chat", body={"text": "hi"})
        resp = await _post(b, "/api/peer/chat", {"envelope": env}, token)
        assert resp.status_code == 503, resp.text

    async def test_ack_is_503(self, nodes):
        a, b, _ = nodes
        token = await pin_contact(b, a)
        _write_switch(b, {"enabled": False})
        resp = await _post(
            b, "/api/peer/ack",
            {"envelope_id": "n" * 32, "contact_id": a.contact_id}, token,
        )
        assert resp.status_code == 503, resp.text

    async def test_disabled_does_no_nonce_work(self, nodes, monkeypatch):
        a, b, _ = nodes
        token = await pin_contact(b, a)
        _write_switch(b, {"enabled": False})
        calls = _count_record_nonce(monkeypatch, b)
        assert (await _inbox(a, b, token)).status_code == 503
        assert calls == []


@pytest.mark.asyncio
class TestPausedContact:
    async def test_paused_contact_is_403_other_contact_is_200(self, nodes):
        a, b, c = nodes
        token_a = await pin_contact(b, a)
        token_c = await pin_contact(b, c)
        _write_switch(b, {"enabled": True, "paused_contacts": [a.contact_id]})
        paused = await _inbox(a, b, token_a)
        assert paused.status_code == 403, paused.text
        assert paused.json()["detail"] == "contact paused"
        active = await _inbox(c, b, token_c)
        assert active.status_code == 200, active.text

    async def test_paused_contact_bad_signature_is_403_and_burns_no_nonce(
        self, nodes, monkeypatch,
    ):
        a, b, _ = nodes
        token = await pin_contact(b, a)
        _write_switch(b, {"enabled": True, "paused_contacts": [a.contact_id]})
        calls = _count_record_nonce(monkeypatch, b)
        # Signed with B's key under A's id: a bad signature.
        env = b.sign_envelope(to=b.contact_id, kind="handshake", from_id=a.contact_id)
        resp = await _post(b, "/api/peer/inbox", {"envelope": env}, token)
        assert resp.status_code == 403, resp.text
        assert resp.json()["detail"] == "contact paused"
        assert calls == []

    async def test_unpause_restores_service(self, nodes):
        a, b, _ = nodes
        token = await pin_contact(b, a)
        assert (await _put_collab(b, {"paused_contacts": [a.contact_id]})).status_code == 200
        assert (await _inbox(a, b, token)).status_code == 403
        assert (await _put_collab(b, {"paused_contacts": []})).status_code == 200
        resp = await _inbox(a, b, token)
        assert resp.status_code == 200, resp.text
        # Pausing deleted nothing: the contact row and peer link are intact.
        store = b.app.state.contacts_store
        assert await store.get_contact(a.contact_id) is not None
        assert await store.get_peer_link(a.contact_id) is not None


@pytest.mark.asyncio
class TestSettingsRoute:
    async def test_put_normalises_and_roundtrips(self, nodes):
        _, b, _ = nodes
        resp = await _put_collab(
            b, {"enabled": False, "paused_contacts": ["  HUB:ABCDEF ", "hub:abcdef"]},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"enabled": False, "paused_contacts": ["hub:abcdef"]}
        got = await _get_collab(b)
        assert got.json() == {"enabled": False, "paused_contacts": ["hub:abcdef"]}

    async def test_put_partial_keeps_other_field(self, nodes):
        _, b, _ = nodes
        await _put_collab(b, {"paused_contacts": ["hub:ab"]})
        await _put_collab(b, {"enabled": False})
        got = await _get_collab(b)
        assert got.json() == {"enabled": False, "paused_contacts": ["hub:ab"]}

    @pytest.mark.parametrize("body", [
        {"enabled": "no"},
        {"paused_contacts": "hub:ab"},
        {"paused_contacts": [1]},
        {"paused_contacts": ["alice"]},
        ["enabled"],
    ])
    async def test_put_wrong_types_400(self, nodes, body):
        _, b, _ = nodes
        resp = await _put_collab(b, body)
        assert resp.status_code == 400, resp.text

    async def test_corrupt_file_reads_disabled(self, nodes):
        a, b, _ = nodes
        token = await pin_contact(b, a)
        (b.data_dir / "collaboration.json").write_text("{not json")
        got = await _get_collab(b)
        assert got.json()["enabled"] is False
        assert (await _inbox(a, b, token)).status_code == 503


@pytest.mark.asyncio
async def test_non_admin_session_cannot_put(client, app):
    auth_mgr = app.state.auth
    invite_code = auth_mgr.add_user_invite("member", "admin")
    auth_mgr.complete_invite("member", invite_code, "Test Member", "", "membercollabpass123")
    member = auth_mgr.find_user("member")
    token = auth_mgr.create_session(user_id=member["id"], long_lived=True)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test",
        cookies={"taos_session": token},
    ) as member_client:
        resp = await member_client.put(
            "/api/settings/collaboration", json={"enabled": False},
        )
        get_resp = await member_client.get("/api/settings/collaboration")
    assert resp.status_code in (401, 403), resp.text
    assert get_resp.status_code in (401, 403), get_resp.text
    assert not (app.state.data_dir / "collaboration.json").exists()


async def _create_project(client, slug="collab-switch-proj") -> str:
    resp = await client.post("/api/projects", json={"name": "Collab Switch", "slug": slug})
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


@pytest.mark.asyncio
async def test_collab_invite_mint_refuses_while_disabled(client, app):
    from tinyagentos.collab_settings import CollabSettings, write_collab_settings

    pid = await _create_project(client)
    write_collab_settings(app.state.data_dir, CollabSettings(enabled=False))
    resp = await client.post(
        f"/api/projects/{pid}/invites/collab", json={"contact_id": "hub:abcdef"},
    )
    assert resp.status_code == 503, resp.text
    assert resp.json()["error"] == "collaboration disabled"
    assert await app.state.project_invites.list_pending_collab_for_contact("hub:abcdef") == []


@pytest.mark.asyncio
async def test_collab_invite_mint_refuses_paused_contact(client, app):
    from tinyagentos.collab_settings import CollabSettings, write_collab_settings

    pid = await _create_project(client)
    write_collab_settings(
        app.state.data_dir, CollabSettings(enabled=True, paused_contacts=["hub:abcdef"]),
    )
    resp = await client.post(
        f"/api/projects/{pid}/invites/collab", json={"contact_id": "hub:abcdef"},
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["error"] == "contact paused"
    assert await app.state.project_invites.list_pending_collab_for_contact("hub:abcdef") == []

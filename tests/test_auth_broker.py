"""Sign-in broker (tsk-diqs7d): an agent posts a Claude sign-in link, the user
completes it from a taOS card, and the long-lived token lands in that agent's
Secrets.

Red-first: before the broker exists every route here 404s.
"""
from __future__ import annotations

import pytest
import pytest_asyncio

from tinyagentos.agent_registry_store import (
    AgentRegistryStore,
    load_or_create_signing_keypair,
    mint_registry_token,
)

SIGNIN_URL = "https://claude.ai/oauth/authorize?code=true&client_id=abc&state=xyz"


@pytest_asyncio.fixture(autouse=True)
async def _auth_broker_store(app, tmp_data_dir):
    # The client fixture does not run the app lifespan, so give the app its
    # broker store here (app.py wires the real one at startup).
    from tinyagentos.auth_broker import AuthBrokerStore

    store = AuthBrokerStore(tmp_data_dir / "auth_broker.db")
    await store.init()
    app.state.auth_broker = store
    yield store
    await store.close()
BASE = "/api/auth-broker/requests"


def _bearer(app, agent_name: str) -> dict:
    return {"Authorization": f"Bearer {app.state.auth.mint_agent_local_token(agent_name)}"}


async def _create(client, app, agent_name="haiku-agent", url=SIGNIN_URL):
    return await client.post(
        BASE,
        json={"kind": "claude-subscription", "url": url, "prompt": "Sign in to Claude"},
        headers=_bearer(app, agent_name),
    )


async def _created_id(client, app, agent_name="haiku-agent") -> str:
    resp = await _create(client, app, agent_name)
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/oauth",
        "http://claude.ai/oauth/authorize",
        "https://claude.ai.evil.example/oauth",
        "https://user@claude.ai/oauth",
        "https://claude.ai:8443/oauth",
        "javascript:alert(1)",
    ],
)
async def test_create_refuses_url_off_allowlist(client, app, url):
    resp = await _create(client, app, url=url)
    assert resp.status_code == 422, resp.text


@pytest.mark.asyncio
async def test_create_accepts_allowlisted_hosts(client, app):
    for url in (SIGNIN_URL, "https://console.anthropic.com/oauth/authorize?x=1"):
        resp = await _create(client, app, url=url)
        assert resp.status_code == 200, resp.text
        assert resp.json()["id"]


@pytest.mark.asyncio
async def test_create_needs_an_agent_identity(client, app):
    # The test client carries an admin SESSION cookie and no agent token.
    resp = await client.post(
        BASE, json={"kind": "claude-subscription", "url": SIGNIN_URL, "prompt": ""}
    )
    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_create_raises_a_signin_notification(client, app):
    rid = await _created_id(client, app)
    rows = await app.state.notifications.list(limit=20)
    hits = [r for r in rows if r.get("source") == "auth_broker"]
    assert hits, rows
    data = hits[0]["data"]
    assert data["request_id"] == rid
    assert data["url"] == SIGNIN_URL
    assert data["host"] == "claude.ai"
    assert data["agent"] == "haiku-agent"


@pytest.mark.asyncio
async def test_agent_token_on_code_route_is_403(client, app):
    rid = await _created_id(client, app)
    resp = await client.post(
        f"{BASE}/{rid}/code", json={"code": "abc#123"}, headers=_bearer(app, "haiku-agent")
    )
    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_another_agent_cannot_read_the_code(client, app):
    rid = await _created_id(client, app)
    assert (await client.post(f"{BASE}/{rid}/code", json={"code": "abc#123"})).status_code == 200
    resp = await client.get(f"{BASE}/{rid}", headers=_bearer(app, "other-agent"))
    assert resp.status_code == 404, resp.text
    # ...and the requester still gets it afterwards (the probe did not consume it).
    mine = await client.get(f"{BASE}/{rid}", headers=_bearer(app, "haiku-agent"))
    assert mine.json().get("code") == "abc#123"


@pytest.mark.asyncio
async def test_code_is_readable_once(client, app):
    rid = await _created_id(client, app)
    hdr = _bearer(app, "haiku-agent")
    pending = await client.get(f"{BASE}/{rid}", headers=hdr)
    assert pending.status_code == 200
    assert pending.json()["status"] == "pending"
    assert "code" not in pending.json()

    assert (await client.post(f"{BASE}/{rid}/code", json={"code": "abc#123"})).status_code == 200
    first = await client.get(f"{BASE}/{rid}", headers=hdr)
    assert first.json()["code"] == "abc#123"
    second = await client.get(f"{BASE}/{rid}", headers=hdr)
    assert second.status_code == 200
    assert "code" not in second.json()
    assert second.json()["status"] == "code_read"


@pytest.mark.asyncio
async def test_code_cannot_be_overwritten(client, app):
    rid = await _created_id(client, app)
    assert (await client.post(f"{BASE}/{rid}/code", json={"code": "first"})).status_code == 200
    again = await client.post(f"{BASE}/{rid}/code", json={"code": "second"})
    assert again.status_code == 409, again.text


@pytest.mark.asyncio
async def test_request_expires_after_ten_minutes(client, app, monkeypatch):
    import tinyagentos.auth_broker as broker

    t0 = 1_800_000_000.0
    monkeypatch.setattr(broker, "_now", lambda: t0)
    rid = await _created_id(client, app)

    monkeypatch.setattr(broker, "_now", lambda: t0 + 601)
    late = await client.post(f"{BASE}/{rid}/code", json={"code": "abc#123"})
    assert late.status_code == 410, late.text
    status = await client.get(f"{BASE}/{rid}", headers=_bearer(app, "haiku-agent"))
    assert status.json()["status"] == "expired"
    assert "code" not in status.json()


@pytest.mark.asyncio
async def test_token_lands_in_the_requesters_secret_only(client, app):
    rid = await _created_id(client, app)
    hdr = _bearer(app, "haiku-agent")
    assert (await client.post(f"{BASE}/{rid}/code", json={"code": "abc#123"})).status_code == 200
    assert (await client.get(f"{BASE}/{rid}", headers=hdr)).json()["code"] == "abc#123"

    # Another agent cannot complete someone else's request.
    other = await client.post(
        f"{BASE}/{rid}/token", json={"token": "sk-ant-oat01-stolen"},
        headers=_bearer(app, "other-agent"),
    )
    assert other.status_code == 404, other.text

    resp = await client.post(
        f"{BASE}/{rid}/token", json={"token": "sk-ant-oat01-secret-value"}, headers=hdr
    )
    assert resp.status_code == 200, resp.text
    assert "sk-ant-oat01-secret-value" not in resp.text
    name = resp.json()["secret_name"]
    assert name == "claude_code_oauth_token:haiku-agent"

    row = await app.state.secrets.get(name)
    assert row is not None
    assert row["value"] == "sk-ant-oat01-secret-value"
    assert row["agents"] == ["haiku-agent"]
    assert await app.state.secrets.get_agent_secrets("other-agent") == []

    # One token per request: a replay is refused.
    replay = await client.post(
        f"{BASE}/{rid}/token", json={"token": "sk-ant-oat01-again"}, headers=hdr
    )
    assert replay.status_code == 409, replay.text


@pytest.mark.asyncio
async def test_token_refused_before_the_code_was_read(client, app):
    rid = await _created_id(client, app)
    resp = await client.post(
        f"{BASE}/{rid}/token", json={"token": "sk-ant-oat01-early"},
        headers=_bearer(app, "haiku-agent"),
    )
    assert resp.status_code == 409, resp.text
    assert await app.state.secrets.get("claude_code_oauth_token:haiku-agent") is None


@pytest.mark.asyncio
async def test_session_user_cannot_post_the_token(client, app):
    rid = await _created_id(client, app)
    assert (await client.post(f"{BASE}/{rid}/code", json={"code": "abc#123"})).status_code == 200
    resp = await client.post(f"{BASE}/{rid}/token", json={"token": "sk-ant-oat01-x"})
    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_native_taos_agent_token_lands_in_the_harness_secret(
    client, app, monkeypatch, tmp_path
):
    """The root taOS Agent (registry JWT, origin taos-native) stores its token
    under the name the claude_code harness reads (claude_code_runtime)."""
    registry = AgentRegistryStore(tmp_path / "reg.db")
    await registry.init()
    priv, pub = load_or_create_signing_keypair(tmp_path / "keys")
    monkeypatch.setattr(app.state, "agent_registry", registry)
    monkeypatch.setattr(app.state, "agent_registry_keypair", (priv, pub))
    admin = app.state.auth.find_user("admin")
    rec = await registry.register(
        framework="taos-native", display_name="taOS Agent",
        user_id=admin["id"], origin="taos-native", handle="@taos-agent",
        allow_reserved=True,
    )
    cid = rec["canonical_id"]
    hdr = {"Authorization": f"Bearer {mint_registry_token(cid, priv, user_id=admin['id'])}"}
    try:
        created = await client.post(
            BASE, json={"kind": "claude-subscription", "url": SIGNIN_URL, "prompt": ""},
            headers=hdr,
        )
        assert created.status_code == 200, created.text
        rid = created.json()["id"]
        assert (await client.post(f"{BASE}/{rid}/code", json={"code": "c"})).status_code == 200
        assert (await client.get(f"{BASE}/{rid}", headers=hdr)).json()["code"] == "c"
        done = await client.post(f"{BASE}/{rid}/token", json={"token": "sk-native"}, headers=hdr)
        assert done.status_code == 200, done.text
        assert done.json()["secret_name"] == "claude_code_oauth_token"
        row = await app.state.secrets.get("claude_code_oauth_token")
        assert row["value"] == "sk-native"
        assert row["agents"] == [cid]
    finally:
        await registry.close()


@pytest.mark.asyncio
async def test_card_for_an_unowned_agent_goes_to_admins_never_a_broadcast(client, app):
    # haiku-agent is a bound local-token agent with no registry row, so it has
    # no owner. A user_id=None notification is a broadcast to every account.
    rid = await _created_id(client, app)
    rows = await app.state.notifications.list(limit=50)
    hits = [r for r in rows if r.get("source") == "auth_broker" and r["data"]["request_id"] == rid]
    assert hits, rows
    assert all(r["user_id"] is not None for r in hits), hits
    admin_ids = {u["id"] for u in app.state.auth.list_users() if u.get("is_admin")}
    assert {r["user_id"] for r in hits} == admin_ids


@pytest.mark.asyncio
async def test_token_post_claims_the_request_before_writing_the_secret(client, app):
    rid = await _created_id(client, app)
    hdr = _bearer(app, "haiku-agent")
    assert (await client.post(f"{BASE}/{rid}/code", json={"code": "abc#123"})).status_code == 200
    assert (await client.get(f"{BASE}/{rid}", headers=hdr)).json()["code"] == "abc#123"

    secrets_store = app.state.secrets
    real_add = secrets_store.add
    overlapping = {}

    async def add_with_an_overlapping_post(*args, **kwargs):
        # A second token post lands while the first one is writing the secret.
        if not overlapping:
            overlapping["started"] = True
            overlapping["resp"] = await client.post(
                f"{BASE}/{rid}/token", json={"token": "sk-ant-oat01-second"}, headers=hdr
            )
        return await real_add(*args, **kwargs)

    secrets_store.add = add_with_an_overlapping_post
    try:
        first = await client.post(
            f"{BASE}/{rid}/token", json={"token": "sk-ant-oat01-first"}, headers=hdr
        )
    finally:
        secrets_store.add = real_add
    assert first.status_code == 200, first.text
    assert overlapping["resp"].status_code == 409, overlapping["resp"].text
    row = await secrets_store.get("claude_code_oauth_token:haiku-agent")
    assert row["value"] == "sk-ant-oat01-first"


@pytest.mark.asyncio
async def test_failed_secret_write_leaves_the_request_retryable(client, app):
    rid = await _created_id(client, app)
    hdr = _bearer(app, "haiku-agent")
    assert (await client.post(f"{BASE}/{rid}/code", json={"code": "abc#123"})).status_code == 200
    assert (await client.get(f"{BASE}/{rid}", headers=hdr)).json()["code"] == "abc#123"

    secrets_store = app.state.secrets
    real_add = secrets_store.add

    async def failing_add(*args, **kwargs):
        raise RuntimeError("secrets db unavailable")

    secrets_store.add = failing_add
    try:
        with pytest.raises(RuntimeError):
            await client.post(f"{BASE}/{rid}/token", json={"token": "sk-ant-oat01-x"}, headers=hdr)
    finally:
        secrets_store.add = real_add
    retry = await client.post(f"{BASE}/{rid}/token", json={"token": "sk-ant-oat01-x"}, headers=hdr)
    assert retry.status_code == 200, retry.text

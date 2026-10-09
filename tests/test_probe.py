from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from tinyagentos.agent_registry_store import mint_registry_token
from taos_test_csrf import csrf_event_hooks


async def _make_agent_token(app, *, scopes=("chat_session",), handle="@chat-agent"):
    registry = app.state.agent_registry
    grants = app.state.agent_grants
    if registry._db is None:
        await registry.init()
    if grants._db is None:
        await grants.init()
    priv, _pub = app.state.agent_registry_keypair
    rec = await registry.register(
        framework="taosmd", display_name="Chat Agent", origin="taos-deployed", handle=handle,
    )
    cid = rec["canonical_id"]
    for scope in scopes:
        await grants.add_grant(cid, scope)
    token = mint_registry_token(cid, priv, user_id="u", framework="taosmd")
    return cid, token


def _bare(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest_asyncio.fixture
async def chat_client(app, tmp_data_dir):
    for attr in ("agent_registry", "agent_grants", "metrics"):
        store = getattr(app.state, attr)
        if store._db is None:
            await store.init()
    app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
    record = app.state.auth.find_user("admin")
    uid = record["id"] if record else ""
    token = app.state.auth.create_session(user_id=uid, long_lived=True)
    app.state._startup_complete = True
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies={"taos_session": token},
        event_hooks=csrf_event_hooks(),
    ) as c:
        c._app = app
        yield c
    for attr in ("agent_registry", "agent_grants", "metrics"):
        store = getattr(app.state, attr)
        if store._db is not None:
            await store.close()


@pytest.mark.asyncio
async def test_probe(chat_client):
    app = chat_client._app
    _cid, token = await _make_agent_token(app, scopes=("chat_session",))

    # no bearer
    async with _bare(app) as bare:
        r1 = await bare.get("/api/agents/self/chat/events")
        r2 = await bare.post("/api/agents/self/chat/reply", json={"kind": "final"})
    print("NO-BEARER events:", r1.status_code, "reply:", r2.status_code)

    # bearer without chat_session
    _cid2, token2 = await _make_agent_token(app, scopes=("a2a_receive",), handle="@noscope")
    async with _bare(app) as bare:
        r3 = await bare.get("/api/agents/self/chat/events", headers={"Authorization": f"Bearer {token2}"})
        r4 = await bare.post("/api/agents/self/chat/reply", json={"kind": "final"}, headers={"Authorization": f"Bearer {token2}"})
    print("NO-SCOPE events:", r3.status_code, "reply:", r4.status_code)

    # bearer with chat_session
    async with _bare(app) as bare:
        r5 = await bare.get("/api/agents/self/chat/events", headers={"Authorization": f"Bearer {token}"})
    print("WITH-SCOPE events:", r5.status_code, r5.headers.get("content-type"))
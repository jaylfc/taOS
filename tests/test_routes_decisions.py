import asyncio
import json
import time

import pytest

from tinyagentos.routes.decisions import SERVER_RAISED_KEY


@pytest.mark.asyncio
async def test_post_list_get_answer_flow(client):
    resp = await client.post("/api/decisions", json={
        "from_agent": "@taOS-dev",
        "question": "Which engine?",
        "type": "single_select",
        "options": [{"label": "Excalidraw", "value": "excalidraw", "recommended": True, "rationale": "MIT"},
                    {"label": "Konva", "value": "konva"}],
        "context": "canvas replacement",
        "project_id": "prj-x",
    })
    assert resp.status_code == 200
    d = resp.json()
    assert d["id"].startswith("dec-") and d["status"] == "pending"

    resp = await client.get("/api/decisions?status=pending")
    assert resp.status_code == 200
    assert any(x["id"] == d["id"] for x in resp.json()["items"])

    resp = await client.get(f"/api/decisions/{d['id']}")
    assert resp.status_code == 200
    assert resp.json()["question"] == "Which engine?"

    resp = await client.post(f"/api/decisions/{d['id']}/answer", json={"value": "excalidraw"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "answered"
    assert resp.json()["answer"]["value"] == "excalidraw"

    # cannot answer twice
    resp = await client.post(f"/api/decisions/{d['id']}/answer", json={"value": "konva"})
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_select_requires_options(client):
    resp = await client.post("/api/decisions", json={
        "from_agent": "@a", "question": "q", "type": "single_select", "options": [],
    })
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_free_text_and_approve_deny_need_no_options(client):
    for t in ("free_text", "approve_deny"):
        resp = await client.post("/api/decisions", json={"from_agent": "@a", "question": "q", "type": t})
        assert resp.status_code == 200, t


@pytest.mark.asyncio
async def test_invalid_type_400(client):
    resp = await client.post("/api/decisions", json={"from_agent": "@a", "question": "q", "type": "nope"})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_get_unknown_404(client):
    resp = await client.get("/api/decisions/dec-missing")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_answer_must_match_options(client):
    resp = await client.post("/api/decisions", json={
        "from_agent": "@a", "question": "q", "type": "single_select",
        "options": [{"label": "A", "value": "a"}, {"label": "B", "value": "b"}],
    })
    d = resp.json()
    # A value outside the declared options is rejected.
    resp = await client.post(f"/api/decisions/{d['id']}/answer", json={"value": "zzz"})
    assert resp.status_code == 400
    # The decision is still answerable with a valid option.
    resp = await client.post(f"/api/decisions/{d['id']}/answer", json={"value": "a"})
    assert resp.status_code == 200
    assert resp.json()["answer"]["value"] == "a"


@pytest.mark.asyncio
async def test_multi_select_answer_must_be_subset(client):
    resp = await client.post("/api/decisions", json={
        "from_agent": "@a", "question": "q", "type": "multi_select",
        "options": [{"label": "A", "value": "a"}, {"label": "B", "value": "b"}],
    })
    d = resp.json()
    resp = await client.post(f"/api/decisions/{d['id']}/answer", json={"value": ["a", "nope"]})
    assert resp.status_code == 400
    resp = await client.post(f"/api/decisions/{d['id']}/answer", json={"value": ["a", "b"]})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_select_options_without_value_default_to_label(client):
    # An agent may declare options with only a label (value is optional in the
    # API). Every option must still get a distinct, non-null value, otherwise
    # the inbox cannot tell them apart (a multi_select would check all at once)
    # and answer validation silently no-ops on an empty valid set.
    resp = await client.post("/api/decisions", json={
        "from_agent": "@a", "question": "pick apps", "type": "multi_select",
        "options": [{"label": "Images"}, {"label": "Observatory"}, {"label": "Decisions"}],
    })
    assert resp.status_code == 200
    d = resp.json()
    values = [o["value"] for o in d["options"]]
    assert values == ["Images", "Observatory", "Decisions"]
    # Answer validation now has a populated valid set keyed on those values.
    resp = await client.post(f"/api/decisions/{d['id']}/answer", json={"value": ["Images", "Decisions"]})
    assert resp.status_code == 200
    resp2 = await client.post("/api/decisions", json={
        "from_agent": "@a", "question": "pick one", "type": "single_select",
        "options": [{"label": "A"}, {"label": "B"}],
    })
    d2 = resp2.json()
    # A label that was not declared is still rejected.
    bad = await client.post(f"/api/decisions/{d2['id']}/answer", json={"value": "C"})
    assert bad.status_code == 400


@pytest.mark.asyncio
async def test_duplicate_labels_get_distinct_values(client):
    # Two value-less options sharing a label must NOT collapse to one identity,
    # otherwise the multi_select would check both at once again. Collisions are
    # disambiguated with a suffix so every option keeps a distinct value.
    resp = await client.post("/api/decisions", json={
        "from_agent": "@a", "question": "pick", "type": "multi_select",
        "options": [{"label": "Other"}, {"label": "Other"}, {"label": "Other"}],
    })
    assert resp.status_code == 200
    d = resp.json()
    values = [o["value"] for o in d["options"]]
    assert len(set(values)) == 3, f"values must be distinct, got {values}"
    # Selecting one colliding option does not implicitly select the others.
    one = values[0]
    resp = await client.post(f"/api/decisions/{d['id']}/answer", json={"value": [one]})
    assert resp.status_code == 200
    assert resp.json()["answer"]["value"] == [one]


@pytest.mark.asyncio
async def test_metadata_echoed_on_create(client):
    resp = await client.post("/api/decisions", json={
        "from_agent": "@a", "question": "q", "type": "free_text",
        "metadata": {"kind": "app_grant", "app_id": "x", "capabilities": ["app.net"]},
    })
    assert resp.status_code == 200
    assert resp.json()["metadata"]["app_id"] == "x"


@pytest.mark.asyncio
async def test_app_grant_answer_writes_grants(client):
    # An app-grant consent Decision (metadata.kind == app_grant): answering the
    # multi_select with a subset writes granted for the picked caps and denied
    # for the rest to the app_grants ledger.
    app = client._transport.app
    user_id = _admin_uid(app)
    d = await app.state.decision_store.create(
        from_agent="@taos-app-install",
        question="stream-chat permissions",
        type="multi_select",
        options=[{"label": "Net", "value": "app.net"},
                 {"label": "Memory", "value": "app.memory"}],
        user_id=user_id,
        metadata={SERVER_RAISED_KEY: True, "kind": "app_grant", "app_id": "stream-chat",
                  "capabilities": ["app.net", "app.memory"]},
    )
    resp = await client.post(f"/api/decisions/{d['id']}/answer", json={"value": ["app.net"]})
    assert resp.status_code == 200
    granted = await app.state.app_grants.granted_capabilities(user_id, "stream-chat")
    assert granted == {"app.net"}
    grants = {g["capability"]: g["decision"]
              for g in await app.state.app_grants.list_grants(user_id, "stream-chat")}
    assert grants == {"app.net": "granted", "app.memory": "denied"}


@pytest.mark.asyncio
async def test_execution_gate_approve_writes_grant(client):
    # An execution-gate Decision (agent governance #160 slice 1, metadata.kind
    # == execution_gate): approving it writes a live execution grant for the
    # (agent, action_class) pair so the agent's retry passes the policy gate.
    app = client._transport.app
    d = await app.state.decision_store.create(
        from_agent="agent-a",
        question="Agent agent-a wants to run code_exec (code-exec)",
        type="approve_deny", priority="blocking",
        user_id=_admin_uid(app),
        metadata={SERVER_RAISED_KEY: True, "kind": "execution_gate", "agent_name": "agent-a",
                  "action_class": "code-exec", "tool": "code_exec"},
    )
    resp = await client.post(f"/api/decisions/{d['id']}/answer", json={"value": "approve"})
    assert resp.status_code == 200
    assert await app.state.execution_policies.has_live_grant("agent-a", "code-exec") is True


@pytest.mark.asyncio
async def test_execution_gate_deny_writes_no_grant(client):
    app = client._transport.app
    d = await app.state.decision_store.create(
        from_agent="agent-a",
        question="Agent agent-a wants to run code_exec (code-exec)",
        type="approve_deny", priority="blocking",
        user_id=_admin_uid(app),
        metadata={SERVER_RAISED_KEY: True, "kind": "execution_gate", "agent_name": "agent-a",
                  "action_class": "code-exec", "tool": "code_exec"},
    )
    resp = await client.post(f"/api/decisions/{d['id']}/answer", json={"value": "deny"})
    assert resp.status_code == 200
    assert await app.state.execution_policies.has_live_grant("agent-a", "code-exec") is False


@pytest.mark.asyncio
async def test_execution_gate_grant_scoped_to_its_own_agent_and_class(client):
    app = client._transport.app
    d = await app.state.decision_store.create(
        from_agent="agent-a",
        question="q", type="approve_deny", priority="blocking",
        user_id=_admin_uid(app),
        metadata={SERVER_RAISED_KEY: True, "kind": "execution_gate", "agent_name": "agent-a",
                  "action_class": "code-exec", "tool": "code_exec"},
    )
    await client.post(f"/api/decisions/{d['id']}/answer", json={"value": "approve"})
    # Neither a different agent nor a different action class picks up the grant.
    assert await app.state.execution_policies.has_live_grant("agent-b", "code-exec") is False
    assert await app.state.execution_policies.has_live_grant("agent-a", "external-network") is False


@pytest.mark.asyncio
async def test_app_grant_payload_builder():
    from tinyagentos.routes.app_permissions import app_grant_decision_payload
    payload = app_grant_decision_payload("stream-chat", ["app.net", "app.memory"])
    assert payload["type"] == "multi_select"
    assert payload["metadata"] == {SERVER_RAISED_KEY: True,
                                   "kind": "app_grant", "app_id": "stream-chat",
                                   "capabilities": ["app.net", "app.memory"]}
    assert [o["value"] for o in payload["options"]] == ["app.net", "app.memory"]
    assert payload["options"][0]["label"]


@pytest.mark.asyncio
async def test_answer_routes_back_to_bus_agent(client, monkeypatch):
    import tinyagentos.routes.decisions as dmod

    posted = {}

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None):
            posted["url"] = url
            posted["json"] = json
            return None

    monkeypatch.setattr(dmod.httpx, "AsyncClient", _FakeClient)

    resp = await client.post("/api/decisions", json={
        "from_agent": "@taOSmd-dev", "question": "Use arctic?", "type": "approve_deny",
    })
    d = resp.json()
    resp = await client.post(f"/api/decisions/{d['id']}/answer", json={"value": "approve"})
    assert resp.status_code == 200
    # The answer was routed back to the asking agent on the 'decisions' thread.
    assert posted["url"].endswith("/a2a/send")
    assert posted["json"]["thread"] == "decisions"
    assert "@taOSmd-dev" in posted["json"]["body"]
    assert "approve" in posted["json"]["body"]


@pytest.mark.asyncio
async def test_answer_succeeds_when_bus_unreachable(client, monkeypatch):
    import tinyagentos.routes.decisions as dmod

    class _BrokenClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            raise RuntimeError("bus down")

    monkeypatch.setattr(dmod.httpx, "AsyncClient", _BrokenClient)

    resp = await client.post("/api/decisions", json={
        "from_agent": "@taOSmd-dev", "question": "q", "type": "approve_deny",
    })
    d = resp.json()
    # Best-effort delivery: a bus failure must not fail the answer.
    resp = await client.post(f"/api/decisions/{d['id']}/answer", json={"value": "deny"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "answered"


@pytest.mark.asyncio
async def test_answer_no_route_for_non_agent_sender(client, monkeypatch):
    import tinyagentos.routes.decisions as dmod

    calls = {"n": 0}

    class _CountingClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            calls["n"] += 1
            return None

    monkeypatch.setattr(dmod.httpx, "AsyncClient", _CountingClient)

    # from_agent without a leading @ is not an agent handle -> no bus post.
    resp = await client.post("/api/decisions", json={
        "from_agent": "system", "question": "q", "type": "free_text",
    })
    d = resp.json()
    resp = await client.post(f"/api/decisions/{d['id']}/answer", json={"value": "ok"})
    assert resp.status_code == 200
    assert calls["n"] == 0


@pytest.mark.asyncio
async def test_create_with_parent_supersedes_it(client):
    # Original decision.
    r = await client.post("/api/decisions", json={
        "from_agent": "@taOS-dev", "question": "Canvas engine?", "type": "single_select",
        "options": [{"label": "Konva", "value": "konva"}], "project_id": "prj-x",
    })
    old = r.json()
    # A replacement that revisits it going forward.
    r = await client.post("/api/decisions", json={
        "from_agent": "@taOS-dev", "question": "Canvas engine (revisited)?", "type": "single_select",
        "options": [{"label": "Excalidraw", "value": "excalidraw"}], "project_id": "prj-x",
        "parent_decision_id": old["id"],
    })
    new = r.json()
    assert r.status_code == 200
    assert new["parent_decision_id"] == old["id"]
    # The old decision is now superseded.
    r = await client.get(f"/api/decisions/{old['id']}")
    assert r.json()["status"] == "superseded"


@pytest.mark.asyncio
async def test_create_with_unknown_parent_rejected(client):
    r = await client.post("/api/decisions", json={
        "from_agent": "@taOS-dev", "question": "q", "type": "free_text",
        "parent_decision_id": "dec-missing",
    })
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_history_returns_lineage_oldest_first(client):
    ids = []
    parent = None
    for i in range(3):
        body = {"from_agent": "@taOS-dev", "question": f"q{i}", "type": "free_text"}
        if parent:
            body["parent_decision_id"] = parent
        r = await client.post("/api/decisions", json=body)
        parent = r.json()["id"]
        ids.append(parent)
    # History of the newest walks back through both ancestors.
    r = await client.get(f"/api/decisions/{ids[-1]}/history")
    assert r.status_code == 200
    chain = [d["id"] for d in r.json()["items"]]
    assert chain == ids  # oldest first


@pytest.mark.asyncio
async def test_human_answer_rejects_mirrored_from_chat_source(client):
    """A human answer with source=mirrored_from_chat must be rejected as
    spoofing the audit trail. The human path only records in_app."""
    resp = await client.post("/api/decisions", json={
        "from_agent": "@a", "question": "q", "type": "free_text",
    })
    d = resp.json()
    resp = await client.post(
        f"/api/decisions/{d['id']}/answer",
        json={"value": "ok", "source": "mirrored_from_chat"},
    )
    assert resp.status_code == 400
    assert "mirrored_from_chat" in resp.json()["error"]


@pytest.mark.asyncio
async def test_human_answer_source_field_in_response(client):
    """A normal human answer includes source and answered_by in its answer JSON."""
    resp = await client.post("/api/decisions", json={
        "from_agent": "@a", "question": "q", "type": "free_text",
    })
    d = resp.json()
    resp = await client.post(
        f"/api/decisions/{d['id']}/answer",
        json={"value": "ok", "answered_by": "Jay"},
    )
    assert resp.status_code == 200
    ans = resp.json()["answer"]
    assert ans["source"] == "in_app"
    assert ans["answered_by"] == "Jay"


@pytest.mark.asyncio
async def test_single_select_handles_non_hashable_value(client):
    """A list or dict submitted as a single_select answer must return 400,
    not 500, by catching the TypeError from set membership."""
    resp = await client.post("/api/decisions", json={
        "from_agent": "@a", "question": "q", "type": "single_select",
        "options": [{"label": "A", "value": "a"}],
    })
    d = resp.json()
    # A list as the value is non-hashable and must not 500.
    resp = await client.post(
        f"/api/decisions/{d['id']}/answer",
        json={"value": ["a"]},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_multi_select_handles_non_iterable_value(client):
    """A non-iterable value for a multi_select answer must 400."""
    resp = await client.post("/api/decisions", json={
        "from_agent": "@a", "question": "q", "type": "multi_select",
        "options": [{"label": "A", "value": "a"}],
    })
    d = resp.json()
    # An integer (not a list) should 400 for multi_select.
    resp = await client.post(
        f"/api/decisions/{d['id']}/answer",
        json={"value": 42},
    )
    assert resp.status_code == 400


# --------------------------------------------------------------------------- #
# Device-bearer self-service tests
# --------------------------------------------------------------------------- #

def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _admin_uid(app) -> str:
    return app.state.auth.find_user("admin")["id"]


async def _register_device(app, user_id: str) -> dict:
    return await app.state.device_store.register(
        user_id=user_id, platform="ios", display_name="lock-screen"
    )


async def _decision_for_user(app, user_id: str, **extra) -> dict:
    defaults = {
        "from_agent": "@taOS-dev",
        "question": "device-bearer test",
        "type": "approve_deny",
    }
    defaults.update(extra)
    return await app.state.decision_store.create(
        from_agent=defaults["from_agent"],
        question=defaults["question"],
        type=defaults["type"],
        user_id=user_id,
    )


def _bearer_only_client(app, token):
    """AsyncClient with ONLY a device Bearer header, no session cookie."""
    from httpx import ASGITransport, AsyncClient
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers=_bearer(token),
    )


@pytest.mark.asyncio
async def test_device_bearer_lists_only_own_user_decisions(client, app):
    """Invariant (a): a device paired to an ADMIN user must NOT inherit
    admin scope.  GET /api/decisions with the device bearer must return only
    that user's decisions, never uid=None (which lists EVERY user's).

    This test FAILS if is_admin were copied from the user record, because the
    admin decision AND the other-user decision would both be returned.
    """
    admin_uid = _admin_uid(app)
    admin_decision = await _decision_for_user(app, admin_uid)
    other_decision = await _decision_for_user(app, "other-user-abc")
    device = await _register_device(app, admin_uid)

    resp = await client.get("/api/decisions", headers=_bearer(device["scoped_token"]))
    assert resp.status_code == 200
    ids = {d["id"] for d in resp.json()["items"]}
    assert admin_decision["id"] in ids
    assert other_decision["id"] not in ids


@pytest.mark.asyncio
async def test_device_bearer_cannot_get_other_user_decision(client, app):
    """Invariants (a) + (d): an admin-paired device bearer cannot read another
    user's decision -- the ownership gate on get_decision must fire because
    is_admin is False and the decision's user_id differs."""
    admin_uid = _admin_uid(app)
    other_decision = await _decision_for_user(app, "other-user-xyz")
    device = await _register_device(app, admin_uid)

    resp = await client.get(
        f"/api/decisions/{other_decision['id']}",
        headers=_bearer(device["scoped_token"]),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_device_bearer_cannot_answer_other_user_decision(client, app):
    """Invariants (a) + (d): an admin-paired device bearer cannot answer
    another user's decision.  answered_by stays attribution-only; the decider
    check (existing["user_id"] != user.user_id) blocks it."""
    admin_uid = _admin_uid(app)
    other_decision = await _decision_for_user(app, "other-user-qrs")
    device = await _register_device(app, admin_uid)

    resp = await client.post(
        f"/api/decisions/{other_decision['id']}/answer",
        json={"value": "approve"},
        headers=_bearer(device["scoped_token"]),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_device_bearer_lists_gets_answers_own_decision(client, app):
    """Criterion 3: a device bearer can list/get/answer a decision addressed to
    its own user."""
    admin_uid = _admin_uid(app)
    device = await _register_device(app, admin_uid)
    decision = await _decision_for_user(app, admin_uid, question="Lock screen?")
    token = device["scoped_token"]

    # List (pending only)
    resp = await client.get(
        "/api/decisions?status=pending", headers=_bearer(token)
    )
    assert resp.status_code == 200
    assert any(d["id"] == decision["id"] for d in resp.json()["items"])

    # Get
    resp = await client.get(
        f"/api/decisions/{decision['id']}", headers=_bearer(token)
    )
    assert resp.status_code == 200
    assert resp.json()["question"] == "Lock screen?"

    # Answer
    resp = await client.post(
        f"/api/decisions/{decision['id']}/answer",
        json={"value": "approve"},
        headers=_bearer(token),
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "answered"
    assert resp.json()["answer"]["value"] == "approve"


@pytest.mark.asyncio
async def test_device_bearer_answer_routes_back_to_bus(client, app, monkeypatch):
    """The device bearer answer must route back to the asking agent on the
    A2A bus identically to a user-session answer."""
    import tinyagentos.routes.decisions as dmod

    posted = {}

    class _FakeAsyncClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None):
            posted["url"] = url
            posted["json"] = json
            return None

    monkeypatch.setattr(dmod.httpx, "AsyncClient", _FakeAsyncClient)

    admin_uid = _admin_uid(app)
    device = await _register_device(app, admin_uid)
    decision = await _decision_for_user(
        app, admin_uid, from_agent="@taOS-dev", question="Unlock?",
    )
    resp = await client.post(
        f"/api/decisions/{decision['id']}/answer",
        json={"value": "approve"},
        headers=_bearer(device["scoped_token"]),
    )
    assert resp.status_code == 200
    assert posted["url"].endswith("/a2a/send")
    assert posted["json"]["thread"] == "decisions"
    assert "@taOS-dev" in posted["json"]["body"]


@pytest.mark.asyncio
async def test_device_bearer_401_on_unrelated_routes(client, app):
    """Invariant (c): a device bearer must 401 on routes that are NOT carded
    for device auth -- proving the dependency is per-route and no
    request.state.user_id injection happened."""
    admin_uid = _admin_uid(app)
    device = await _register_device(app, admin_uid)

    async with _bearer_only_client(app, device["scoped_token"]) as dc:
        # GET /api/devices is session-only (list own devices).
        assert (await dc.get("/api/devices")).status_code == 401
        # POST /api/decisions is agent/session-only (create).
        assert (
            await dc.post("/api/decisions", json={
                "from_agent": "@a", "question": "q", "type": "free_text",
            })
        ).status_code == 401


@pytest.mark.asyncio
async def test_device_bearer_history_parity(client, app):
    """LOW: /history parity -- a device bearer can read the history of a
    decision addressed to its own user."""
    admin_uid = _admin_uid(app)
    device = await _register_device(app, admin_uid)
    decision = await _decision_for_user(app, admin_uid)

    resp = await client.get(
        f"/api/decisions/{decision['id']}/history",
        headers=_bearer(device["scoped_token"]),
    )
    assert resp.status_code == 200
    assert "items" in resp.json()


@pytest.mark.asyncio
async def test_device_bearer_history_rejects_other_user(client, app):
    """LOW: /history parity -- a device bearer cannot read another user's
    decision history."""
    admin_uid = _admin_uid(app)
    device = await _register_device(app, admin_uid)
    other_decision = await _decision_for_user(app, "other-user-hist")

    resp = await client.get(
        f"/api/decisions/{other_decision['id']}/history",
        headers=_bearer(device["scoped_token"]),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_device_bearer_execution_gate_refused(client, app):
    """A device bearer must not approve an execution_gate decision: the phone
    is a notification surface, not an approval channel for agent execution."""
    admin_uid = _admin_uid(app)
    device = await _register_device(app, admin_uid)
    resp = await client.post("/api/decisions", json={
        "from_agent": "agent-a",
        "question": "Agent agent-a wants to run code_exec (code-exec)",
        "type": "approve_deny",
        "priority": "blocking",
        "metadata": {"kind": "execution_gate", "agent_name": "agent-a",
                     "action_class": "code-exec", "tool": "code_exec"},
    })
    assert resp.status_code == 200
    d = resp.json()
    resp = await client.post(
        f"/api/decisions/{d['id']}/answer",
        json={"value": "approve"},
        headers=_bearer(device["scoped_token"]),
    )
    assert resp.status_code == 409
    assert await app.state.execution_policies.has_live_grant("agent-a", "code-exec") is False


@pytest.mark.asyncio
async def test_device_bearer_delegation_gate_refused(client, app):
    """A device bearer must not approve a delegation_gate decision."""
    admin_uid = _admin_uid(app)
    device = await _register_device(app, admin_uid)
    resp = await client.post("/api/decisions", json={
        "from_agent": "agent-a",
        "question": "Delegate task to agent-b?",
        "type": "approve_deny",
        "priority": "blocking",
        "metadata": {"kind": "delegation_gate", "from_agent": "agent-a",
                     "to_agent": "agent-b", "task_id": "task-1", "task_title": "Task 1"},
    })
    assert resp.status_code == 200
    d = resp.json()
    resp = await client.post(
        f"/api/decisions/{d['id']}/answer",
        json={"value": "approve"},
        headers=_bearer(device["scoped_token"]),
    )
    assert resp.status_code == 409
    # No delegate grant must be written.
    policies = getattr(app.state, "execution_policies", None)
    if policies is not None:
        assert await policies.has_live_grant("agent-a", "delegate") is False


@pytest.mark.asyncio
async def test_device_bearer_ordinary_consent_still_answered(client, app):
    """Control: a device bearer can still answer a non-gate consent decision
    (e.g. a plain approve_deny with no gate kind)."""
    admin_uid = _admin_uid(app)
    device = await _register_device(app, admin_uid)
    decision = await _decision_for_user(
        app, admin_uid, question="Notify me?", type="approve_deny",
    )
    resp = await client.post(
        f"/api/decisions/{decision['id']}/answer",
        json={"value": "approve"},
        headers=_bearer(device["scoped_token"]),
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "answered"
    assert resp.json()["answer"]["value"] == "approve"


@pytest.mark.asyncio
async def test_session_user_execution_gate_still_approved(client, app):
    """Control: a real session user can still approve an execution_gate."""
    d = await app.state.decision_store.create(
        from_agent="agent-a",
        question="Agent agent-a wants to run code_exec (code-exec)",
        type="approve_deny",
        priority="blocking",
        user_id=_admin_uid(app),
        metadata={SERVER_RAISED_KEY: True, "kind": "execution_gate", "agent_name": "agent-a",
                  "action_class": "code-exec", "tool": "code_exec"},
    )
    resp = await client.post(
        f"/api/decisions/{d['id']}/answer", json={"value": "approve"},
    )
    assert resp.status_code == 200
    assert await app.state.execution_policies.has_live_grant("agent-a", "code-exec") is True


# --------------------------------------------------------------------------- #
# Other / free-text answer tests
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_single_select_other_answer_accepted(client):
    """A free-text Other answer on a single_select decision is accepted and
    stored distinguishably."""
    resp = await client.post("/api/decisions", json={
        "from_agent": "@a", "question": "q", "type": "single_select",
        "options": [{"label": "A", "value": "a"}, {"label": "B", "value": "b"}],
    })
    d = resp.json()
    resp = await client.post(
        f"/api/decisions/{d['id']}/answer",
        json={"value": None, "other_value": "my custom answer"},
    )
    assert resp.status_code == 200
    ans = resp.json()["answer"]
    assert ans["value"] == "my custom answer"
    assert ans["other_value"] == "my custom answer"


@pytest.mark.asyncio
async def test_single_select_other_with_note(client):
    """A free-text Other answer with a note stores both."""
    resp = await client.post("/api/decisions", json={
        "from_agent": "@a", "question": "q", "type": "single_select",
        "options": [{"label": "A", "value": "a"}],
    })
    d = resp.json()
    resp = await client.post(
        f"/api/decisions/{d['id']}/answer",
        json={"value": None, "other_value": "custom", "note": "with caveat"},
    )
    assert resp.status_code == 200
    ans = resp.json()["answer"]
    assert ans["value"] == "custom"
    assert ans["other_value"] == "custom"
    assert ans["note"] == "with caveat"


@pytest.mark.asyncio
async def test_single_select_other_and_option_mutually_exclusive(client):
    """Sending both a value and other_value for single_select is rejected."""
    resp = await client.post("/api/decisions", json={
        "from_agent": "@a", "question": "q", "type": "single_select",
        "options": [{"label": "A", "value": "a"}],
    })
    d = resp.json()
    resp = await client.post(
        f"/api/decisions/{d['id']}/answer",
        json={"value": "a", "other_value": "custom"},
    )
    assert resp.status_code == 400
    assert "cannot combine value with other_value" in resp.json()["error"]


@pytest.mark.asyncio
async def test_single_select_option_still_rejects_invalid_with_other_support(client):
    """An invalid option value is still rejected even when Other is available."""
    resp = await client.post("/api/decisions", json={
        "from_agent": "@a", "question": "q", "type": "single_select",
        "options": [{"label": "A", "value": "a"}, {"label": "B", "value": "b"}],
    })
    d = resp.json()
    # A value outside the declared options is still rejected.
    resp = await client.post(
        f"/api/decisions/{d['id']}/answer",
        json={"value": "zzz"},
    )
    assert resp.status_code == 400
    # The decision is still answerable with a valid option.
    resp = await client.post(f"/api/decisions/{d['id']}/answer", json={"value": "a"})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_multi_select_other_answer_accepted(client):
    """A free-text Other answer on a multi_select decision is accepted."""
    resp = await client.post("/api/decisions", json={
        "from_agent": "@a", "question": "q", "type": "multi_select",
        "options": [{"label": "A", "value": "a"}, {"label": "B", "value": "b"}],
    })
    d = resp.json()
    resp = await client.post(
        f"/api/decisions/{d['id']}/answer",
        json={"value": ["a"], "other_value": "custom"},
    )
    assert resp.status_code == 200
    ans = resp.json()["answer"]
    assert ans["value"] == ["a", "custom"]
    assert ans["other_value"] == "custom"


@pytest.mark.asyncio
async def test_multi_select_other_plus_option_works(client):
    """multi_select with Other plus a real option stores both."""
    resp = await client.post("/api/decisions", json={
        "from_agent": "@a", "question": "q", "type": "multi_select",
        "options": [{"label": "A", "value": "a"}, {"label": "B", "value": "b"}],
    })
    d = resp.json()
    resp = await client.post(
        f"/api/decisions/{d['id']}/answer",
        json={"value": ["a", "b"], "other_value": "also this"},
    )
    assert resp.status_code == 200
    ans = resp.json()["answer"]
    assert ans["value"] == ["a", "b", "also this"]


@pytest.mark.asyncio
async def test_multi_select_invalid_option_still_rejected(client):
    """An invalid option value in multi_select is still rejected."""
    resp = await client.post("/api/decisions", json={
        "from_agent": "@a", "question": "q", "type": "multi_select",
        "options": [{"label": "A", "value": "a"}, {"label": "B", "value": "b"}],
    })
    d = resp.json()
    resp = await client.post(
        f"/api/decisions/{d['id']}/answer",
        json={"value": ["a", "nope"]},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_other_answer_routes_to_agent_with_note(client, monkeypatch):
    """A free-text Other answer with a note reaches the agent in the same
    shape as an option answer, with the note appended."""
    import tinyagentos.routes.decisions as dmod

    posted = {}

    class _FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None):
            posted["url"] = url
            posted["json"] = json
            return None

    monkeypatch.setattr(dmod.httpx, "AsyncClient", _FakeClient)

    resp = await client.post("/api/decisions", json={
        "from_agent": "@taOS-dev", "question": "Pick one", "type": "single_select",
        "options": [{"label": "A", "value": "a"}],
    })
    d = resp.json()
    resp = await client.post(
        f"/api/decisions/{d['id']}/answer",
        json={"value": None, "other_value": "custom", "note": "caveat"},
    )
    assert resp.status_code == 200
    assert posted["url"].endswith("/a2a/send")
    assert "@taOS-dev" in posted["json"]["body"]
    assert "custom" in posted["json"]["body"]
    assert "(note: caveat)" in posted["json"]["body"]


# ---------------------------------------------------------------------------
# W2: first-answer-wins + live propagation
# --------------------------------------------------------------------------- #


class _NoOpAsyncClient:
    """Stub httpx.AsyncClient so _route_answer_to_agent does not hit the network."""

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None):
        return None


@pytest.mark.asyncio
async def test_concurrent_double_answer_first_wins(client, monkeypatch):
    """First-answer-wins hard requirement: two answers fired concurrently to
    the same decision must record exactly one answer; the loser is rejected
    cleanly with 409, never a 500, a silent overwrite, or a duplicate row."""
    import tinyagentos.routes.decisions as dmod

    monkeypatch.setattr(dmod.httpx, "AsyncClient", _NoOpAsyncClient)

    resp = await client.post("/api/decisions", json={
        "from_agent": "@taOS-dev", "question": "deploy?", "type": "approve_deny",
    })
    assert resp.status_code == 200, resp.text
    did = resp.json()["id"]

    # Fire both answers concurrently. Both pass the store.get() auth gate (they
    # see status='pending'), but only one UPDATE ... WHERE status='pending' can
    # commit at the SQLite layer; the loser gets rowcount=0 -> None -> 409.
    r1, r2 = await asyncio.gather(
        client.post(f"/api/decisions/{did}/answer", json={"value": "approve"}),
        client.post(f"/api/decisions/{did}/answer", json={"value": "deny"}),
    )

    statuses = sorted([r1.status_code, r2.status_code])
    assert statuses == [200, 409], (
        f"expected one 200 and one 409, got {statuses}: {r1.text} / {r2.text}"
    )

    # Exactly one answer persisted in the store.
    app = client._transport.app
    stored = await app.state.decision_store.get(did)
    assert stored["status"] == "answered"
    winner = "approve" if r1.status_code == 200 else "deny"
    assert stored["answer"]["value"] == winner
    assert stored["answer"]["answered_by"]  # attributed to a real user


@pytest.mark.asyncio
async def test_answer_publishes_decision_answered_to_owner_channel(client, monkeypatch):
    """Answering a decision publishes a 'decision.answered' event on the
    OWNER's user:<id> channel (which their SSE stream subscribes to), and
    NOT on the broadcast channel: the payload is the full decision record,
    and broadcast + the replay buffer would hand it to every other
    authenticated user."""
    from tinyagentos.events.bus import EventBus

    import tinyagentos.routes.decisions as dmod

    monkeypatch.setattr(dmod.httpx, "AsyncClient", _NoOpAsyncClient)

    app = client._transport.app
    bus = EventBus()
    app.state.event_bus = bus
    bcast_q = await bus.subscribe("broadcast")

    resp = await client.post("/api/decisions", json={
        "from_agent": "@taOS-dev", "question": "q", "type": "approve_deny",
    })
    did = resp.json()["id"]
    owner = (await app.state.decision_store.get(did))["user_id"]
    assert owner  # the decision must be owned for this test to mean anything
    owner_q = await bus.subscribe(f"user:{owner}")

    resp = await client.post(f"/api/decisions/{did}/answer", json={"value": "approve"})
    assert resp.status_code == 200

    ev = owner_q.get_nowait()
    assert ev.kind == "decision.answered"
    assert ev.payload["decision_id"] == did
    assert ev.payload["answer"]["value"] == "approve"
    assert ev.targets == ["user"]

    # Scoping: nothing decision-shaped may reach the broadcast channel.
    while not bcast_q.empty():
        assert bcast_q.get_nowait().kind != "decision.answered"


@pytest.mark.asyncio
async def test_second_answer_409_no_duplicate_event(client, monkeypatch):
    """A rejected (second) answer must not broadcast a second event.
    Only the winning answer publishes decision.answered; the 409 loser is silent."""
    from tinyagentos.events.bus import EventBus

    import tinyagentos.routes.decisions as dmod

    monkeypatch.setattr(dmod.httpx, "AsyncClient", _NoOpAsyncClient)

    app = client._transport.app
    bus = EventBus()
    app.state.event_bus = bus

    resp = await client.post("/api/decisions", json={
        "from_agent": "@taOS-dev", "question": "q", "type": "approve_deny",
    })
    did = resp.json()["id"]
    owner = (await app.state.decision_store.get(did))["user_id"]
    owner_q = await bus.subscribe(f"user:{owner}")

    first = await client.post(f"/api/decisions/{did}/answer", json={"value": "approve"})
    assert first.status_code == 200
    second = await client.post(f"/api/decisions/{did}/answer", json={"value": "deny"})
    assert second.status_code == 409

    # Exactly one decision.answered event was published.
    count = 0
    while not owner_q.empty():
        ev = owner_q.get_nowait()
        if ev.kind == "decision.answered":
            count += 1
    assert count == 1


@pytest.mark.asyncio
async def test_dec_sfdooy_wake_budget_decision_created_and_closed(client, app):
    """Create the dec-sfdooy decision card for wake-budget design and close it."""
    store = app.state.decision_store
    await store.init()
    now = time.time()
    await store._db.execute(
        """INSERT INTO decisions
           (id, from_agent, project_id, user_id, question, type, options, context,
            priority, status, created_at, deadline, parent_decision_id,
            checkpoint_ref, timeline_id, metadata)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?)""",
        (
            "dec-sfdooy",
            "@taOS-dev",
            None,
            "admin",
            "Wake-budget config: per-agent + global default + per-project override, OS-enforced",
            "approve_deny",
            json.dumps([
                {"label": "Approve", "value": "approve", "recommended": True, "rationale": "Matches Jay direction on dec-sfdooy"},
                {"label": "Deny", "value": "deny"},
            ]),
            "Fleet default stays at 2/day until this ships. Most specific wins: per-project > per-agent > global_default. Mention wakes exempt but countable.",
            "normal",
            now,
            None,
            None,
            None,
            None,
            json.dumps({"kind": "design_decision", "card": "dec-sfdooy"}),
        ),
    )
    await store._db.commit()
    decision = await store.get("dec-sfdooy")
    assert decision is not None
    assert decision["status"] == "pending"

    updated = await store.answer("dec-sfdooy", "approve", answered_by="admin", source="in_app")
    assert updated is not None
    assert updated["status"] == "answered"
    answer_value = updated["answer"]
    if isinstance(answer_value, str):
        answer_value = json.loads(answer_value)
    assert answer_value["value"] == "approve"

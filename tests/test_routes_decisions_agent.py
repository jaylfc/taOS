"""Agent-token path for POST /api/decisions (decisions_write grant gating)."""
import pytest
from httpx import ASGITransport, AsyncClient

from tinyagentos.agent_registry_store import mint_registry_token


async def _mint_agent(app, project_id, scopes, handle="@taOS-dev"):
    """Register an active agent, grant it *scopes* for *project_id*, return
    (canonical_id, bearer_token). project_id=None grants globally."""
    registry = app.state.agent_registry
    grants = app.state.agent_grants
    for store in (registry, grants):
        if store._db is None:
            await store.init()
    priv, _pub = app.state.agent_registry_keypair
    rec = await registry.register(
        framework="claude-code",
        display_name="taOS dev",
        # Simulates an internal driver agent; its name deliberately slugs to
        # the reserved taos- prefix, so it needs the internal-path escape hatch.
        allow_reserved=True,
        origin="internal",
        handle=handle,
    )
    cid = rec["canonical_id"]
    if rec.get("status") != "active":
        await registry.set_status(cid, "active")
    for scope in scopes:
        await grants.add_grant(cid, scope, project_id=project_id)
    token = mint_registry_token(
        cid, priv, user_id="u", framework="claude-code", project_id=project_id
    )
    return cid, token


def _agent_client(app, token):
    """Cookieless client that authenticates only via the agent bearer token."""
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )


async def _new_project(client, name="alpha", slug="alpha"):
    resp = await client.post("/api/projects", json={"name": name, "slug": slug})
    assert resp.status_code in (200, 201), resp.text
    return resp.json()["id"]


def _decision_body(**over):
    body = {"from_agent": "spoofed", "question": "ship it?", "type": "approve_deny"}
    body.update(over)
    return body


@pytest.mark.asyncio
async def test_agent_with_project_grant_posts(client):
    """A granted agent posts into its project: attributed to the agent, decided
    by the project owner, from_agent not taken from the (spoofed) body."""
    app = client._transport.app
    admin_id = app.state.auth.find_user("admin")["id"]
    pid = await _new_project(client)  # owned by the admin session
    cid, token = await _mint_agent(app, pid, ("decisions_write",))

    async with _agent_client(app, token) as ac:
        resp = await ac.post("/api/decisions", json=_decision_body(project_id=pid))
    assert resp.status_code == 200, resp.text
    d = resp.json()
    assert d["from_agent"] == cid          # authenticated identity, not "spoofed"
    assert d["user_id"] == admin_id        # project owner, resolved not caller
    assert d["project_id"] == pid


@pytest.mark.asyncio
async def test_agent_global_grant_posts_os_level(client):
    """A global (null-project) grant lets the agent raise an OS-level decision,
    decided by the instance admin."""
    app = client._transport.app
    admin_id = app.state.auth.find_user("admin")["id"]
    cid, token = await _mint_agent(app, None, ("decisions_write",))

    async with _agent_client(app, token) as ac:
        resp = await ac.post("/api/decisions", json=_decision_body())  # no project_id
    assert resp.status_code == 200, resp.text
    d = resp.json()
    assert d["from_agent"] == cid
    assert d["user_id"] == admin_id
    assert d["project_id"] is None


@pytest.mark.asyncio
async def test_agent_global_grant_403_into_project(client):
    """A global grant is not a skeleton key: posting into a specific project
    without a per-project grant is 403."""
    app = client._transport.app
    pid = await _new_project(client)
    _cid, token = await _mint_agent(app, None, ("decisions_write",))

    async with _agent_client(app, token) as ac:
        resp = await ac.post("/api/decisions", json=_decision_body(project_id=pid))
    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_agent_without_decisions_grant_403(client):
    """A valid agent token with some other scope but no decisions_write is 403."""
    app = client._transport.app
    pid = await _new_project(client)
    _cid, token = await _mint_agent(app, pid, ("project_tasks",))  # wrong scope

    async with _agent_client(app, token) as ac:
        resp = await ac.post("/api/decisions", json=_decision_body(project_id=pid))
    assert resp.status_code == 403, resp.text


@pytest.mark.asyncio
async def test_human_path_unchanged(client):
    """The session user path still works: the decision is attributed to the
    body's from_agent and decided by the session user."""
    app = client._transport.app
    admin_id = app.state.auth.find_user("admin")["id"]
    resp = await client.post("/api/decisions", json=_decision_body(from_agent="@taOS-dev"))
    assert resp.status_code == 200, resp.text
    d = resp.json()
    assert d["from_agent"] == "@taOS-dev"
    assert d["user_id"] == admin_id


# ── Agent answer (mirror) tests ──────────────────────────────────


@pytest.mark.asyncio
async def test_agent_answers_own_decision(client):
    """An agent with a decisions_write grant can answer its own pending
    decision via POST /api/decisions/{id}/answer/agent."""
    app = client._transport.app
    pid = await _new_project(client)
    cid, token = await _mint_agent(app, pid, ("decisions_write",))

    # Create a decision as the agent
    async with _agent_client(app, token) as ac:
        resp = await ac.post("/api/decisions", json=_decision_body(project_id=pid))
    assert resp.status_code == 200, resp.text
    did = resp.json()["id"]

    # Answer it as the agent
    async with _agent_client(app, token) as ac:
        resp = await ac.post(
            f"/api/decisions/{did}/answer/agent",
            json={"value": "approve"},
        )
    assert resp.status_code == 200, resp.text
    d = resp.json()
    assert d["status"] == "answered"
    ans = d["answer"]
    assert ans["value"] == "approve"
    assert ans["source"] == "mirrored_from_chat"
    assert ans["answered_by"] == cid  # canonical agent id, not "user"


@pytest.mark.asyncio
async def test_agent_cannot_answer_others_decision(client):
    """An agent answering a decision it did NOT ask must 404."""
    app = client._transport.app
    pid = await _new_project(client)

    # Agent A creates a decision
    cid_a, token_a = await _mint_agent(app, pid, ("decisions_write",), handle="@agent-a")
    async with _agent_client(app, token_a) as ac:
        resp = await ac.post("/api/decisions", json=_decision_body(project_id=pid))
    assert resp.status_code == 200, resp.text
    did = resp.json()["id"]

    # Agent B tries to answer it
    _cid_b, token_b = await _mint_agent(app, pid, ("decisions_write",), handle="@agent-b")
    async with _agent_client(app, token_b) as ac:
        resp = await ac.post(
            f"/api/decisions/{did}/answer/agent",
            json={"value": "approve"},
        )
    assert resp.status_code == 404, resp.text
    assert resp.json() == {"error": "not found"}, (
        f"body must match scope-mismatch 404: {resp.text}"
    )


@pytest.mark.asyncio
async def test_agent_global_grant_answers_os_level(client):
    """A global (null-project) grant lets the agent answer its own OS-level
    decision."""
    app = client._transport.app
    cid, token = await _mint_agent(app, None, ("decisions_write",))

    async with _agent_client(app, token) as ac:
        resp = await ac.post("/api/decisions", json=_decision_body())
    assert resp.status_code == 200, resp.text
    did = resp.json()["id"]

    async with _agent_client(app, token) as ac:
        resp = await ac.post(
            f"/api/decisions/{did}/answer/agent",
            json={"value": "approve"},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["answer"]["source"] == "mirrored_from_chat"
    assert resp.json()["answer"]["answered_by"] == cid


# ── Agent read-own tests ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_agent_reads_own_decision(client):
    """An agent can GET /api/decisions/{id}/agent for a decision it asked."""
    app = client._transport.app
    pid = await _new_project(client)
    cid, token = await _mint_agent(app, pid, ("decisions_write",))

    async with _agent_client(app, token) as ac:
        resp = await ac.post("/api/decisions", json=_decision_body(project_id=pid))
    assert resp.status_code == 200, resp.text
    did = resp.json()["id"]

    async with _agent_client(app, token) as ac:
        resp = await ac.get(f"/api/decisions/{did}/agent")
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == did
    assert resp.json()["from_agent"] == cid


@pytest.mark.asyncio
async def test_agent_cannot_read_others_decision(client):
    """An agent reading a decision it did NOT ask must 404."""
    app = client._transport.app
    pid = await _new_project(client)

    cid_a, token_a = await _mint_agent(app, pid, ("decisions_write",), handle="@agent-a")
    async with _agent_client(app, token_a) as ac:
        resp = await ac.post("/api/decisions", json=_decision_body(project_id=pid))
    assert resp.status_code == 200, resp.text
    did = resp.json()["id"]

    _cid_b, token_b = await _mint_agent(app, pid, ("decisions_write",), handle="@agent-b")
    async with _agent_client(app, token_b) as ac:
        resp = await ac.get(f"/api/decisions/{did}/agent")
    assert resp.status_code == 404, resp.text


# ── Agent list-own tests ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_agent_lists_own_decisions(client):
    """GET /api/decisions/agent returns only the asking agent's decisions."""
    app = client._transport.app
    pid = await _new_project(client)
    cid_a, token_a = await _mint_agent(app, pid, ("decisions_write",), handle="@agent-a")
    cid_b, token_b = await _mint_agent(app, pid, ("decisions_write",), handle="@agent-b")

    # Agent A posts a decision
    async with _agent_client(app, token_a) as ac:
        resp = await ac.post("/api/decisions", json=_decision_body(project_id=pid))
    assert resp.status_code == 200, resp.text

    # Agent B posts a decision
    async with _agent_client(app, token_b) as ac:
        resp = await ac.post("/api/decisions", json=_decision_body(project_id=pid))
    assert resp.status_code == 200, resp.text

    # Agent A's list only shows its own
    async with _agent_client(app, token_a) as ac:
        resp = await ac.get("/api/decisions/agent")
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["from_agent"] == cid_a

    # Agent B's list only shows its own
    async with _agent_client(app, token_b) as ac:
        resp = await ac.get("/api/decisions/agent")
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["from_agent"] == cid_b


@pytest.mark.asyncio
async def test_agent_list_no_grant_403(client):
    """An agent without a decisions_write grant cannot list."""
    app = client._transport.app
    pid = await _new_project(client)
    _cid, token = await _mint_agent(app, pid, ("project_tasks",))

    async with _agent_client(app, token) as ac:
        resp = await ac.get("/api/decisions/agent")
    assert resp.status_code == 403, resp.text


# ── Route order test ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_agent_list_is_not_human_get(client):
    """GET /api/decisions/agent reaches the list endpoint, not
    GET /api/decisions/{decision_id} with decision_id='agent'."""
    app = client._transport.app
    pid = await _new_project(client)
    cid, token = await _mint_agent(app, pid, ("decisions_write",))

    # Create a decision so the list is non-empty
    async with _agent_client(app, token) as ac:
        resp = await ac.post("/api/decisions", json=_decision_body(project_id=pid))
    assert resp.status_code == 200, resp.text

    # The list endpoint returns {"items": [...]}, not a single decision
    async with _agent_client(app, token) as ac:
        resp = await ac.get("/api/decisions/agent")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "items" in data  # list response shape
    assert len(data["items"]) >= 1


# ── Agent mirror does not run consent side effects ─────────────────


@pytest.mark.asyncio
async def test_agent_mirror_does_not_write_execution_grant(client):
    """An agent mirroring an answer must NOT write an execution grant.
    Only the human answer path runs consent side effects; otherwise an
    agent could create a privileged decision and self-approve it."""
    app = client._transport.app
    pid = await _new_project(client)
    cid, token = await _mint_agent(app, pid, ("decisions_write",))

    # Create an execution_gate decision as the agent
    body = _decision_body(
        project_id=pid,
        type="approve_deny",
        metadata={"kind": "execution_gate", "agent_name": cid, "action_class": "test-exec"},
    )
    async with _agent_client(app, token) as ac:
        resp = await ac.post("/api/decisions", json=body)
    assert resp.status_code == 200, resp.text
    did = resp.json()["id"]

    # The agent mirrors "approve" -- gate-kind decisions MUST be refused.
    async with _agent_client(app, token) as ac:
        resp = await ac.post(
            f"/api/decisions/{did}/answer/agent",
            json={"value": "approve"},
        )
    assert resp.status_code == 409, resp.text
    assert "gate decisions cannot be answered" in resp.json()["error"]

    # No execution grant must exist — the agent cannot self-approve.
    policies = getattr(app.state, "execution_policies", None)
    if policies is not None:
        assert await policies.has_live_grant(cid, "test-exec") is False


@pytest.mark.asyncio
async def test_agent_mirror_handles_non_hashable_select_value(client):
    """Malformed select values via agent mirror must return 400, not 500."""
    app = client._transport.app
    pid = await _new_project(client)
    cid, token = await _mint_agent(app, pid, ("decisions_write",))

    body = _decision_body(
        project_id=pid,
        type="single_select",
        options=[{"label": "A", "value": "a"}],
    )
    async with _agent_client(app, token) as ac:
        resp = await ac.post("/api/decisions", json=body)
    assert resp.status_code == 200, resp.text
    did = resp.json()["id"]

    # A list value for single_select must 400, not 500.
    async with _agent_client(app, token) as ac:
        resp = await ac.post(
            f"/api/decisions/{did}/answer/agent",
            json={"value": ["a"]},
        )
    assert resp.status_code == 400


# ── Cross-project scope isolation tests ──────────────────────────


@pytest.mark.asyncio
async def test_agent_cross_project_read_blocks_wrong_project(client):
    """Agent with decisions_write on project B cannot read a decision on
    project A (even when from_agent matches).  5d207ec swapped
    check_agent_scope_for_project for project-agnostic check_agent_scope,
    leaking cross-project read.  This test proves the old head leaks
    (200 under 5d207ec) and the fix blocks it (403 under 6410e3c)."""
    app = client._transport.app

    # Two projects: agent has a grant on beta but NOT on alpha.
    pid_a = await _new_project(client, name="alpha", slug="alpha")
    pid_b = await _new_project(client, name="beta", slug="beta")
    cid, token = await _mint_agent(app, pid_b, ("decisions_write",), handle="@cross-x")

    # Create a decision on project A attributed to the agent (admin posts
    # with from_agent set to the agent's canonical id — simulating the
    # agent having had a grant on A that was later revoked).
    resp = await client.post(
        "/api/decisions",
        json=_decision_body(project_id=pid_a, from_agent=cid),
    )
    assert resp.status_code == 200, resp.text
    did = resp.json()["id"]

    # Agent with only project B grant tries to read the project A decision.
    async with _agent_client(app, token) as ac:
        resp = await ac.get(f"/api/decisions/{did}/agent")
    # Project-scoped check blocks with 404 (PROJECT_SCOPE_MISMATCH collapsed
    # to not-found to avoid making the route an existence oracle).
    assert resp.status_code == 404, (
        f"expected 404 cross-project block, got {resp.status_code}: {resp.text}"
    )


@pytest.mark.asyncio
async def test_agent_cross_project_answer_blocks_wrong_project(client):
    """Agent with decisions_write on project B cannot answer a decision on
    project A (even when from_agent matches).  Same 5d207ec regression
    as the read endpoint."""
    app = client._transport.app

    pid_a = await _new_project(client, name="alpha", slug="alpha")
    pid_b = await _new_project(client, name="beta", slug="beta")
    cid, token = await _mint_agent(app, pid_b, ("decisions_write",), handle="@cross-y")

    # Create a pending decision on project A attributed to the agent.
    resp = await client.post(
        "/api/decisions",
        json=_decision_body(project_id=pid_a, from_agent=cid),
    )
    assert resp.status_code == 200, resp.text
    did = resp.json()["id"]

    # Agent with only project B grant tries to answer the project A decision.
    async with _agent_client(app, token) as ac:
        resp = await ac.post(
            f"/api/decisions/{did}/answer/agent",
            json={"value": "approve"},
        )
    assert resp.status_code == 404, (
        f"expected 404 cross-project block, got {resp.status_code}: {resp.text}"
    )
    assert resp.json() == {"error": "not found"}, (
        f"body must match not-found 404: {resp.text}"
    )


# ── Expired-grant test ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_agent_expired_grant_cannot_read(client):
    """Agent with an expired decisions_write grant on project A but an active
    grant on project B cannot read a decision on project A (even when
    from_agent matches)."""
    app = client._transport.app
    from datetime import datetime, timedelta, timezone

    pid_a = await _new_project(client, name="alpha", slug="alpha")
    pid_b = await _new_project(client, name="beta", slug="beta")

    registry = app.state.agent_registry
    grants = app.state.agent_grants
    for store_obj in (registry, grants):
        if store_obj._db is None:
            await store_obj.init()
    priv, _pub = app.state.agent_registry_keypair
    rec = await registry.register(
        framework="claude-code",
        display_name="taOS dev",
        # Simulates an internal driver agent; its name deliberately slugs to
        # the reserved taos- prefix, so it needs the internal-path escape hatch.
        allow_reserved=True,
        origin="internal",
        handle="@expired-x",
    )
    cid = rec["canonical_id"]
    if rec.get("status") != "active":
        await registry.set_status(cid, "active")

    # Expired grant on project A
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    await grants.add_grant(cid, "decisions_write", project_id=pid_a, expires_at=past)
    # Active grant on project B
    await grants.add_grant(cid, "decisions_write", project_id=pid_b)

    token = mint_registry_token(
        cid, priv, user_id="u", framework="claude-code", project_id=pid_b
    )

    # Admin creates a decision on project A attributed to the agent.
    resp = await client.post(
        "/api/decisions",
        json=_decision_body(project_id=pid_a, from_agent=cid),
    )
    assert resp.status_code == 200, resp.text
    did = resp.json()["id"]

    async with _agent_client(app, token) as ac:
        resp = await ac.get(f"/api/decisions/{did}/agent")
    # Expired grant → PROJECT_SCOPE_MISMATCH → collapsed to 404.
    assert resp.status_code == 404, (
        f"expected 404 for expired grant, got {resp.status_code}: {resp.text}"
    )


# ── Cross-project list isolation test ────────────────────────────


@pytest.mark.asyncio
async def test_agent_list_respects_project_grants(client):
    """Agent with decisions_write only on project B does not see its own
    decisions on project A in the list endpoint."""
    app = client._transport.app

    pid_a = await _new_project(client, name="alpha", slug="alpha-2")
    pid_b = await _new_project(client, name="beta", slug="beta-2")
    cid, token = await _mint_agent(app, pid_b, ("decisions_write",), handle="@cross-list")

    # Admin creates a decision on project A attributed to the agent
    # (simulating a grant that was later revoked).
    resp = await client.post(
        "/api/decisions",
        json=_decision_body(project_id=pid_a, from_agent=cid),
    )
    assert resp.status_code == 200, resp.text

    # Agent creates a decision on project B (its granted project)
    async with _agent_client(app, token) as ac:
        resp = await ac.post(
            "/api/decisions",
            json=_decision_body(project_id=pid_b),
        )
    assert resp.status_code == 200, resp.text

    # List: must only show the project-B decision, not the project-A one.
    async with _agent_client(app, token) as ac:
        resp = await ac.get("/api/decisions/agent")
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["project_id"] == pid_b


@pytest.mark.asyncio
async def test_agent_global_grant_lists_only_null_project_decisions(client):
    """A global (null-project) grant must list only OS-level (null-project)
    decisions, never project-scoped ones -- matching _resolve_decision_actor's
    posting rule and the per-project isolation enforced on read/answer.

    This is the agent-list counterpart to the global-grant post/answer
    isolation tests above.  It FAILS if the store treats project_id=None as
    'no filter' instead of 'IS NULL': the project-scoped decision leaks."""
    app = client._transport.app
    pid = await _new_project(client)
    cid, token = await _mint_agent(app, None, ("decisions_write",))

    # OS-level decision (project_id=None) attributed to the agent.
    resp = await client.post(
        "/api/decisions",
        json=_decision_body(project_id=None, from_agent=cid),
    )
    assert resp.status_code == 200, resp.text

    # Project-scoped decision attributed to the SAME agent.
    resp = await client.post(
        "/api/decisions",
        json=_decision_body(project_id=pid, from_agent=cid),
    )
    assert resp.status_code == 200, resp.text

    # Agent lists its own decisions with a global grant.
    async with _agent_client(app, token) as ac:
        resp = await ac.get("/api/decisions/agent")
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    # Only the null-project decision should be visible under a global grant.
    assert len(items) == 1, (
        f"global grant leaked project-scoped decisions: {items}"
    )
    assert items[0]["project_id"] is None
    assert items[0]["from_agent"] == cid


class TestAuthRunsBeforeBodyValidation:
    """A bad bearer must 401 before Pydantic body validation can 422.

    Without the _authenticate_request dependency, a garbage token paired with a
    malformed body returned 422 (field errors) while the same token with a valid
    body returned 401 - an oracle that let an unauthenticated caller distinguish
    token validity by varying the body."""

    @pytest.mark.asyncio
    async def test_garbage_token_with_invalid_body_401s_not_422(self, client):
        app = client._transport.app
        async with _agent_client(app, "garbage-token") as ac:
            resp = await ac.post("/api/decisions", json={"from_agent": "@a"})
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_garbage_token_with_valid_body_401s(self, client):
        app = client._transport.app
        async with _agent_client(app, "garbage-token") as ac:
            resp = await ac.post("/api/decisions", json=_decision_body())
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_answer_agent_garbage_token_with_invalid_body_401s(self, client):
        app = client._transport.app
        async with _agent_client(app, "garbage-token") as ac:
            resp = await ac.post("/api/decisions/dec-xyz/answer/agent", json={})
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_valid_token_with_invalid_body_still_422(self, client):
        app = client._transport.app
        pid = await _new_project(client)
        _cid, token = await _mint_agent(app, pid, ("decisions_write",))
        async with _agent_client(app, token) as ac:
            resp = await ac.post("/api/decisions", json={"from_agent": "@a"})
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_local_token_bearer_is_rejected_on_create(self, client, app):
        """The admin local token is not a registry JWT: as a Bearer on the
        agent path it is rejected 401 (unchanged from before the dependency -
        admin drives decisions through the session path, never a bearer)."""
        local_token = app.state.auth.get_local_token()
        resp = await client.post(
            "/api/decisions",
            json=_decision_body(),
            headers={"Authorization": f"Bearer {local_token}"},
        )
        assert resp.status_code == 401


# ── Other / free-text answer tests ──────────────────────────────────


@pytest.mark.asyncio
async def test_agent_single_select_other_answer_accepted(client):
    """An agent can answer its own single_select decision with a free-text
    Other value via the mirror endpoint."""
    app = client._transport.app
    pid = await _new_project(client)
    cid, token = await _mint_agent(app, pid, ("decisions_write",))

    body = _decision_body(
        project_id=pid,
        type="single_select",
        options=[{"label": "A", "value": "a"}],
    )
    async with _agent_client(app, token) as ac:
        resp = await ac.post("/api/decisions", json=body)
    assert resp.status_code == 200, resp.text
    did = resp.json()["id"]

    async with _agent_client(app, token) as ac:
        resp = await ac.post(
            f"/api/decisions/{did}/answer/agent",
            json={"value": None, "other_value": "my custom answer"},
        )
    assert resp.status_code == 200, resp.text
    d = resp.json()
    assert d["answer"]["value"] == "my custom answer"
    assert d["answer"]["other_value"] == "my custom answer"


@pytest.mark.asyncio
async def test_agent_multi_select_other_plus_option_accepted(client):
    """An agent can answer its own multi_select with a real option plus Other."""
    app = client._transport.app
    pid = await _new_project(client)
    cid, token = await _mint_agent(app, pid, ("decisions_write",))

    body = _decision_body(
        project_id=pid,
        type="multi_select",
        options=[{"label": "A", "value": "a"}, {"label": "B", "value": "b"}],
    )
    async with _agent_client(app, token) as ac:
        resp = await ac.post("/api/decisions", json=body)
    assert resp.status_code == 200, resp.text
    did = resp.json()["id"]

    async with _agent_client(app, token) as ac:
        resp = await ac.post(
            f"/api/decisions/{did}/answer/agent",
            json={"value": ["a"], "other_value": "custom"},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["answer"]["value"] == ["a", "custom"]


@pytest.mark.asyncio
async def test_agent_cannot_answer_via_human_path(client):
    """An agent with a bearer JWT must NEVER be able to answer its own question
    via the human path POST /api/decisions/{id}/answer. That route is not in
    _AGENT_DECISIONS_ROUTES, so the auth middleware does not pass the Bearer
    through, current_user_or_device falls through to require_device (which
    rejects a non-device token), and the request 401s. The answer is always
    attributed to the real user identity (session/device), never the agent."""
    app = client._transport.app
    pid = await _new_project(client)
    _cid, token = await _mint_agent(app, pid, ("decisions_write",))

    # Agent creates its own decision
    async with _agent_client(app, token) as ac:
        resp = await ac.post("/api/decisions", json=_decision_body(project_id=pid))
    assert resp.status_code == 200, resp.text
    did = resp.json()["id"]

    # Agent attempts to answer via the human path with its bearer token
    async with _agent_client(app, token) as ac:
        resp = await ac.post(f"/api/decisions/{did}/answer", json={"value": "approve"})
    assert resp.status_code == 401, resp.text

    # The decision is still pending — no answer was recorded by the agent.
    stored = await app.state.decision_store.get(did)
    assert stored["status"] == "pending"
    assert stored["answer"] is None

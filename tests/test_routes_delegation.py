"""Gated agent delegation (#161): POST /api/agents/{from_agent}/delegate.

Mirrors the structure of test_skill_exec_governance.py: governance applies
only to AGENT (local-token) callers, so every gating assertion uses a
local-token client, not the admin-session `client` fixture (an admin session
bypasses governance the same way it does for skill-exec).
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient


def _local_token_client(app) -> AsyncClient:
    """A bare client (no session cookie) presenting the host local token --
    the way a deployed agent calls this endpoint. Governance applies here."""
    local_token = app.state.auth.get_local_token()
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {local_token}"},
    )


async def _make_project_and_task(app, *, title="Do the thing", created_by="from-agent"):
    project = await app.state.project_store.create_project(
        name="Delegation Test Project", slug="delegation-test", created_by=created_by,
    )
    task = await app.state.project_task_store.create_task(
        project_id=project["id"], title=title, created_by=created_by,
    )
    return project, task


@pytest.fixture(autouse=True)
def _seed_agents(app):
    """Two config agents so from_agent/to_agent resolve via find_agent."""
    app.state.config.agents.append(
        {"name": "from-agent", "host": "", "qmd_index": "from-agent", "color": "#111111"}
    )
    app.state.config.agents.append(
        {"name": "to-agent", "host": "", "qmd_index": "to-agent", "color": "#222222"}
    )


@pytest.mark.asyncio
class TestDelegationGateDeny:
    async def test_deny_policy_returns_403_and_does_not_assign(self, client, app):
        await app.state.execution_policies.set_policy("delegate", "deny", agent_name="from-agent")
        _project, task = await _make_project_and_task(app)

        agent_client = _local_token_client(app)
        try:
            resp = await agent_client.post(
                "/api/agents/from-agent/delegate",
                json={"to_agent": "to-agent", "task_id": task["id"]},
            )
        finally:
            await agent_client.aclose()

        assert resp.status_code == 403
        assert resp.json()["error"] == "forbidden_by_policy"

        unchanged = await app.state.project_task_store.get_task(task["id"])
        assert unchanged["assignee_id"] is None


@pytest.mark.asyncio
class TestDelegationGateRequireApproval:
    async def test_conservative_default_is_pending_approval(self, client, app):
        """delegate is a seeded conservative-default action class -- with no
        override and no live grant, an agent's delegation queues a Decision
        instead of running immediately."""
        _project, task = await _make_project_and_task(app)

        agent_client = _local_token_client(app)
        try:
            resp = await agent_client.post(
                "/api/agents/from-agent/delegate",
                json={"to_agent": "to-agent", "task_id": task["id"], "note": "please pick this up"},
            )
        finally:
            await agent_client.aclose()

        assert resp.status_code == 202
        body = resp.json()
        assert body["status"] == "pending_approval"
        assert body["action_class"] == "delegate"
        assert body["decision_id"]

        decision = await app.state.decision_store.get(body["decision_id"])
        assert decision is not None
        assert decision["status"] == "pending"
        assert decision["priority"] == "blocking"
        assert decision["metadata"]["kind"] == "delegation_gate"
        assert decision["metadata"]["from_agent"] == "from-agent"
        assert decision["metadata"]["to_agent"] == "to-agent"
        assert decision["metadata"]["task_id"] == task["id"]
        assert decision["metadata"]["note"] == "please pick this up"

        # The task must not be assigned until the decision is approved.
        still_open = await app.state.project_task_store.get_task(task["id"])
        assert still_open["assignee_id"] is None

    async def test_live_grant_allows_immediate_delegation(self, client, app):
        _project, task = await _make_project_and_task(app)
        await app.state.execution_policies.add_grant("from-agent", "delegate", "dec-x")

        agent_client = _local_token_client(app)
        try:
            resp = await agent_client.post(
                "/api/agents/from-agent/delegate",
                json={"to_agent": "to-agent", "task_id": task["id"]},
            )
        finally:
            await agent_client.aclose()

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "delegated"
        assert body["task_id"] == task["id"]
        assert body["to_agent"] == "to-agent"

        updated = await app.state.project_task_store.get_task(task["id"])
        assert updated["assignee_id"] == "to-agent"

    async def test_grant_is_scoped_per_agent(self, client, app):
        """A grant for one agent does not authorize a different agent's delegation."""
        _project, task = await _make_project_and_task(app)
        await app.state.execution_policies.add_grant("someone-else", "delegate", "dec-x")

        agent_client = _local_token_client(app)
        try:
            resp = await agent_client.post(
                "/api/agents/from-agent/delegate",
                json={"to_agent": "to-agent", "task_id": task["id"]},
            )
        finally:
            await agent_client.aclose()

        assert resp.status_code == 202
        assert resp.json()["status"] == "pending_approval"


@pytest.mark.asyncio
class TestDelegationAllowPolicy:
    async def test_allow_policy_delegates_immediately(self, client, app):
        await app.state.execution_policies.set_policy("delegate", "allow", agent_name="from-agent")
        _project, task = await _make_project_and_task(app)

        agent_client = _local_token_client(app)
        try:
            resp = await agent_client.post(
                "/api/agents/from-agent/delegate",
                json={"to_agent": "to-agent", "task_id": task["id"]},
            )
        finally:
            await agent_client.aclose()

        assert resp.status_code == 200
        assert resp.json()["status"] == "delegated"


@pytest.mark.asyncio
class TestDelegationApprovalFlow:
    async def test_approving_decision_completes_delegation(self, client, app):
        """The full round trip: an ungranted delegation queues a Decision;
        answering it 'approve' assigns the task, grants the action class for
        next time, and notifies to_agent on the project's A2A channel."""
        _project, task = await _make_project_and_task(app)

        agent_client = _local_token_client(app)
        try:
            resp = await agent_client.post(
                "/api/agents/from-agent/delegate",
                json={"to_agent": "to-agent", "task_id": task["id"], "note": "handoff"},
            )
        finally:
            await agent_client.aclose()
        assert resp.status_code == 202
        decision_id = resp.json()["decision_id"]

        answer_resp = await client.post(
            f"/api/decisions/{decision_id}/answer", json={"value": "approve"}
        )
        assert answer_resp.status_code == 200

        completed = await app.state.project_task_store.get_task(task["id"])
        assert completed["assignee_id"] == "to-agent"

        # The action class is now granted for from-agent, so a follow-up
        # delegation by the same agent proceeds without another approval.
        assert await app.state.execution_policies.has_live_grant("from-agent", "delegate")

    async def test_denying_decision_does_not_delegate(self, client, app):
        _project, task = await _make_project_and_task(app)

        agent_client = _local_token_client(app)
        try:
            resp = await agent_client.post(
                "/api/agents/from-agent/delegate",
                json={"to_agent": "to-agent", "task_id": task["id"]},
            )
        finally:
            await agent_client.aclose()
        decision_id = resp.json()["decision_id"]

        answer_resp = await client.post(
            f"/api/decisions/{decision_id}/answer", json={"value": "deny"}
        )
        assert answer_resp.status_code == 200

        still_open = await app.state.project_task_store.get_task(task["id"])
        assert still_open["assignee_id"] is None
        assert not await app.state.execution_policies.has_live_grant("from-agent", "delegate")

    async def test_approving_decision_creates_task_from_title(self, client, app):
        """Same approval round trip, but the original request had no task_id
        -- only a task_title + project_id -- so approval must create it."""
        project, _existing_task = await _make_project_and_task(app)

        agent_client = _local_token_client(app)
        try:
            resp = await agent_client.post(
                "/api/agents/from-agent/delegate",
                json={
                    "to_agent": "to-agent",
                    "task_title": "Brand new task",
                    "project_id": project["id"],
                },
            )
        finally:
            await agent_client.aclose()
        assert resp.status_code == 202
        decision_id = resp.json()["decision_id"]

        answer_resp = await client.post(
            f"/api/decisions/{decision_id}/answer", json={"value": "approve"}
        )
        assert answer_resp.status_code == 200

        tasks = await app.state.project_task_store.list_tasks(project["id"])
        new_task = next(t for t in tasks if t["title"] == "Brand new task")
        assert new_task["assignee_id"] == "to-agent"


@pytest.mark.asyncio
class TestDelegationTaskTitleCreatesTask:
    async def test_allow_policy_creates_task_from_title(self, client, app):
        await app.state.execution_policies.set_policy("delegate", "allow", agent_name="from-agent")
        project = await app.state.project_store.create_project(
            name="New Task Project", slug="new-task-project", created_by="from-agent",
        )

        agent_client = _local_token_client(app)
        try:
            resp = await agent_client.post(
                "/api/agents/from-agent/delegate",
                json={"to_agent": "to-agent", "task_title": "Write the docs", "project_id": project["id"]},
            )
        finally:
            await agent_client.aclose()

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "delegated"

        tasks = await app.state.project_task_store.list_tasks(project["id"])
        created = next(t for t in tasks if t["title"] == "Write the docs")
        assert created["assignee_id"] == "to-agent"

    async def test_task_title_without_project_id_is_400(self, client, app):
        await app.state.execution_policies.set_policy("delegate", "allow", agent_name="from-agent")
        agent_client = _local_token_client(app)
        try:
            resp = await agent_client.post(
                "/api/agents/from-agent/delegate",
                json={"to_agent": "to-agent", "task_title": "Orphan task"},
            )
        finally:
            await agent_client.aclose()
        assert resp.status_code == 400


@pytest.mark.asyncio
class TestDelegationValidation:
    async def test_missing_task_id_and_task_title_is_400(self, client, app):
        agent_client = _local_token_client(app)
        try:
            resp = await agent_client.post(
                "/api/agents/from-agent/delegate", json={"to_agent": "to-agent"}
            )
        finally:
            await agent_client.aclose()
        assert resp.status_code == 400

    async def test_unknown_from_agent_is_404(self, client, app):
        _project, task = await _make_project_and_task(app)
        agent_client = _local_token_client(app)
        try:
            resp = await agent_client.post(
                "/api/agents/no-such-agent/delegate",
                json={"to_agent": "to-agent", "task_id": task["id"]},
            )
        finally:
            await agent_client.aclose()
        assert resp.status_code == 404

    async def test_unknown_to_agent_is_404(self, client, app):
        _project, task = await _make_project_and_task(app)
        agent_client = _local_token_client(app)
        try:
            resp = await agent_client.post(
                "/api/agents/from-agent/delegate",
                json={"to_agent": "no-such-agent", "task_id": task["id"]},
            )
        finally:
            await agent_client.aclose()
        assert resp.status_code == 404

    async def test_unknown_task_id_is_404(self, client, app):
        agent_client = _local_token_client(app)
        try:
            resp = await agent_client.post(
                "/api/agents/from-agent/delegate",
                json={"to_agent": "to-agent", "task_id": "no-such-task"},
            )
        finally:
            await agent_client.aclose()
        assert resp.status_code == 404

    async def test_plain_non_admin_session_is_forbidden(self, client, app):
        """A bare non-admin member session must never reach the gate --
        mirrors _is_admin_or_local_token's guard in skill_exec.py. The
        `client` fixture already set up the admin user + startup_complete;
        this just adds a non-admin member on top of it."""
        auth_mgr = app.state.auth
        invite_code = auth_mgr.add_user_invite("member", "admin")
        auth_mgr.complete_invite("member", invite_code, "Test Member", "", "memberpass123")
        member = auth_mgr.find_user("member")
        token = auth_mgr.create_session(user_id=member["id"], long_lived=True)

        member_client = AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            cookies={"taos_session": token},
        )
        try:
            resp = await member_client.post(
                "/api/agents/from-agent/delegate",
                json={"to_agent": "to-agent", "task_title": "x", "project_id": "p1"},
            )
        finally:
            await member_client.aclose()
        assert resp.status_code == 403

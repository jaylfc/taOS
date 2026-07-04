"""Agent governance gate in tinyagentos/routes/skill_exec.py (#160 slice 1).

Covers the policy-driven deny / require-approval / allow paths for skill
EXECUTION, and — critically — that the pre-existing authorization gate
(GHSA-h24f-gp4c-8qjm, ``_is_admin_or_local_token``) still runs first and is
completely unaffected by the new governance layer.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient


async def _ensure_skills_seeded(app) -> None:
    store = app.state.skills
    if store._db is None:
        await store.init()


async def _member_client(app) -> AsyncClient:
    auth_mgr = app.state.auth
    invite_code = auth_mgr.add_user_invite("member", "admin")
    auth_mgr.complete_invite(
        "member", invite_code, "Test Member", "", "memberpass123"
    )
    member = auth_mgr.find_user("member")
    token = auth_mgr.create_session(user_id=member["id"], long_lived=True)
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={"taos_session": token},
    )


def _fake_subprocess_result() -> MagicMock:
    result = MagicMock()
    result.stdout = "hello\n"
    result.stderr = ""
    result.returncode = 0
    return result


@pytest.mark.asyncio
class TestConservativeDefaultRequiresApproval:
    async def test_code_exec_pending_approval_and_no_run(self, client, app):
        """code-exec is a seeded conservative default: an admin caller with no
        live grant gets 202 pending_approval and subprocess never runs."""
        await _ensure_skills_seeded(app)
        with patch("subprocess.run") as mock_run:
            resp = await client.post(
                "/api/skill-exec/code_exec/call",
                json={"args": {"code": "print('no')"}, "agent_name": "agent-a"},
            )
        assert resp.status_code == 202
        body = resp.json()
        assert body["status"] == "pending_approval"
        assert body["action_class"] == "code-exec"
        assert body["decision_id"]
        mock_run.assert_not_called()

        # The blocking Decision was actually created.
        decision = await app.state.decision_store.get(body["decision_id"])
        assert decision is not None
        assert decision["status"] == "pending"
        assert decision["priority"] == "blocking"
        assert decision["metadata"]["kind"] == "execution_gate"
        assert decision["metadata"]["agent_name"] == "agent-a"
        assert decision["metadata"]["action_class"] == "code-exec"
        assert decision["metadata"]["tool"] == "code_exec"

    async def test_live_grant_allows_the_retry(self, client, app):
        """After a grant is written for (agent, action_class), the same call
        proceeds straight to the skill implementation."""
        await _ensure_skills_seeded(app)
        await app.state.execution_policies.add_grant("agent-a", "code-exec", "dec-x")
        with patch("subprocess.run", return_value=_fake_subprocess_result()) as mock_run:
            resp = await client.post(
                "/api/skill-exec/code_exec/call",
                json={"args": {"code": "print('hello')"}, "agent_name": "agent-a"},
            )
        assert resp.status_code == 200
        assert resp.json()["returncode"] == 0
        mock_run.assert_called_once()

    async def test_grant_is_scoped_per_agent(self, client, app):
        """A grant for one agent does not authorize a different agent."""
        await _ensure_skills_seeded(app)
        await app.state.execution_policies.add_grant("agent-a", "code-exec", "dec-x")
        with patch("subprocess.run") as mock_run:
            resp = await client.post(
                "/api/skill-exec/code_exec/call",
                json={"args": {"code": "print('no')"}, "agent_name": "agent-b"},
            )
        assert resp.status_code == 202
        mock_run.assert_not_called()


@pytest.mark.asyncio
class TestDenyPolicy:
    async def test_global_deny_blocks_with_403_and_no_run(self, client, app):
        await _ensure_skills_seeded(app)
        await app.state.execution_policies.set_policy("code-exec", "deny")
        with patch("subprocess.run") as mock_run:
            resp = await client.post(
                "/api/skill-exec/code_exec/call",
                json={"args": {"code": "print('no')"}, "agent_name": "agent-a"},
            )
        assert resp.status_code == 403
        assert resp.json()["error"] == "forbidden_by_policy"
        assert resp.json()["action_class"] == "code-exec"
        mock_run.assert_not_called()

    async def test_per_agent_deny_overrides_global_allow(self, client, app):
        await _ensure_skills_seeded(app)
        await app.state.execution_policies.set_policy(
            "external-network", "deny", agent_name="agent-a"
        )
        resp = await client.post(
            "/api/skill-exec/http_request/call",
            json={"args": {"url": "http://example.com"}, "agent_name": "agent-a"},
        )
        assert resp.status_code == 403
        assert resp.json()["action_class"] == "external-network"


@pytest.mark.asyncio
class TestAllowedActionClass:
    async def test_per_agent_allow_overrides_conservative_default(self, client, app):
        """Per-agent policy wins over the seeded global require_approval."""
        await _ensure_skills_seeded(app)
        await app.state.execution_policies.set_policy(
            "code-exec", "allow", agent_name="agent-a"
        )
        with patch("subprocess.run", return_value=_fake_subprocess_result()) as mock_run:
            resp = await client.post(
                "/api/skill-exec/code_exec/call",
                json={"args": {"code": "print('hi')"}, "agent_name": "agent-a"},
            )
        assert resp.status_code == 200
        mock_run.assert_called_once()

    async def test_unclassified_skill_runs_unconditionally(self, client, app):
        """file_read has no action class; the governance layer never gates it."""
        await _ensure_skills_seeded(app)
        resp = await client.post(
            "/api/skill-exec/file_read/call",
            json={"args": {"path": "nope.txt"}, "agent_name": "agent-a"},
        )
        # 200 with a tool-level "not found" error, not a policy block.
        assert resp.status_code == 200
        assert "error" in resp.json()


@pytest.mark.asyncio
class TestAuthGateStillFirst:
    """REGRESSION: the RCE-fix authorization gate (GHSA-h24f-gp4c-8qjm) must
    remain the very first check and must be completely unaffected by the new
    governance layer -- a non-admin, non-local-token caller is rejected 403
    before any policy lookup, decision creation, or subprocess call."""

    async def test_non_admin_rejected_403_even_with_a_live_grant(self, client, app):
        await _ensure_skills_seeded(app)
        # Even a live grant for this exact (agent, action_class) must not let
        # a non-admin session bypass the auth gate.
        await app.state.execution_policies.add_grant("agent-a", "code-exec", "dec-x")
        member_client = await _member_client(app)
        try:
            with patch("subprocess.run") as mock_run:
                resp = await member_client.post(
                    "/api/skill-exec/code_exec/call",
                    json={"args": {"code": "print('no')"}, "agent_name": "agent-a"},
                )
        finally:
            await member_client.aclose()
        assert resp.status_code == 403
        mock_run.assert_not_called()

    async def test_non_admin_rejected_before_any_decision_is_created(self, client, app):
        await _ensure_skills_seeded(app)
        before = len(await app.state.decision_store.list())
        member_client = await _member_client(app)
        try:
            resp = await member_client.post(
                "/api/skill-exec/code_exec/call",
                json={"args": {"code": "print('no')"}, "agent_name": "agent-a"},
            )
        finally:
            await member_client.aclose()
        assert resp.status_code == 403
        after = len(await app.state.decision_store.list())
        assert after == before, "no decision should be created for an unauthorized caller"

    async def test_admin_session_still_allowed_for_unclassified_skill(self, client, app):
        await _ensure_skills_seeded(app)
        resp = await client.post(
            "/api/skill-exec/list_files/call",
            json={"args": {"path": ""}, "agent_name": "agent-a"},
        )
        assert resp.status_code == 200

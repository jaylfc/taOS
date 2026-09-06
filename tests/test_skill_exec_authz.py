"""Authorization gate for skill EXECUTION (GHSA-h24f-gp4c-8qjm).

``POST /api/skill-exec/{skill_id}/call`` previously had no authorization check
beyond "any valid session": a plain non-admin user could invoke the built-in
``code_exec`` skill and get arbitrary Python execution on the host
(``subprocess.run(["python3", "-c", code])``). The only legitimate callers are
an admin session or a deployed agent presenting the host's local token (see
``tinyagentos/routes/skill_exec.py::_is_admin_or_local_token`` and
``tinyagentos/auth_middleware.py``).

This suite locks in the fix end-to-end through the real ``AuthMiddleware``:
  (a) a non-admin user session is rejected 403 and ``subprocess.run`` is
      never invoked;
  (b) an admin session is allowed;
  (c) a request presenting a valid per-agent local token is allowed;
  (d) an invalid/absent token from a non-admin caller is rejected and never
      elevates privilege, and ``subprocess.run`` is never invoked.
  (e) two deployed agents with distinct per-agent tokens each resolve to their
      own identity; a token cannot impersonate another agent.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient


async def _ensure_skills_seeded(app) -> None:
    """The ``client`` fixture in conftest.py does not initialise app.state.skills
    (it is untouched by most route tests); execute_skill needs the DB open and
    the builtin skills (incl. code_exec) seeded before any /call request."""
    store = app.state.skills
    if store._db is None:
        await store.init()


async def _member_client(app) -> AsyncClient:
    """Cookie'd client for a non-admin member session on *app*.

    Requires the admin created by the ``client`` fixture (add_user_invite
    checks the inviter is an admin), so callers must depend on ``client`` (or
    otherwise set up the admin) before calling this.
    """
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
class TestSkillExecAuthz:
    async def test_non_admin_user_rejected_403_and_no_subprocess(self, client, app):
        """(a) Plain non-admin user session must be rejected 403 and the
        subprocess must never run."""
        await _ensure_skills_seeded(app)
        member_client = await _member_client(app)
        try:
            with patch("subprocess.run") as mock_run:
                resp = await member_client.post(
                    "/api/skill-exec/code_exec/call",
                    json={"args": {"code": "print('should not run')"}},
                )
        finally:
            await member_client.aclose()

        assert resp.status_code == 403
        assert resp.json() != {"stdout": "hello\n", "stderr": "", "returncode": 0}
        mock_run.assert_not_called()

    async def test_admin_session_allowed(self, client, app):
        """(b) An admin session may execute code_exec.

        Agent governance (#160 slice 1) never gates an admin HUMAN session --
        the human is the approval authority and bypasses the policy layer -- so
        this auth-gate test needs no governance setup to reach the impl."""
        await _ensure_skills_seeded(app)
        with patch("subprocess.run", return_value=_fake_subprocess_result()) as mock_run:
            resp = await client.post(
                "/api/skill-exec/code_exec/call",
                json={"args": {"code": "print('hello')"}},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["returncode"] == 0
        mock_run.assert_called_once()

    async def test_valid_local_token_allowed(self, client, app):
        """(c) A request presenting a valid per-agent local token is allowed,
        with no session cookie at all."""
        await _ensure_skills_seeded(app)
        # A local-token caller (a deployed agent) IS governed, unlike an admin
        # session -- so neutralize the conservative code-exec gate here to keep
        # this test focused on the auth gate. Governance itself is covered in
        # test_skill_exec_governance.py.
        await app.state.execution_policies.set_policy("code-exec", "allow")
        # Use the deployer's real token-minting flow, not the shared host token.
        alice_token = app.state.auth.mint_agent_local_token("alice")
        bare_client = AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        )
        try:
            with patch("subprocess.run", return_value=_fake_subprocess_result()) as mock_run:
                resp = await bare_client.post(
                    "/api/skill-exec/code_exec/call",
                    json={"args": {"code": "print('hello')"}},
                    headers={"Authorization": f"Bearer {alice_token}"},
                )
        finally:
            await bare_client.aclose()

        assert resp.status_code == 200
        assert resp.json()["returncode"] == 0
        mock_run.assert_called_once()

    async def test_invalid_token_no_session_rejected_and_no_subprocess(self, client, app):
        """(d) An invalid token with no session at all is rejected (the
        AuthMiddleware session gate itself denies it) and the subprocess never
        runs."""
        await _ensure_skills_seeded(app)
        bare_client = AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        )
        try:
            with patch("subprocess.run") as mock_run:
                resp = await bare_client.post(
                    "/api/skill-exec/code_exec/call",
                    json={"args": {"code": "print('should not run')"}},
                    headers={"Authorization": "Bearer not-a-real-token"},
                )
        finally:
            await bare_client.aclose()

        assert resp.status_code in (401, 403)
        mock_run.assert_not_called()

    async def test_non_admin_session_with_invalid_token_still_rejected(self, client, app):
        """(d) A non-admin session cannot escalate to local-token auth by also
        presenting a bogus Bearer token -- the bad token must not be accepted,
        and the session's own is_admin=False still governs the route gate."""
        await _ensure_skills_seeded(app)
        member_client = await _member_client(app)
        try:
            with patch("subprocess.run") as mock_run:
                resp = await member_client.post(
                    "/api/skill-exec/code_exec/call",
                    json={"args": {"code": "print('should not run')"}},
                    headers={"Authorization": "Bearer not-a-real-token"},
                )
        finally:
            await member_client.aclose()

        assert resp.status_code == 403
        mock_run.assert_not_called()

    async def test_non_admin_user_rejected_for_other_skills_too(self, client, app):
        """The gate applies to skill execution generally, not just code_exec --
        a non-admin session must not be able to invoke file_write either."""
        await _ensure_skills_seeded(app)
        member_client = await _member_client(app)
        try:
            resp = await member_client.post(
                "/api/skill-exec/file_write/call",
                json={"args": {"path": "x.txt", "content": "y"}},
            )
        finally:
            await member_client.aclose()

        assert resp.status_code == 403


@pytest.mark.asyncio
class TestIsAdminOrLocalToken:
    """Unit coverage for the gate predicate itself, independent of the route,
    including the code_exec inline defense-in-depth check."""

    def _request_with_state(self, **state):
        req = MagicMock()
        for key, value in state.items():
            setattr(req.state, key, value)
        return req

    async def test_predicate_true_for_admin(self):
        from tinyagentos.routes.skill_exec import _is_admin_or_local_token

        req = self._request_with_state(is_admin=True, via="session")
        assert _is_admin_or_local_token(req) is True

    async def test_predicate_true_for_local_token_pre_onboarding(self):
        """is_admin is False before a primary user exists, but via ==
        'local_token' must still authorize (matches AuthMiddleware)."""
        from tinyagentos.routes.skill_exec import _is_admin_or_local_token

        req = self._request_with_state(is_admin=False, via="local_token")
        assert _is_admin_or_local_token(req) is True

    async def test_predicate_false_for_plain_session(self):
        from tinyagentos.routes.skill_exec import _is_admin_or_local_token

        req = self._request_with_state(is_admin=False, via="session")
        assert _is_admin_or_local_token(req) is False

    async def test_code_exec_inline_gate_blocks_direct_call_without_route(self):
        """Defense-in-depth: even calling _skill_code_exec directly (bypassing
        execute_skill's route-level gate) must not run the subprocess for an
        unauthorized caller."""
        from tinyagentos.routes.skill_exec import _skill_code_exec

        req = self._request_with_state(is_admin=False, via="session")
        with patch("subprocess.run") as mock_run:
            result = await _skill_code_exec({"code": "print('no')"}, req)

        assert "error" in result
        mock_run.assert_not_called()


@pytest.mark.asyncio
class TestSkillExecCredentialAgentName:
    """Agent identity must come from the credential, not the request body."""

    async def test_per_agent_token_mismatched_agent_name_rejected(self, client, app):
        """A deployed agent presenting its own per-agent token but claiming a
        different agent_name in the body must be rejected."""
        await _ensure_skills_seeded(app)
        alice_token = app.state.auth.mint_agent_local_token("alice")

        bare_client = AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        )
        try:
            resp = await bare_client.post(
                "/api/skill-exec/code_exec/call",
                json={"args": {"code": "print('should not run')"},
                      "agent_name": "victim"},
                headers={"Authorization": f"Bearer {alice_token}"},
            )
        finally:
            await bare_client.aclose()

        assert resp.status_code == 403

    async def test_per_agent_token_matching_agent_name_accepted(self, client, app):
        """A deployed agent presenting its own per-agent token and claiming the
        bound agent_name must be accepted."""
        await _ensure_skills_seeded(app)
        await app.state.execution_policies.set_policy("code-exec", "allow")
        alice_token = app.state.auth.mint_agent_local_token("alice")

        bare_client = AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        )
        try:
            with patch("subprocess.run", return_value=_fake_subprocess_result()) as mock_run:
                resp = await bare_client.post(
                    "/api/skill-exec/code_exec/call",
                    json={"args": {"code": "print('hello')"},
                          "agent_name": "alice"},
                    headers={"Authorization": f"Bearer {alice_token}"},
                )
        finally:
            await bare_client.aclose()

        assert resp.status_code == 200
        assert resp.json()["returncode"] == 0
        mock_run.assert_called_once()

    async def test_per_agent_token_no_body_agent_name_uses_credential(self, client, app):
        """When no agent_name is in the body, the credential-bound name is used."""
        await _ensure_skills_seeded(app)
        await app.state.execution_policies.set_policy("code-exec", "allow")
        alice_token = app.state.auth.mint_agent_local_token("alice")

        bare_client = AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        )
        try:
            with patch("subprocess.run", return_value=_fake_subprocess_result()) as mock_run:
                resp = await bare_client.post(
                    "/api/skill-exec/code_exec/call",
                    json={"args": {"code": "print('hello')"}},
                    headers={"Authorization": f"Bearer {alice_token}"},
                )
        finally:
            await bare_client.aclose()

        assert resp.status_code == 200
        assert resp.json()["returncode"] == 0
        mock_run.assert_called_once()

    async def test_admin_session_body_agent_name_still_honoured(self, client, app):
        """Admin sessions are trusted: body-supplied agent_name is still honoured
        so the human-driven OS agent keeps working."""
        await _ensure_skills_seeded(app)
        resp = await client.post(
            "/api/skill-exec/code_exec/call",
            json={"args": {"code": "print('hello')"},
                  "agent_name": "whatever"},
        )

        assert resp.status_code == 200
        assert resp.json()["returncode"] == 0

    async def test_list_tools_mismatched_agent_name_rejected(self, client, app):
        """GET /api/skill-exec/tools with a mismatched agent_name for a
        local-token caller is rejected."""
        alice_token = app.state.auth.mint_agent_local_token("alice")

        bare_client = AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        )
        try:
            resp = await bare_client.get(
                "/api/skill-exec/tools",
                params={"agent_name": "victim"},
                headers={"Authorization": f"Bearer {alice_token}"},
            )
        finally:
            await bare_client.aclose()

        assert resp.status_code == 403

    async def test_two_deploys_distinct_tokens_resolve_correctly(self, client, app):
        """Two deployed agents with distinct per-agent tokens each resolve to
        their own identity. This is the collision regression: on the old shared-
        token design, the second deploy overwrites the binding and the first
        agent's honest call 403s."""
        await _ensure_skills_seeded(app)
        await app.state.execution_policies.set_policy("code-exec", "allow")

        alice_token = app.state.auth.mint_agent_local_token("alice")
        bob_token = app.state.auth.mint_agent_local_token("bob")

        # alice's honest call (her own token + agent_name alice) -> 200
        alice_client = AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        )
        try:
            with patch("subprocess.run", return_value=_fake_subprocess_result()) as mock_run:
                resp = await alice_client.post(
                    "/api/skill-exec/code_exec/call",
                    json={"args": {"code": "print('hello from alice')"},
                          "agent_name": "alice"},
                    headers={"Authorization": f"Bearer {alice_token}"},
                )
        finally:
            await alice_client.aclose()

        assert resp.status_code == 200
        assert resp.json()["returncode"] == 0
        mock_run.assert_called_once()

        # bob's honest call (his own token + agent_name bob) -> 200
        bob_client = AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        )
        try:
            with patch("subprocess.run", return_value=_fake_subprocess_result()) as mock_run:
                resp = await bob_client.post(
                    "/api/skill-exec/code_exec/call",
                    json={"args": {"code": "print('hello from bob')"},
                          "agent_name": "bob"},
                    headers={"Authorization": f"Bearer {bob_token}"},
                )
        finally:
            await bob_client.aclose()

        assert resp.status_code == 200
        assert resp.json()["returncode"] == 0
        mock_run.assert_called_once()

        # alice presenting agent_name bob with HER token -> 403
        alice_impersonator = AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        )
        try:
            resp = await alice_impersonator.post(
                "/api/skill-exec/code_exec/call",
                json={"args": {"code": "print('should not run')"},
                      "agent_name": "bob"},
                headers={"Authorization": f"Bearer {alice_token}"},
            )
        finally:
            await alice_impersonator.aclose()

        assert resp.status_code == 403

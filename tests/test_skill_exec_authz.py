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
  (c) a request presenting the valid local token is allowed;
  (d) an invalid/absent token from a non-admin caller is rejected and never
      elevates privilege, and ``subprocess.run`` is never invoked.
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
        """(c) A request presenting the valid local token is allowed, with no
        session cookie at all."""
        await _ensure_skills_seeded(app)
        # A local-token caller (a deployed agent) IS governed, unlike an admin
        # session -- so neutralize the conservative code-exec gate here to keep
        # this test focused on the auth gate. Governance itself is covered in
        # test_skill_exec_governance.py.
        await app.state.execution_policies.set_policy("code-exec", "allow")
        local_token = app.state.auth.get_local_token()
        bare_client = AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        )
        try:
            with patch("subprocess.run", return_value=_fake_subprocess_result()) as mock_run:
                resp = await bare_client.post(
                    "/api/skill-exec/code_exec/call",
                    json={"args": {"code": "print('hello')"}},
                    headers={"Authorization": f"Bearer {local_token}"},
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

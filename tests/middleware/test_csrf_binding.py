"""Session binding regression tests for signed CSRF tokens."""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient


class TestCSRFSessionBinding:
    async def _create_sessions(self, app) -> tuple[str, str]:
        app.state.auth.setup_user("csrf-user", "CSRF User", "", "pass1234!")
        user = app.state.auth.find_user("csrf-user")
        return (
            app.state.auth.create_session(user_id=user["id"], long_lived=False),
            app.state.auth.create_session(user_id=user["id"], long_lived=False),
        )

    async def _csrf_token_for_session(self, app, session_id: str) -> str:
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
            cookies={"taos_session": session_id},
        ) as client:
            response = await client.get("/api/health")
            assert response.status_code == 200
            token = client.cookies.get("csrf_token")
            assert token
            return token

    @pytest.mark.asyncio
    async def test_token_minted_for_session_a_is_rejected_for_session_b(self, app):
        session_a, session_b = await self._create_sessions(app)
        token_a = await self._csrf_token_for_session(app, session_a)

        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
            cookies={"taos_session": session_b, "csrf_token": token_a},
        ) as client:
            response = await client.post(
                "/auth/logout",
                headers={"X-CSRF-Token": token_a},
                follow_redirects=False,
            )

        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_token_verification_depends_on_session_id(self, app):
        session_a, session_b = await self._create_sessions(app)
        token_a = await self._csrf_token_for_session(app, session_a)

        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
            cookies={"taos_session": session_a, "csrf_token": token_a},
        ) as client_a:
            response_a = await client_a.post(
                "/auth/logout",
                headers={"X-CSRF-Token": token_a},
                follow_redirects=False,
            )

        async with AsyncClient(
            transport=transport,
            base_url="http://test",
            cookies={"taos_session": session_b, "csrf_token": token_a},
        ) as client_b:
            response_b = await client_b.post(
                "/auth/logout",
                headers={"X-CSRF-Token": token_a},
                follow_redirects=False,
            )

        assert response_a.status_code == 303
        assert response_b.status_code == 403

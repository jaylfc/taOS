"""RED test for generated media isolation (tsk-tfje2m).

Member A must NOT be able to access member B's generated images/music.
This test MUST FAIL on the current codebase (RED), then pass after the fix.
"""
import base64
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient, Request as HttpxRequest, Response

from tinyagentos.app import create_app
from taos_test_csrf import csrf_event_hooks


async def _create_app_with_users(tmp_data_dir, usernames):
    """Create app with multiple users set up."""
    app = create_app(data_dir=tmp_data_dir)
    app.state.data_dir = str(tmp_data_dir)

    # Initialize stores
    store = app.state.metrics
    if store._db is not None:
        await store.close()
    await store.init()
    await app.state.qmd_client.init()
    # Set up users - first is admin via setup_user, rest via invite flow
    for i, username in enumerate(usernames):
        if i == 0:
            # First user is admin
            app.state.auth.setup_user(username, f"Test {username.title()}", "", "testpassword")
        else:
            # Additional users via invite flow
            invite_code = app.state.auth.add_user_invite(username, usernames[0])
            app.state.auth.complete_invite(username, invite_code, f"Test {username.title()}", "", "testpassword")
    app.state._startup_complete = True
    return app


def _get_token(app, username):
    """Get session token for a user."""
    record = app.state.auth.find_user(username)
    return app.state.auth.create_session(user_id=record["id"] if record else "", long_lived=True)


def _make_client(app, token):
    transport = ASGITransport(app=app)
    return AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies={"taos_session": token},
        event_hooks=csrf_event_hooks(),
    )


@pytest.mark.asyncio
class TestGeneratedMediaIsolation:
    """Tests that generated images and music are isolated per user."""

    @pytest_asyncio.fixture
    async def two_user_setup(self, tmp_data_dir):
        """Set up app with two users: admin (user A) and alice (user B)."""
        app = await _create_app_with_users(tmp_data_dir, ["admin", "alice"])

        admin_token = _get_token(app, "admin")
        alice_token = _get_token(app, "alice")

        admin_client = _make_client(app, admin_token)
        alice_client = _make_client(app, alice_token)

        # Also create an unauthenticated client
        transport = ASGITransport(app=app)
        anon_client = AsyncClient(transport=transport, base_url="http://test")

        yield {
            "app": app,
            "admin_client": admin_client,
            "alice_client": alice_client,
            "anon_client": anon_client,
            "admin_id": app.state.auth.find_user("admin")["id"],
            "alice_id": app.state.auth.find_user("alice")["id"],
        }

        await admin_client.aclose()
        await alice_client.aclose()
        await anon_client.aclose()
        await app.state.qmd_client.close()
        await app.state.http_client.aclose()
        await app.state.metrics.close()

    @pytest_asyncio.fixture
    async def three_user_setup(self, tmp_data_dir):
        """Set up app with three users: admin, alice (user B), bob (user C, non-admin)."""
        app = await _create_app_with_users(tmp_data_dir, ["admin", "alice", "bob"])

        admin_token = _get_token(app, "admin")
        alice_token = _get_token(app, "alice")
        bob_token = _get_token(app, "bob")

        admin_client = _make_client(app, admin_token)
        alice_client = _make_client(app, alice_token)
        bob_client = _make_client(app, bob_token)

        # Also create an unauthenticated client
        transport = ASGITransport(app=app)
        anon_client = AsyncClient(transport=transport, base_url="http://test")

        yield {
            "app": app,
            "admin_client": admin_client,
            "alice_client": alice_client,
            "bob_client": bob_client,
            "anon_client": anon_client,
            "admin_id": app.state.auth.find_user("admin")["id"],
            "alice_id": app.state.auth.find_user("alice")["id"],
            "bob_id": app.state.auth.find_user("bob")["id"],
        }

        await admin_client.aclose()
        await alice_client.aclose()
        await bob_client.aclose()
        await anon_client.aclose()
        await app.state.qmd_client.close()
        await app.state.http_client.aclose()
        await app.state.metrics.close()

    async def _mock_image_backend(self, fake_image_b64):
        """Helper to mock the image generation backend."""
        mock_request = HttpxRequest("POST", "http://localhost:8080/v1/images/generations")
        mock_response = Response(
            status_code=200,
            json={"data": [{"b64_json": fake_image_b64}]},
            request=mock_request,
        )
        mock_instance = AsyncMock()
        mock_instance.post.return_value = mock_response
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=False)
        return mock_instance

    # ============ IMAGES TESTS ============

    async def test_image_generated_by_alice_not_accessible_by_admin_via_static_route(self, two_user_setup):
        """RED: Admin (user A) should NOT access Alice's (user B) generated image via /data/workspace/images/generated/<file>.

        This is the vulnerability: currently returns 200, should return 403/404 after fix.
        """
        app = two_user_setup["app"]
        alice_client = two_user_setup["alice_client"]
        admin_client = two_user_setup["admin_client"]
        alice_id = two_user_setup["alice_id"]

        # Alice generates an image
        fake_image = base64.b64encode(b"alice-secret-image").decode()
        with patch("tinyagentos.routes.images.httpx.AsyncClient") as MockClient:
            MockClient.return_value = await self._mock_image_backend(fake_image)
            resp = await alice_client.post("/api/images/generate", json={
                "prompt": "alice's secret",
                "seed": 12345,
            })

        assert resp.status_code == 200
        data = resp.json()
        filename = data["filename"]
        assert filename.endswith("_12345.png")

        # The returned path is the OLD shared path: /data/workspace/images/generated/<file>
        # After fix, it should be /data/workspace/users/<alice_id>/images/generated/<file>
        old_shared_path = f"/data/workspace/images/generated/{filename}"

        # Admin tries to access Alice's image via the old shared path
        # CURRENTLY THIS RETURNS 200 (vulnerability) - should be 403/404 after fix
        resp = await admin_client.get(old_shared_path)

        # RED ASSERTION: This MUST fail on current code (returns 200)
        # After fix, should be 403 or 404
        assert resp.status_code in (403, 404), (
            f"VULNERABILITY: Admin accessed Alice's image via shared path! "
            f"Got {resp.status_code}, expected 403/404. "
            f"This proves member A can read member B's generated media."
        )

    async def test_image_generated_by_alice_accessible_by_alice_via_user_scoped_path(self, two_user_setup):
        """After fix: Alice should access her own image via user-scoped path."""
        app = two_user_setup["app"]
        alice_client = two_user_setup["alice_client"]
        alice_id = two_user_setup["alice_id"]

        fake_image = base64.b64encode(b"alice-secret-image").decode()
        with patch("tinyagentos.routes.images.httpx.AsyncClient") as MockClient:
            MockClient.return_value = await self._mock_image_backend(fake_image)
            resp = await alice_client.post("/api/images/generate", json={
                "prompt": "alice's secret",
                "seed": 12345,
            })

        assert resp.status_code == 200
        data = resp.json()
        filename = data["filename"]

        # After fix, the returned path should be user-scoped
        user_scoped_path = f"/data/workspace/users/{alice_id}/images/generated/{filename}"

        # Alice accesses her own image via user-scoped path
        resp = await alice_client.get(user_scoped_path)

        # Should succeed for owner
        assert resp.status_code == 200, f"Owner should access own image: got {resp.status_code}"
        assert resp.content == b"alice-secret-image"

    async def test_image_unauthenticated_access_returns_401(self, two_user_setup):
        """Unauthenticated access to generated images must return 401."""
        app = two_user_setup["app"]
        alice_client = two_user_setup["alice_client"]
        anon_client = two_user_setup["anon_client"]
        alice_id = two_user_setup["alice_id"]

        fake_image = base64.b64encode(b"alice-secret-image").decode()
        with patch("tinyagentos.routes.images.httpx.AsyncClient") as MockClient:
            MockClient.return_value = await self._mock_image_backend(fake_image)
            resp = await alice_client.post("/api/images/generate", json={
                "prompt": "alice's secret",
                "seed": 12345,
            })

        data = resp.json()
        filename = data["filename"]
        user_scoped_path = f"/data/workspace/users/{alice_id}/images/generated/{filename}"

        resp = await anon_client.get(user_scoped_path)
        assert resp.status_code == 401, f"Unauthenticated should get 401, got {resp.status_code}"

    async def test_image_path_traversal_rejected(self, two_user_setup):
        """Path traversal attempts on generated images must be rejected."""
        app = two_user_setup["app"]
        admin_client = two_user_setup["admin_client"]

        # Try to traverse out of the workspace
        resp = await admin_client.get("/data/workspace/users/../../etc/passwd")
        assert resp.status_code in (400, 404), f"Traversal should be blocked, got {resp.status_code}"

    async def test_image_generated_by_alice_not_accessible_by_bob_via_user_scoped_path(self, three_user_setup):
        """Non-admin member (bob) must NOT access another member's (alice) generated image via user-scoped path.

        This tests the DENY branch of require_owner_or_admin: bob is neither the owner nor an admin.
        """
        app = three_user_setup["app"]
        alice_client = three_user_setup["alice_client"]
        bob_client = three_user_setup["bob_client"]
        alice_id = three_user_setup["alice_id"]
        bob_id = three_user_setup["bob_id"]

        # Alice generates an image
        fake_image = base64.b64encode(b"alice-secret-image").decode()
        with patch("tinyagentos.routes.images.httpx.AsyncClient") as MockClient:
            MockClient.return_value = await self._mock_image_backend(fake_image)
            resp = await alice_client.post("/api/images/generate", json={
                "prompt": "alice's secret",
                "seed": 12345,
            })

        assert resp.status_code == 200
        data = resp.json()
        filename = data["filename"]
        user_scoped_path = data["path"]  # Use server-provided path

        # Verify the path is user-scoped to alice
        assert user_scoped_path == f"/data/workspace/users/{alice_id}/images/generated/{filename}"

        # Alice (owner) accesses her own image - should succeed
        resp = await alice_client.get(user_scoped_path)
        assert resp.status_code == 200, f"Owner should access own image: got {resp.status_code}"

        # Bob (other non-admin member) tries to access Alice's image - should be forbidden
        resp = await bob_client.get(user_scoped_path)
        assert resp.status_code == 403, (
            f"VULNERABILITY: Bob (non-admin member) accessed Alice's image! "
            f"Got {resp.status_code}, expected 403. "
            f"This proves member A can read member B's generated media."
        )

    # ============ MUSIC TESTS ============

    async def test_music_generated_by_alice_not_accessible_by_admin_via_static_route(self, two_user_setup):
        """RED: Admin (user A) should NOT access Alice's (user B) generated music via /data/workspace/music/generated/<file>.

        This is the vulnerability: currently returns 200, should return 403/404 after fix.
        """
        app = two_user_setup["app"]
        alice_client = two_user_setup["alice_client"]
        admin_client = two_user_setup["admin_client"]
        alice_id = two_user_setup["alice_id"]

        # Set up music backend
        app.state.config.server["music_backend_url"] = "http://localhost:9000"

        # Mock music backend
        fake_wav = base64.b64encode(b"alice-secret-music").decode()
        mock_request = HttpxRequest("POST", "http://localhost:9000/v1/audio/generations")
        mock_response = Response(
            status_code=200,
            json={"data": [{"b64_json": fake_wav}]},
            request=mock_request,
        )
        mock_instance = AsyncMock()
        mock_instance.post.return_value = mock_response
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("tinyagentos.routes.music._http_backend_reachable", new=AsyncMock(return_value=True)),
            patch("tinyagentos.routes.music.httpx.AsyncClient") as MockClient,
        ):
            MockClient.return_value = mock_instance
            resp = await alice_client.post("/api/music/compose", json={
                "prompt": "alice's secret beat",
                "duration": 5,
            })

        assert resp.status_code == 200
        data = resp.json()
        filename = data["filename"]

        # The returned path is the OLD shared path: /data/workspace/music/generated/<file>
        old_shared_path = f"/data/workspace/music/generated/{filename}"

        # Admin tries to access Alice's music via the old shared path
        # CURRENTLY THIS RETURNS 200 (vulnerability) - should be 403/404 after fix
        resp = await admin_client.get(old_shared_path)

        # RED ASSERTION: This MUST fail on current code (returns 200)
        # After fix, should be 403 or 404
        assert resp.status_code in (403, 404), (
            f"VULNERABILITY: Admin accessed Alice's music via shared path! "
            f"Got {resp.status_code}, expected 403/404. "
            f"This proves member A can read member B's generated media."
        )

    async def test_music_generated_by_alice_accessible_by_alice_via_user_scoped_path(self, two_user_setup):
        """After fix: Alice should access her own music via user-scoped path."""
        app = two_user_setup["app"]
        alice_client = two_user_setup["alice_client"]
        alice_id = two_user_setup["alice_id"]

        # Set up music backend
        app.state.config.server["music_backend_url"] = "http://localhost:9000"

        fake_wav = base64.b64encode(b"alice-secret-music").decode()
        mock_request = HttpxRequest("POST", "http://localhost:9000/v1/audio/generations")
        mock_response = Response(
            status_code=200,
            json={"data": [{"b64_json": fake_wav}]},
            request=mock_request,
        )
        mock_instance = AsyncMock()
        mock_instance.post.return_value = mock_response
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("tinyagentos.routes.music._http_backend_reachable", new=AsyncMock(return_value=True)),
            patch("tinyagentos.routes.music.httpx.AsyncClient") as MockClient,
        ):
            MockClient.return_value = mock_instance
            resp = await alice_client.post("/api/music/compose", json={
                "prompt": "alice's secret beat",
                "duration": 5,
            })

        assert resp.status_code == 200
        data = resp.json()
        filename = data["filename"]

        # After fix, the returned path should be user-scoped
        user_scoped_path = f"/data/workspace/users/{alice_id}/music/generated/{filename}"

        # Alice accesses her own music via user-scoped path
        resp = await alice_client.get(user_scoped_path)

        # Should succeed for owner
        assert resp.status_code == 200, f"Owner should access own music: got {resp.status_code}"
        assert resp.content == b"alice-secret-music"

    async def test_music_unauthenticated_access_returns_401(self, two_user_setup):
        """Unauthenticated access to generated music must return 401."""
        app = two_user_setup["app"]
        alice_client = two_user_setup["alice_client"]
        anon_client = two_user_setup["anon_client"]
        alice_id = two_user_setup["alice_id"]

        # Set up music backend
        app.state.config.server["music_backend_url"] = "http://localhost:9000"

        fake_wav = base64.b64encode(b"alice-secret-music").decode()
        mock_request = HttpxRequest("POST", "http://localhost:9000/v1/audio/generations")
        mock_response = Response(
            status_code=200,
            json={"data": [{"b64_json": fake_wav}]},
            request=mock_request,
        )
        mock_instance = AsyncMock()
        mock_instance.post.return_value = mock_response
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("tinyagentos.routes.music._http_backend_reachable", new=AsyncMock(return_value=True)),
            patch("tinyagentos.routes.music.httpx.AsyncClient") as MockClient,
        ):
            MockClient.return_value = mock_instance
            resp = await alice_client.post("/api/music/compose", json={
                "prompt": "alice's secret beat",
                "duration": 5,
            })

        data = resp.json()
        filename = data["filename"]
        user_scoped_path = f"/data/workspace/users/{alice_id}/music/generated/{filename}"

        resp = await anon_client.get(user_scoped_path)
        assert resp.status_code == 401, f"Unauthenticated should get 401, got {resp.status_code}"

    async def test_music_path_traversal_rejected(self, two_user_setup):
        """Path traversal attempts on generated music must be rejected."""
        app = two_user_setup["app"]
        admin_client = two_user_setup["admin_client"]

        resp = await admin_client.get("/data/workspace/users/../../etc/passwd")
        assert resp.status_code in (400, 404), f"Traversal should be blocked, got {resp.status_code}"

    async def test_music_generated_by_alice_not_accessible_by_bob_via_user_scoped_path(self, three_user_setup):
        """Non-admin member (bob) must NOT access another member's (alice) generated music via user-scoped path.

        This tests the DENY branch of require_owner_or_admin: bob is neither the owner nor an admin.
        """
        app = three_user_setup["app"]
        alice_client = three_user_setup["alice_client"]
        bob_client = three_user_setup["bob_client"]
        alice_id = three_user_setup["alice_id"]
        bob_id = three_user_setup["bob_id"]

        # Set up music backend
        app.state.config.server["music_backend_url"] = "http://localhost:9000"

        fake_wav = base64.b64encode(b"alice-secret-music").decode()
        mock_request = HttpxRequest("POST", "http://localhost:9000/v1/audio/generations")
        mock_response = Response(
            status_code=200,
            json={"data": [{"b64_json": fake_wav}]},
            request=mock_request,
        )
        mock_instance = AsyncMock()
        mock_instance.post.return_value = mock_response
        mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
        mock_instance.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("tinyagentos.routes.music._http_backend_reachable", new=AsyncMock(return_value=True)),
            patch("tinyagentos.routes.music.httpx.AsyncClient") as MockClient,
        ):
            MockClient.return_value = mock_instance
            resp = await alice_client.post("/api/music/compose", json={
                "prompt": "alice's secret beat",
                "duration": 5,
            })

        assert resp.status_code == 200
        data = resp.json()
        filename = data["filename"]
        user_scoped_path = data["path"]  # Use server-provided path

        # Verify the path is user-scoped to alice
        assert user_scoped_path == f"/data/workspace/users/{alice_id}/music/generated/{filename}"

        # Alice (owner) accesses her own music - should succeed
        resp = await alice_client.get(user_scoped_path)
        assert resp.status_code == 200, f"Owner should access own music: got {resp.status_code}"

        # Bob (other non-admin member) tries to access Alice's music - should be forbidden
        resp = await bob_client.get(user_scoped_path)
        assert resp.status_code == 403, (
            f"VULNERABILITY: Bob (non-admin member) accessed Alice's music! "
            f"Got {resp.status_code}, expected 403. "
            f"This proves member A can read member B's generated media."
        )
"""Test hub route admin checks for non-admin member access.

This test file verifies the security fix where non-admin members cannot access
the box owner's private hub content (friends-only posts and blobs). The four
handlers have been updated to check user.is_admin before allowing access.

Following the pattern from tests/projects/test_routes_lists.py lines 380-406,
we test both the failing case (non-admin tries to access owner's content) and
the control case (admin can still access).
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from types import SimpleNamespace
from unittest.mock import Mock
from fastapi import HTTPException

from tinyagentos.hub import identity, posts, store as hub_store

# Import the handlers to test
from tinyagentos.routes.hub import (
    own_chain,
    own_timeline,
    get_blob,
    delete_post_route,
    put_own_profile,
    follow_peer,
    grant_cache,
    send_friend_request,
    accept_friend_request,
    decline_friend_request,
    block_peer,
    mute_peer,
    unmute_peer,
    create_post,
)
from tinyagentos.routes.hub import (
    ProfileIn,
    FollowIn,
    CacheGrantIn,
    FriendRequestIn,
    PeerIn,
    AcceptIn,
    PostIn,
)


@pytest.fixture(autouse=True)
def _isolate_hub_data(tmp_data_dir, monkeypatch):
    """Isolate hub data per test (mirrors tests/test_routes_hub_chain_export.py)."""
    monkeypatch.setenv("TAOS_DATA_DIR", str(tmp_data_dir))
    return tmp_data_dir


@pytest.fixture
def _png_data_uri():
    """Create a simple PNG for testing image attachments."""
    import base64
    import io
    from PIL import Image

    img = Image.new("RGB", (8, 8), (10, 120, 200))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


async def _create_owner_post(client, text: str, **extra) -> dict:
    """Helper to create a post as the box owner (mirrors tests/test_routes_hub_chain_export.py)."""
    resp = await client.post("/api/hub/posts", json={"text": text, **extra})
    assert resp.status_code == 200
    return resp.json()["post"]


@pytest.mark.asyncio
class TestHubMemberAccess:
    """Test that non-admin members cannot access owner's private hub content."""

    async def test_timeline_for_non_admin_raises_403(self, client, tmp_data_dir, monkeypatch):
        """Non-admin cannot read owner's timeline (friends-only posts)."""
        # Set up isolated hub data
        monkeypatch.setenv("TAOS_DATA_DIR", str(tmp_data_dir))
        
        # Create an identity and set up stores
        identity.load_or_create()
        store = hub_store.HubStore(hub_store.default_db_path())
        await store.init()
        
        # Create friends-only post as owner
        await _create_owner_post(client, "friends only content", visibility="circle")
        
        # Create a mock request object
        class MockRequest:
            app = client._transport.app
            
        request = MockRequest()
        
        # Create a non-admin user
        non_admin_user = SimpleNamespace(
            user_id="member-2",
            is_admin=False
        )
        
        # Try to access timeline as non-admin member - should raise 403
        try:
            result = await own_timeline(request, non_admin_user)
            assert False, "Expected HTTPException to be raised"
        except Exception as e:
            assert isinstance(e, HTTPException)
            assert e.status_code == 403
            assert e.detail == "forbidden"
        
        await store.close()

    async def test_chain_for_non_admin_raises_403(self, client, tmp_data_dir, monkeypatch):
        """Non-admin cannot read owner's chain (includes friends-only posts)."""
        # Set up isolated hub data
        monkeypatch.setenv("TAOS_DATA_DIR", str(tmp_data_dir))
        
        # Create an identity and set up stores
        identity.load_or_create()
        store = hub_store.HubStore(hub_store.default_db_path())
        await store.init()
        
        # Create friends-only post as owner
        await _create_owner_post(client, "friends only in chain", visibility="circle")
        
        # Create a mock request object
        class MockRequest:
            app = client._transport.app
            
        request = MockRequest()
        
        # Create a non-admin user
        non_admin_user = SimpleNamespace(
            user_id="member-2",
            is_admin=False
        )
        
        # Try to access chain as non-admin member - should raise 403
        try:
            result = await own_chain(request, after_seq=0, limit=100, user=non_admin_user)
            assert False, "Expected HTTPException to be raised"
        except Exception as e:
            assert isinstance(e, HTTPException)
            assert e.status_code == 403
            assert e.detail == "forbidden"
        
        await store.close()

    async def test_blob_for_non_admin_raises_403(self, client, tmp_data_dir, monkeypatch, _png_data_uri):
        """Non-admin cannot read owner's blob (ingested image from friends-only post)."""
        # Set up isolated hub data
        monkeypatch.setenv("TAOS_DATA_DIR", str(tmp_data_dir))
        
        # Create an identity and set up stores
        identity.load_or_create()
        store = hub_store.HubStore(hub_store.default_db_path())
        await store.init()
        
        # Create friends-only post with image attachment
        post = await _create_owner_post(client, "pic", attachments=[{"data": _png_data_uri, "mime": "image/png"}])
        blob_hash = post["attachments"][0]["blob"]
        
        # Create a mock request object
        class MockRequest:
            app = client._transport.app
            
        request = MockRequest()
        
        # Create a non-admin user
        non_admin_user = SimpleNamespace(
            user_id="member-2",
            is_admin=False
        )
        
        # Try to access blob as non-admin member - should raise 403
        try:
            result = await get_blob(blob_hash, request, non_admin_user)
            assert False, "Expected HTTPException to be raised"
        except Exception as e:
            assert isinstance(e, HTTPException)
            assert e.status_code == 403
            assert e.detail == "forbidden"
        
        await store.close()

    async def test_delete_for_non_admin_raises_403(self, client, tmp_data_dir, monkeypatch):
        """Non-admin cannot delete owner's post."""
        # Set up isolated hub data
        monkeypatch.setenv("TAOS_DATA_DIR", str(tmp_data_dir))
        
        # Create an identity and set up stores
        identity.load_or_create()
        store = hub_store.HubStore(hub_store.default_db_path())
        await store.init()
        
        # Create post as owner
        post = await _create_owner_post(client, "to delete")
        post_hash = post["hash"]
        
        # Create a mock request object
        class MockRequest:
            app = client._transport.app
            
        request = MockRequest()
        
        # Create a non-admin user
        non_admin_user = SimpleNamespace(
            user_id="member-2",
            is_admin=False
        )
        
        # Try to delete post as non-admin member - should raise 403
        try:
            result = await delete_post_route(post_hash, request, non_admin_user)
            assert False, "Expected HTTPException to be raised"
        except Exception as e:
            assert isinstance(e, HTTPException)
            assert e.status_code == 403
            assert e.detail == "forbidden"
        
        await store.close()

    # --- Control tests: admin users can still access ---

    async def test_timeline_for_admin_succeeds(self, client, tmp_data_dir, monkeypatch):
        """Admin can read owner's timeline (friends-only posts are visible)."""
        # Set up isolated hub data
        monkeypatch.setenv("TAOS_DATA_DIR", str(tmp_data_dir))
        
        # Create an identity and set up stores
        identity.load_or_create()
        store = hub_store.HubStore(hub_store.default_db_path())
        await store.init()
        
        # Create friends-only post as owner
        await _create_owner_post(client, "friends only content for admin", visibility="circle")
        
        # Create a mock request object
        class MockRequest:
            app = client._transport.app
            
        request = MockRequest()
        
        # Create an admin user (using owner as admin)
        admin_user = SimpleNamespace(
            user_id="owner",
            is_admin=True
        )
        
        # Access timeline as admin - should succeed
        result = await own_timeline(request, admin_user)
        assert result["state"] == "ok"
        # The friends-only post should be in the timeline (admin can see it)
        assert len(result["posts"]) == 1
        assert result["posts"][0]["body"]["text"] == "friends only content for admin"
        
        await store.close()

    async def test_chain_for_admin_succeeds(self, client, tmp_data_dir, monkeypatch):
        """Admin can read owner's chain (includes friends-only posts)."""
        # Set up isolated hub data
        monkeypatch.setenv("TAOS_DATA_DIR", str(tmp_data_dir))
        
        # Create an identity and set up stores
        identity.load_or_create()
        store = hub_store.HubStore(hub_store.default_db_path())
        await store.init()
        
        # Create friends-only post as owner
        await _create_owner_post(client, "friends only in chain for admin", visibility="circle")
        
        # Create a mock request object
        class MockRequest:
            app = client._transport.app
            
        request = MockRequest()
        
        # Create an admin user (using owner as admin)
        admin_user = SimpleNamespace(
            user_id="owner",
            is_admin=True
        )
        
        # Access chain as admin - should succeed
        result = await own_chain(request, after_seq=0, limit=100, user=admin_user)
        assert result["state"] == "ok"
        # The friends-only post should be in the chain entries
        assert len(result["entries"]) == 1
        assert result["entries"][0]["object"]["body"]["text"] == "friends only in chain for admin"
        
        await store.close()

    async def test_blob_for_admin_succeeds(self, client, tmp_data_dir, monkeypatch, _png_data_uri):
        """Admin can read owner's blob (ingested image from friends-only post)."""
        # Set up isolated hub data
        monkeypatch.setenv("TAOS_DATA_DIR", str(tmp_data_dir))
        
        # Create an identity and set up stores
        identity.load_or_create()
        store = hub_store.HubStore(hub_store.default_db_path())
        await store.init()
        
        # Create friends-only post with image attachment
        post = await _create_owner_post(client, "pic for admin", attachments=[{"data": _png_data_uri, "mime": "image/png"}])
        blob_hash = post["attachments"][0]["blob"]
        
        # Create a mock request object
        class MockRequest:
            app = client._transport.app
            
        request = MockRequest()
        
        # Create an admin user (using owner as admin)
        admin_user = SimpleNamespace(
            user_id="owner",
            is_admin=True
        )
        
        # Access blob as admin - should succeed
        result = await get_blob(blob_hash, request, admin_user)
        assert result.status_code == 200
        assert result.headers["content-type"] == "image/webp"
        assert len(result.body) == post["attachments"][0]["size"]
        
        await store.close()

    async def test_delete_for_admin_succeeds(self, client, tmp_data_dir, monkeypatch):
        """Admin can delete owner's post."""
        # Set up isolated hub data
        monkeypatch.setenv("TAOS_DATA_DIR", str(tmp_data_dir))
        
        # Create an identity and set up stores
        identity.load_or_create()
        store = hub_store.HubStore(hub_store.default_db_path())
        await store.init()
        
        # Create post as owner
        post = await _create_owner_post(client, "to delete as admin")
        post_hash = post["hash"]
        
        # Create a mock request object
        class MockRequest:
            app = client._transport.app
            
        request = MockRequest()
        
        # Create an admin user (using owner as admin)
        admin_user = SimpleNamespace(
            user_id="owner",
            is_admin=True
        )
        
        # Delete post as admin - should succeed
        result = await delete_post_route(post_hash, request, admin_user)
        assert result["state"] == "ok"
        assert result["tombstone"]["type"] == "tombstone"
        assert result["tombstone"]["target"] == post_hash
        
        await store.close()

    # --- Write route tests: non-admin members get 403, store unchanged ---

    async def test_put_profile_for_non_admin_raises_403(self, client, tmp_data_dir, monkeypatch):
        """Non-admin cannot update profile (acts as box identity)."""
        monkeypatch.setenv("TAOS_DATA_DIR", str(tmp_data_dir))
        identity.load_or_create()
        store = hub_store.HubStore(hub_store.default_db_path())
        await store.init()

        class MockRequest:
            app = client._transport.app
        request = MockRequest()
        non_admin_user = SimpleNamespace(user_id="member-2", is_admin=False)
        body = ProfileIn(display_name="hacker", bio="stolen")

        try:
            result = await put_own_profile(body, request, non_admin_user)
            assert False, "Expected HTTPException to be raised"
        except Exception as e:
            assert isinstance(e, HTTPException)
            assert e.status_code == 403
            assert e.detail == "forbidden"

        # Store unchanged: no profile exists
        fingerprint = identity.signing_fingerprint()
        profile = await store.get_profile(fingerprint)
        assert profile is None

        await store.close()

    async def test_follow_for_non_admin_raises_403(self, client, tmp_data_dir, monkeypatch):
        """Non-admin cannot follow (acts as box identity)."""
        monkeypatch.setenv("TAOS_DATA_DIR", str(tmp_data_dir))
        identity.load_or_create()
        store = hub_store.HubStore(hub_store.default_db_path())
        await store.init()

        class MockRequest:
            app = client._transport.app
        request = MockRequest()
        non_admin_user = SimpleNamespace(user_id="member-2", is_admin=False)
        body = FollowIn(target_fingerprint="a" * 64)

        try:
            result = await follow_peer(body, request, non_admin_user)
            assert False, "Expected HTTPException to be raised"
        except Exception as e:
            assert isinstance(e, HTTPException)
            assert e.status_code == 403
            assert e.detail == "forbidden"

        # Store unchanged: no follow relationship
        fingerprint = identity.signing_fingerprint()
        follows = await store.list_relationships("follow_out")
        assert follows == []

        await store.close()

    async def test_cache_grant_for_non_admin_raises_403(self, client, tmp_data_dir, monkeypatch):
        """Non-admin cannot grant cache (acts as box identity)."""
        monkeypatch.setenv("TAOS_DATA_DIR", str(tmp_data_dir))
        identity.load_or_create()
        store = hub_store.HubStore(hub_store.default_db_path())
        await store.init()

        class MockRequest:
            app = client._transport.app
        request = MockRequest()
        non_admin_user = SimpleNamespace(user_id="member-2", is_admin=False)
        body = CacheGrantIn(grantee_fingerprint="b" * 64)

        try:
            result = await grant_cache(body, request, non_admin_user)
            assert False, "Expected HTTPException to be raised"
        except Exception as e:
            assert isinstance(e, HTTPException)
            assert e.status_code == 403
            assert e.detail == "forbidden"

        # Store unchanged: no cache grant
        grants = await store.list_relationships("cache_grant")
        assert grants == []

        await store.close()

    async def test_friend_request_for_non_admin_raises_403(self, client, tmp_data_dir, monkeypatch):
        """Non-admin cannot send friend request (acts as box identity)."""
        monkeypatch.setenv("TAOS_DATA_DIR", str(tmp_data_dir))
        identity.load_or_create()
        store = hub_store.HubStore(hub_store.default_db_path())
        await store.init()

        class MockRequest:
            app = client._transport.app
        request = MockRequest()
        non_admin_user = SimpleNamespace(user_id="member-2", is_admin=False)
        body = FriendRequestIn(target_fingerprint="c" * 64)

        try:
            result = await send_friend_request(body, request, non_admin_user)
            assert False, "Expected HTTPException to be raised"
        except Exception as e:
            assert isinstance(e, HTTPException)
            assert e.status_code == 403
            assert e.detail == "forbidden"

        # Store unchanged: no outgoing request recorded
        requests_out = await store.list_relationships("request_out")
        assert requests_out == []

        await store.close()

    async def test_friend_accept_for_non_admin_raises_403(self, client, tmp_data_dir, monkeypatch):
        """Non-admin cannot accept friend request (acts as box identity)."""
        monkeypatch.setenv("TAOS_DATA_DIR", str(tmp_data_dir))
        identity.load_or_create()
        store = hub_store.HubStore(hub_store.default_db_path())
        await store.init()

        class MockRequest:
            app = client._transport.app
        request = MockRequest()
        non_admin_user = SimpleNamespace(user_id="member-2", is_admin=False)
        body = AcceptIn(peer_fingerprint="d" * 64)

        try:
            result = await accept_friend_request("rid-1", body, request, non_admin_user)
            assert False, "Expected HTTPException to be raised"
        except Exception as e:
            assert isinstance(e, HTTPException)
            assert e.status_code == 403
            assert e.detail == "forbidden"

        # Store unchanged: no friend edge
        friends = await store.list_relationships("friend")
        assert friends == []

        await store.close()

    async def test_friend_decline_for_non_admin_raises_403(self, client, tmp_data_dir, monkeypatch):
        """Non-admin cannot decline friend request (acts as box identity)."""
        monkeypatch.setenv("TAOS_DATA_DIR", str(tmp_data_dir))
        identity.load_or_create()
        store = hub_store.HubStore(hub_store.default_db_path())
        await store.init()

        class MockRequest:
            app = client._transport.app
        request = MockRequest()
        non_admin_user = SimpleNamespace(user_id="member-2", is_admin=False)

        try:
            result = await decline_friend_request("rid-2", request, non_admin_user)
            assert False, "Expected HTTPException to be raised"
        except Exception as e:
            assert isinstance(e, HTTPException)
            assert e.status_code == 403
            assert e.detail == "forbidden"

        # Store unchanged: no declined request recorded
        declined = await store.list_relationships("request_declined")
        assert declined == []

        await store.close()

    async def test_block_for_non_admin_raises_403(self, client, tmp_data_dir, monkeypatch):
        """Non-admin cannot block peer (acts as box identity)."""
        monkeypatch.setenv("TAOS_DATA_DIR", str(tmp_data_dir))
        identity.load_or_create()
        store = hub_store.HubStore(hub_store.default_db_path())
        await store.init()

        class MockRequest:
            app = client._transport.app
        request = MockRequest()
        non_admin_user = SimpleNamespace(user_id="member-2", is_admin=False)
        body = PeerIn(peer_fingerprint="e" * 64)

        try:
            result = await block_peer(body, request, non_admin_user)
            assert False, "Expected HTTPException to be raised"
        except Exception as e:
            assert isinstance(e, HTTPException)
            assert e.status_code == 403
            assert e.detail == "forbidden"

        # Store unchanged: no block edge
        blocks = await store.list_relationships("block")
        assert blocks == []

        await store.close()

    async def test_mute_for_non_admin_raises_403(self, client, tmp_data_dir, monkeypatch):
        """Non-admin cannot mute peer (acts as box identity)."""
        monkeypatch.setenv("TAOS_DATA_DIR", str(tmp_data_dir))
        identity.load_or_create()
        store = hub_store.HubStore(hub_store.default_db_path())
        await store.init()

        class MockRequest:
            app = client._transport.app
        request = MockRequest()
        non_admin_user = SimpleNamespace(user_id="member-2", is_admin=False)
        body = PeerIn(peer_fingerprint="f" * 64)

        try:
            result = await mute_peer(body, request, non_admin_user)
            assert False, "Expected HTTPException to be raised"
        except Exception as e:
            assert isinstance(e, HTTPException)
            assert e.status_code == 403
            assert e.detail == "forbidden"

        # Store unchanged: no mute edge
        mutes = await store.list_relationships("mute")
        assert mutes == []

        await store.close()

    async def test_unmute_for_non_admin_raises_403(self, client, tmp_data_dir, monkeypatch):
        """Non-admin cannot unmute peer (acts as box identity)."""
        monkeypatch.setenv("TAOS_DATA_DIR", str(tmp_data_dir))
        identity.load_or_create()
        store = hub_store.HubStore(hub_store.default_db_path())
        await store.init()

        class MockRequest:
            app = client._transport.app
        request = MockRequest()
        non_admin_user = SimpleNamespace(user_id="member-2", is_admin=False)
        body = PeerIn(peer_fingerprint="g" * 64)

        try:
            result = await unmute_peer(body, request, non_admin_user)
            assert False, "Expected HTTPException to be raised"
        except Exception as e:
            assert isinstance(e, HTTPException)
            assert e.status_code == 403
            assert e.detail == "forbidden"

        # Store unchanged: no mute edge to delete (already none)
        mutes = await store.list_relationships("mute")
        assert mutes == []

        await store.close()

    async def test_create_post_for_non_admin_raises_403(self, client, tmp_data_dir, monkeypatch):
        """Non-admin cannot create post (acts as box identity)."""
        monkeypatch.setenv("TAOS_DATA_DIR", str(tmp_data_dir))
        identity.load_or_create()
        store = hub_store.HubStore(hub_store.default_db_path())
        await store.init()

        class MockRequest:
            app = client._transport.app
        request = MockRequest()
        non_admin_user = SimpleNamespace(user_id="member-2", is_admin=False)
        body = PostIn(text="hacked post")

        try:
            result = await create_post(body, request, non_admin_user)
            assert False, "Expected HTTPException to be raised"
        except Exception as e:
            assert isinstance(e, HTTPException)
            assert e.status_code == 403
            assert e.detail == "forbidden"

        # Store unchanged: no post created
        fingerprint = identity.signing_fingerprint()
        posts_list = await store.list_posts(fingerprint)
        assert posts_list == []

        await store.close()

    # --- Control tests: admin users can still access (one per route family) ---

    async def test_put_profile_for_admin_succeeds(self, client, tmp_data_dir, monkeypatch):
        """Admin can update profile (box owner identity)."""
        monkeypatch.setenv("TAOS_DATA_DIR", str(tmp_data_dir))
        identity.load_or_create()
        store = hub_store.HubStore(hub_store.default_db_path())
        await store.init()

        class MockRequest:
            app = client._transport.app
        request = MockRequest()
        admin_user = SimpleNamespace(user_id="owner", is_admin=True)
        body = ProfileIn(display_name="Owner", bio="My bio")

        result = await put_own_profile(body, request, admin_user)
        assert result["state"] == "ok"
        assert result["profile"]["display_name"] == "Owner"
        assert result["profile"]["bio"] == "My bio"

        await store.close()

    async def test_follow_for_admin_succeeds(self, client, tmp_data_dir, monkeypatch):
        """Admin can follow (box owner identity)."""
        monkeypatch.setenv("TAOS_DATA_DIR", str(tmp_data_dir))
        identity.load_or_create()
        store = hub_store.HubStore(hub_store.default_db_path())
        await store.init()

        class MockRequest:
            app = client._transport.app
        request = MockRequest()
        admin_user = SimpleNamespace(user_id="owner", is_admin=True)
        body = FollowIn(target_fingerprint="a" * 64)

        result = await follow_peer(body, request, admin_user)
        assert result["state"] == "following"
        assert result["target"] == "a" * 64

        await store.close()

    async def test_friend_request_for_admin_succeeds(self, client, tmp_data_dir, monkeypatch):
        """Admin can send friend request (box owner identity)."""
        monkeypatch.setenv("TAOS_DATA_DIR", str(tmp_data_dir))
        identity.load_or_create()
        store = hub_store.HubStore(hub_store.default_db_path())
        await store.init()

        class MockRequest:
            app = client._transport.app
            headers = {}
            url = type('URL', (), {'scheme': 'http'})()
        request = MockRequest()
        # Share the store with the handler
        request.app.state.hub_store = store
        admin_user = SimpleNamespace(user_id="owner", is_admin=True)
        body = FriendRequestIn(target_fingerprint="c" * 64)

        # Note: this will fail at the directory forward, but the local store
        # should have the request_out recorded before the forward
        try:
            result = await send_friend_request(body, request, admin_user)
        except Exception:
            pass  # directory forward may fail in test env

        # Local request_out should be recorded (kind is "friend_request_out")
        requests_out = await store.list_relationships("friend_request_out")
        assert len(requests_out) == 1
        assert requests_out[0]["peer"] == "c" * 64

        await store.close()

    async def test_create_post_for_admin_succeeds(self, client, tmp_data_dir, monkeypatch):
        """Admin can create post (box owner identity)."""
        monkeypatch.setenv("TAOS_DATA_DIR", str(tmp_data_dir))
        identity.load_or_create()
        store = hub_store.HubStore(hub_store.default_db_path())
        await store.init()

        class MockRequest:
            app = client._transport.app
        request = MockRequest()
        admin_user = SimpleNamespace(user_id="owner", is_admin=True)
        body = PostIn(text="admin post", visibility="circle")

        result = await create_post(body, request, admin_user)
        assert result["state"] == "ok"
        assert result["post"]["body"]["text"] == "admin post"
        assert "hash" in result["post"]

        await store.close()

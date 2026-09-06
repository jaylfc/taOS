"""End-to-end tests for the device pairing consent loop (S4e).

Creation and polling are unauthenticated (the opaque pair_request_id is the
capability); approval/denial runs through the Decisions answer route as the
admin the Decision was addressed to, exercising _apply_device_pairing_grant
for real rather than calling the store directly.
"""
import pytest


async def _create(client, platform="ios", display_name="My Phone"):
    resp = await client.post(
        "/api/devices/pair-requests",
        json={"platform": platform, "display_name": display_name},
    )
    assert resp.status_code == 200
    return resp.json()


async def _pairing_decision_for(client, pair_request_id):
    items = (await client.get("/api/decisions")).json()["items"]
    matches = [
        d for d in items
        if (d.get("metadata") or {}).get("kind") == "device_pairing"
        and (d.get("metadata") or {}).get("pair_request_id") == pair_request_id
    ]
    assert len(matches) == 1, f"expected exactly one pairing decision, got {len(matches)}"
    return matches[0]


@pytest.mark.asyncio
class TestDevicePairRequests:
    async def test_create_returns_code_once_and_poll_never_leaks_it(self, client):
        body = await _create(client)
        assert len(body["verify_code"]) == 6 and body["verify_code"].isdigit()

        poll = (await client.get(f"/api/devices/pair-requests/{body['pair_request_id']}")).json()
        assert poll["status"] == "pending"
        assert "verify_code" not in poll

    async def test_invalid_platform_400_and_unknown_id_404(self, client):
        resp = await client.post("/api/devices/pair-requests", json={"platform": "windows"})
        assert resp.status_code == 400
        assert (await client.get("/api/devices/pair-requests/nope")).status_code == 404

    async def test_pending_cap_429(self, client):
        for _ in range(5):
            await _create(client)
        resp = await client.post("/api/devices/pair-requests", json={"platform": "ios"})
        assert resp.status_code == 429

    async def test_approve_mints_device_and_releases_token_once(self, client, app):
        body = await _create(client)
        pid = body["pair_request_id"]
        decision = await _pairing_decision_for(client, pid)

        resp = await client.post(
            f"/api/decisions/{decision['id']}/answer", json={"value": "approve"}
        )
        assert resp.status_code == 200

        # First poll: accepted, device row (sans scoped_token) + the one-time token.
        poll = (await client.get(f"/api/devices/pair-requests/{pid}")).json()
        assert poll["status"] == "accepted"
        assert poll["device"]["platform"] == "ios"
        assert "scoped_token" not in poll["device"]
        token = poll["scoped_token"]
        assert token.startswith("taosdev_")

        # F1: the device is bound to the user the Decision was addressed to.
        row = await app.state.device_store.get(poll["device"]["device_id"])
        assert row["user_id"] == decision["user_id"]

        # The token authenticates, and is never handed out a second time.
        assert (await app.state.device_store.get_by_token(token)) is not None
        again = (await client.get(f"/api/devices/pair-requests/{pid}")).json()
        assert again["status"] == "accepted"
        assert "scoped_token" not in again

    async def test_deny_transitions_and_mints_nothing(self, client, app):
        body = await _create(client)
        pid = body["pair_request_id"]
        decision = await _pairing_decision_for(client, pid)

        resp = await client.post(
            f"/api/decisions/{decision['id']}/answer", json={"value": "deny"}
        )
        assert resp.status_code == 200

        poll = (await client.get(f"/api/devices/pair-requests/{pid}")).json()
        assert poll["status"] == "denied"
        assert "scoped_token" not in poll and "device" not in poll
        # No device was minted for the deciding user.
        assert await app.state.device_store.list_for_user(decision["user_id"]) == []

    async def test_expired_request_cannot_be_approved(self, client, app):
        """F6: expiry is enforced at approve time -- an approval landing after
        the TTL persists 'expired' and mints nothing."""
        body = await _create(client)
        pid = body["pair_request_id"]
        decision = await _pairing_decision_for(client, pid)

        store = app.state.device_pair_requests
        await store._db.execute(
            "UPDATE device_pair_requests SET expires_at_ts = '2000-01-01T00:00:00+00:00' "
            "WHERE id = ?",
            (pid,),
        )
        await store._db.commit()

        resp = await client.post(
            f"/api/decisions/{decision['id']}/answer", json={"value": "approve"}
        )
        assert resp.status_code == 200

        poll = (await client.get(f"/api/devices/pair-requests/{pid}")).json()
        assert poll["status"] == "expired"
        assert "scoped_token" not in poll and "device" not in poll
        assert await app.state.device_store.list_for_user(decision["user_id"]) == []

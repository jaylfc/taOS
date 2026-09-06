"""Security regression tests for device pairing and blocking (audit #2233/#2238).

Red-first: each test is written against the post-merge code and must FAIL
before the corresponding fix lands, then PASS after.
"""
import asyncio

import pytest


@pytest.mark.asyncio
class TestDevicePairSecurity:
    async def test_concurrent_creates_cannot_bypass_pending_cap(self, client, app):
        """FINDING 1 (unauthenticated cap race): count_pending() and create()
        are not atomic. Concurrent requests can exceed _PENDING_CAP."""
        store = app.state.device_pair_requests
        await store._db.execute("DELETE FROM device_pair_requests")
        await store._db.commit()

        await store.create(
            platform="ios",
            display_name="filler-1",
            verify_code="123456",
            requester_ip="10.0.0.1",
        )
        await store.create(
            platform="ios",
            display_name="filler-2",
            verify_code="123456",
            requester_ip="10.0.0.1",
        )
        await store.create(
            platform="ios",
            display_name="filler-3",
            verify_code="123456",
            requester_ip="10.0.0.1",
        )
        await store.create(
            platform="ios",
            display_name="filler-4",
            verify_code="123456",
            requester_ip="10.0.0.1",
        )
        pending_before = await store.count_pending()
        assert pending_before == 4

        async def _create():
            return await client.post(
                "/api/devices/pair-requests",
                json={"platform": "ios", "display_name": "racer"},
            )

        # No return_exceptions: a server-side crash must fail the test, not
        # empty the status list into a vacuous pass.
        results = await asyncio.gather(*[_create() for _ in range(5)])
        statuses = sorted(r.status_code for r in results)
        assert statuses == [200, 429, 429, 429, 429], (
            f"cap race: expected exactly one success at the cap, got {statuses}"
        )

    async def test_pair_request_requires_admin_presence(self, client, app):
        """FINDING 2 (200-on-no-admin): when no admin exists, create_pair_request
        must not return 200 because the request can never be approved."""
        auth = app.state.auth
        data = auth._read_users()
        for u in data.get("users", []):
            u["is_admin"] = False
        auth._write_users(data)

        resp = await client.post(
            "/api/devices/pair-requests",
            json={"platform": "ios", "display_name": "Orphan"},
        )
        assert resp.status_code == 409, (
            "pair request must be rejected with 409 when no admin exists to "
            f"approve it, got {resp.status_code}"
        )

    async def test_forged_display_name_cannot_impersonate_approved_device(
        self, client, app
    ):
        """FINDING 3 (client-supplied device identity): a client must not be
        able to forge identity attributes that cause the approved device to
        impersonate an existing device."""
        existing = (
            await client.post(
                "/api/devices/register",
                json={"platform": "ios", "display_name": "Real Device"},
            )
        ).json()
        existing_id = existing["device_id"]

        body = await client.post(
            "/api/devices/pair-requests",
            json={"platform": "ios", "display_name": existing["display_name"]},
        )
        assert body.status_code == 200
        pid = body.json()["pair_request_id"]

        decision = await self._pairing_decision_for(client, pid)
        resp = await client.post(
            f"/api/decisions/{decision['id']}/answer", json={"value": "approve"}
        )
        assert resp.status_code == 200

        poll = (await client.get(f"/api/devices/pair-requests/{pid}")).json()
        assert poll["status"] == "accepted"
        new_id = poll["device"]["device_id"]
        assert new_id != existing_id, (
            "forged display_name must not reuse an existing device_id"
        )

    async def test_display_name_respects_max_length(self, client):
        """CreatePairRequest must reject over-length display_name."""
        resp = await client.post(
            "/api/devices/pair-requests",
            json={"platform": "ios", "display_name": "x" * 201},
        )
        assert resp.status_code == 422

    async def _pairing_decision_for(self, client, pair_request_id):
        items = (await client.get("/api/decisions")).json()["items"]
        matches = [
            d for d in items
            if (d.get("metadata") or {}).get("kind") == "device_pairing"
            and (d.get("metadata") or {}).get("pair_request_id") == pair_request_id
        ]
        assert len(matches) == 1, f"expected exactly one pairing decision, got {len(matches)}"
        return matches[0]

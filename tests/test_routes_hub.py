"""Local hub API routes (hub social slice 2).

The Hub app reads and writes the node's own profile through these routes. The
tests exercise the degrade states (no identity yet -> ``no-identity``; identity
but no profile -> ``no-profile``), profile create then update with a version
bump, and that an unknown kind is rejected. The ``TAOS_DATA_DIR`` override points
the identity keystore and hub store at the test's temp dir so nothing touches a
real data dir.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_hub_data(tmp_data_dir, monkeypatch):
    # Both the identity keystore and the hub store resolve from TAOS_DATA_DIR, so
    # pointing it at the per-test data dir keeps every request hermetic.
    monkeypatch.setenv("TAOS_DATA_DIR", str(tmp_data_dir))
    return tmp_data_dir


class TestHubProfileRoutes:
    @pytest.mark.asyncio
    async def test_get_profile_reports_no_identity_before_first_use(self, client):
        resp = await client.get("/api/hub/profile")
        assert resp.status_code == 200
        assert resp.json() == {"state": "no-identity"}

    @pytest.mark.asyncio
    async def test_put_then_get_profile(self, client):
        resp = await client.put(
            "/api/hub/profile",
            json={"kind": "personal", "display_name": "Alice", "bio": "hi"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["state"] == "ok"
        assert body["profile"]["display_name"] == "Alice"
        assert body["profile"]["version"] == 1
        assert body["profile"]["sig"]

        # Reading it back returns the same current profile.
        resp = await client.get("/api/hub/profile")
        assert resp.status_code == 200
        got = resp.json()
        assert got["state"] == "ok"
        assert got["profile"]["display_name"] == "Alice"
        assert got["profile"]["version"] == 1

    @pytest.mark.asyncio
    async def test_update_bumps_version(self, client):
        await client.put("/api/hub/profile", json={"display_name": "Alice"})
        resp = await client.put(
            "/api/hub/profile", json={"display_name": "Alice B.", "bio": "updated"}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["profile"]["version"] == 2
        assert body["profile"]["display_name"] == "Alice B."

        resp = await client.get("/api/hub/profile")
        assert resp.json()["profile"]["version"] == 2

    @pytest.mark.asyncio
    async def test_business_kind_is_accepted(self, client):
        resp = await client.put(
            "/api/hub/profile",
            json={"kind": "business", "display_name": "Acme"},
        )
        assert resp.status_code == 200
        assert resp.json()["profile"]["kind"] == "business"

    @pytest.mark.asyncio
    async def test_invalid_kind_is_rejected(self, client):
        resp = await client.put(
            "/api/hub/profile",
            json={"kind": "robot", "display_name": "x"},
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_profile_is_signed_by_the_node_identity(self, client):
        from tinyagentos.hub import identity
        from tinyagentos.hub import store as hub_store

        resp = await client.put("/api/hub/profile", json={"display_name": "Alice"})
        profile = resp.json()["profile"]
        pub = identity.public_identity()["signing_pubkey"]
        assert hub_store.verify_object(profile, pub) is True
        assert profile["author"] == identity.signing_fingerprint()

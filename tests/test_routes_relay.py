import pytest


@pytest.mark.asyncio
class TestRelayAuthorize:
    async def test_no_username_header_denies(self, client):
        r = await client.get("/api/relay/authorize")
        assert r.status_code == 200
        assert r.json() == {"allow": False}

    async def test_unknown_user_denies(self, client):
        r = await client.get(
            "/api/relay/authorize",
            headers={"X-Taos-Username": "nonexistent"},
        )
        assert r.status_code == 200
        assert r.json() == {"allow": False}

    async def test_user_without_entitlement_denies(self, client, app):
        app.state.auth.set_remote_relay_pro("admin", False)
        r = await client.get(
            "/api/relay/authorize",
            headers={"X-Taos-Username": "admin"},
        )
        assert r.status_code == 200
        assert r.json() == {"allow": False}

    async def test_user_with_entitlement_allows(self, client, app):
        app.state.auth.set_remote_relay_pro("admin", True)
        r = await client.get(
            "/api/relay/authorize",
            headers={"X-Taos-Username": "admin"},
        )
        assert r.status_code == 200
        assert r.json() == {"allow": True}

    async def test_entitlement_is_per_user_not_global(self, client, app):
        app.state.auth.set_remote_relay_pro("admin", True)
        r = await client.get(
            "/api/relay/authorize",
            headers={"X-Taos-Username": "other"},
        )
        assert r.status_code == 200
        assert r.json() == {"allow": False}


@pytest.mark.asyncio
class TestRelayTlsAllow:
    async def test_no_username_header_denies(self, client):
        r = await client.get("/api/relay/tls-allow")
        assert r.status_code == 200
        assert r.json() == {"allow": False}

    async def test_unknown_user_denies(self, client):
        r = await client.get(
            "/api/relay/tls-allow",
            headers={"X-Taos-Username": "nonexistent"},
        )
        assert r.status_code == 200
        assert r.json() == {"allow": False}

    async def test_user_with_entitlement_allows(self, client, app):
        app.state.auth.set_remote_relay_pro("admin", True)
        r = await client.get(
            "/api/relay/tls-allow",
            headers={"X-Taos-Username": "admin"},
        )
        assert r.status_code == 200
        assert r.json() == {"allow": True}

    async def test_user_without_entitlement_denies(self, client, app):
        app.state.auth.set_remote_relay_pro("admin", False)
        r = await client.get(
            "/api/relay/tls-allow",
            headers={"X-Taos-Username": "admin"},
        )
        assert r.status_code == 200
        assert r.json() == {"allow": False}

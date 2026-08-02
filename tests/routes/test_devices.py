import pytest


@pytest.mark.asyncio
class TestDeviceRoutes:
    async def test_register_returns_scoped_token_and_scopes_to_session_user(self, client):
        resp = await client.post(
            "/api/devices/register",
            json={"platform": "ios", "display_name": "iPhone", "user_id": "attacker"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["platform"] == "ios"
        assert body["scoped_token"].startswith("taosdev_")
        # Body user_id is ignored; the session user owns the device.
        assert body["user_id"] != "attacker"

    async def test_list_hides_scoped_token(self, client):
        await client.post("/api/devices/register", json={"platform": "ios"})
        resp = await client.get("/api/devices")
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 1
        assert "scoped_token" not in items[0]

    async def test_update_push_token(self, client):
        reg = (await client.post("/api/devices/register", json={"platform": "ios"})).json()
        resp = await client.patch(
            f"/api/devices/{reg['device_id']}/push-token", json={"push_token": "abc123"}
        )
        assert resp.status_code == 200
        assert resp.json()["push_token"] == "abc123"
        assert "scoped_token" not in resp.json()

    async def test_revoke_then_absent_from_list(self, client):
        reg = (await client.post("/api/devices/register", json={"platform": "ios"})).json()
        resp = await client.delete(f"/api/devices/{reg['device_id']}")
        assert resp.status_code == 200 and resp.json()["revoked"] is True
        assert (await client.get("/api/devices")).json()["items"] == []

    async def test_cannot_touch_another_users_device(self, client, app):
        # Register a device owned by a different user directly in the store.
        other = await app.state.device_store.register(user_id="someone-else", platform="ios")
        assert (await client.delete(f"/api/devices/{other['device_id']}")).status_code == 404
        assert (
            await client.patch(
                f"/api/devices/{other['device_id']}/push-token", json={"push_token": "x"}
            )
        ).status_code == 404

    # --- Device-bearer push-token rotation (lock-screen self-service) ---

    def _bearer(self, token: str) -> dict:
        return {"Authorization": f"Bearer {token}"}

    def _admin_uid(self, app) -> str:
        return app.state.auth.find_user("admin")["id"]

    async def _register_device(self, app, user_id: str) -> dict:
        return await app.state.device_store.register(
            user_id=user_id, platform="ios", display_name="lock-screen"
        )

    async def test_device_bearer_updates_own_push_token(self, client, app):
        """Criterion 2: a device bearer can PATCH its OWN push-token -> 200."""
        uid = self._admin_uid(app)
        device = await self._register_device(app, uid)
        resp = await client.patch(
            f"/api/devices/{device['device_id']}/push-token",
            json={"push_token": "new-token"},
            headers=self._bearer(device["scoped_token"]),
        )
        assert resp.status_code == 200
        assert resp.json()["push_token"] == "new-token"
        assert "scoped_token" not in resp.json()

    async def test_device_bearer_sibling_device_refused(self, client, app):
        """Criterion 2 (Invariant b): a sibling device of the same user cannot
        PATCH another sibling's push-token."""
        uid = self._admin_uid(app)
        dev_a = await self._register_device(app, uid)
        dev_b = await self._register_device(app, uid)
        resp = await client.patch(
            f"/api/devices/{dev_b['device_id']}/push-token",
            json={"push_token": "hijack"},
            headers=self._bearer(dev_a["scoped_token"]),
        )
        assert resp.status_code == 404
        # The sibling's token is untouched.
        got = await app.state.device_store.get(dev_b["device_id"])
        assert got["push_token"] != "hijack"

    async def test_device_bearer_other_user_device_refused(self, client, app):
        """Criterion 2: a device bearer cannot PATCH another user's device."""
        uid = self._admin_uid(app)
        dev_a = await self._register_device(app, uid)
        dev_other = await self._register_device(app, "non-admin-user-1")
        resp = await client.patch(
            f"/api/devices/{dev_other['device_id']}/push-token",
            json={"push_token": "hijack"},
            headers=self._bearer(dev_a["scoped_token"]),
        )
        assert resp.status_code == 404

    async def test_device_bearer_revoked_token_401(self, client, app):
        """Criterion 2: a revoked device token gets 401."""
        uid = self._admin_uid(app)
        device = await self._register_device(app, uid)
        await app.state.device_store.revoke(device["device_id"])
        resp = await client.patch(
            f"/api/devices/{device['device_id']}/push-token",
            json={"push_token": "new-token"},
            headers=self._bearer(device["scoped_token"]),
        )
        assert resp.status_code == 401

    async def test_session_push_token_still_works(self, client, app):
        """Criterion 5: existing user-session behaviour unchanged."""
        reg = (await client.post("/api/devices/register", json={"platform": "ios"})).json()
        resp = await client.patch(
            f"/api/devices/{reg['device_id']}/push-token",
            json={"push_token": "session-token"},
        )
        assert resp.status_code == 200
        assert resp.json()["push_token"] == "session-token"

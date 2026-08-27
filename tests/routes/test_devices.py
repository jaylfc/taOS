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

    async def test_block_unblock_repair_flow(self, client):
        reg = (
            await client.post(
                "/api/devices/register", json={"platform": "ios", "push_token": "pt-flow"}
            )
        ).json()

        resp = await client.post(f"/api/devices/{reg['device_id']}/block")
        assert resp.status_code == 200
        assert resp.json() == {"blocked": True, "changed": True}

        # Blocked device stays listed, with the derived live_token flag off.
        items = (await client.get("/api/devices")).json()["items"]
        assert [d["device_id"] for d in items] == [reg["device_id"]]
        assert items[0]["live_token"] is False

        # Re-pairing under the same push token is refused while blocked.
        resp = await client.post(
            "/api/devices/register", json={"platform": "ios", "push_token": "pt-flow"}
        )
        assert resp.status_code == 403

        resp = await client.post(f"/api/devices/{reg['device_id']}/unblock")
        assert resp.status_code == 200
        assert resp.json() == {"unblocked": True, "changed": True}

        # After unblock the device may re-pair (fresh registration, fresh token).
        resp = await client.post(
            "/api/devices/register", json={"platform": "ios", "push_token": "pt-flow"}
        )
        assert resp.status_code == 200
        assert resp.json()["scoped_token"] != reg["scoped_token"]

    async def test_live_token_flag_true_for_active_device(self, client):
        await client.post("/api/devices/register", json={"platform": "ios"})
        items = (await client.get("/api/devices")).json()["items"]
        assert items[0]["live_token"] is True

    async def test_block_unblock_other_users_device_404(self, client, app):
        other = await app.state.device_store.register(user_id="someone-else", platform="ios")
        assert (await client.post(f"/api/devices/{other['device_id']}/block")).status_code == 404
        assert (await client.post(f"/api/devices/{other['device_id']}/unblock")).status_code == 404
        # And the foreign device was not touched.
        row = await app.state.device_store.get(other["device_id"])
        assert row["blocked"] == 0 and row["revoked"] == 0

    async def test_blocked_token_rejected_on_real_bearer_route(self, client, app):
        """Criterion (a): a BLOCKED device token is rejected on a real
        device-bearer route (PATCH push-token), not just at the store."""
        uid = self._admin_uid(app)
        device = await self._register_device(app, uid)
        await app.state.device_store.block(device["device_id"])
        resp = await client.patch(
            f"/api/devices/{device['device_id']}/push-token",
            json={"push_token": "new"},
            headers=self._bearer(device["scoped_token"]),
        )
        assert resp.status_code == 401

    async def test_revoke_does_not_affect_sibling_device(self, client, app):
        """Criterion (d): revoking device A does not affect device B on the
        same account -- B can still use its live_token on a real bearer route."""
        uid = self._admin_uid(app)
        dev_a = await self._register_device(app, uid)
        dev_b = await self._register_device(app, uid)
        await app.state.device_store.revoke(dev_a["device_id"])
        # A is dead.
        assert await app.state.device_store.get_by_token(dev_a["scoped_token"]) is None
        # B still authenticates on a real bearer route.
        resp = await client.patch(
            f"/api/devices/{dev_b['device_id']}/push-token",
            json={"push_token": "b-works"},
            headers=self._bearer(dev_b["scoped_token"]),
        )
        assert resp.status_code == 200
        assert resp.json()["push_token"] == "b-works"

    async def test_blocked_device_consumes_max_devices_slot(self, client, monkeypatch):
        """Pinned behaviour: a blocked device counts against _MAX_DEVICES_PER_USER
        until unblocked."""
        import tinyagentos.routes.devices as devices_mod
        monkeypatch.setattr(devices_mod, "_MAX_DEVICES_PER_USER", 2)
        # Fill the cap with two active devices.
        await client.post("/api/devices/register", json={"platform": "ios"})
        await client.post("/api/devices/register", json={"platform": "ios"})
        # Cap reached.
        resp = await client.post("/api/devices/register", json={"platform": "ios"})
        assert resp.status_code == 429
        # Block one device. It still consumes a slot (list_for_user includes
        # blocked rows), so the cap is still reached.
        items = (await client.get("/api/devices")).json()["items"]
        blocked_id = items[0]["device_id"]
        await client.post(f"/api/devices/{blocked_id}/block")
        resp = await client.post("/api/devices/register", json={"platform": "ios"})
        assert resp.status_code == 429
        # Unblock: the slot frees, registration succeeds again.
        await client.post(f"/api/devices/{blocked_id}/unblock")
        resp = await client.post("/api/devices/register", json={"platform": "ios"})
        assert resp.status_code == 200

    async def test_register_android_accepts_url_push_token(self, client):
        reg = await client.post(
            "/api/devices/register",
            json={"platform": "android", "push_token": "https://example.com/endpoint"},
        )
        assert reg.status_code == 200
        assert reg.json()["push_token"] == "https://example.com/endpoint"

    async def test_register_android_rejects_non_url_push_token(self, client):
        resp = await client.post(
            "/api/devices/register",
            json={"platform": "android", "push_token": "not-a-url"},
        )
        assert resp.status_code == 422

    async def test_android_push_token_round_trips_url(self, client, app):
        reg = (await client.post(
            "/api/devices/register",
            json={"platform": "android", "push_token": "https://example.com/old"},
        )).json()
        resp = await client.patch(
            f"/api/devices/{reg['device_id']}/push-token",
            json={"push_token": "https://example.com/new"},
        )
        assert resp.status_code == 200
        assert resp.json()["push_token"] == "https://example.com/new"

    async def test_android_push_token_rejects_non_url_on_patch(self, client, app):
        reg = (await client.post(
            "/api/devices/register",
            json={"platform": "android", "push_token": "https://example.com/ok"},
        )).json()
        resp = await client.patch(
            f"/api/devices/{reg['device_id']}/push-token",
            json={"push_token": "not-a-url"},
        )
        assert resp.status_code == 422

    async def test_register_rejects_loopback_push_endpoint(self, client):
        resp = await client.post(
            "/api/devices/register",
            json={"platform": "android", "push_token": "http://127.0.0.1:7900/a2a/send"},
        )
        assert resp.status_code == 400

    async def test_register_rejects_decimal_encoded_loopback(self, client):
        resp = await client.post(
            "/api/devices/register",
            json={"platform": "android", "push_token": "http://2130706433:7900/"},
        )
        assert resp.status_code == 400

    async def test_register_accepts_rfc1918_lan_push_endpoint(self, client):
        resp = await client.post(
            "/api/devices/register",
            json={"platform": "android", "push_token": "http://192.168.1.50/ntfy_topic"},
        )
        assert resp.status_code == 200
        assert resp.json()["push_token"] == "http://192.168.1.50/ntfy_topic"

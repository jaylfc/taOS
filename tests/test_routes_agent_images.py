"""Route tests for the base-image management API."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


# `incus image list --format=csv -c lasu` output: alias,arch,size,uploaded.
# Includes a non-taos image that must be filtered out of the response.
_INCUS_LIST_CSV = (
    "taos-openclaw-base,aarch64,412.50MiB,2026/06/01 12:00 UTC\n"
    "taos-base,aarch64,300.00MiB,2026/06/01 12:05 UTC\n"
    "ubuntu/jammy,aarch64,180.00MiB,2026/05/01 00:00 UTC\n"
)


@pytest.mark.asyncio
class TestListAgentImages:
    async def test_lists_only_taos_bases_with_total(self, client):
        with patch(
            "tinyagentos.routes.agent_images._incus_image_rows",
            new=AsyncMock(return_value=[
                {"alias": "taos-openclaw-base", "architecture": "aarch64",
                 "size": "412.50MiB", "uploaded_at": "2026/06/01 12:00 UTC"},
                {"alias": "taos-base", "architecture": "aarch64",
                 "size": "300.00MiB", "uploaded_at": "2026/06/01 12:05 UTC"},
                {"alias": "ubuntu/jammy", "architecture": "aarch64",
                 "size": "180.00MiB", "uploaded_at": "2026/05/01 00:00 UTC"},
            ]),
        ):
            resp = await client.get("/api/agent-images")
        assert resp.status_code == 200
        data = resp.json()
        assert data["incus_available"] is True
        aliases = {img["alias"] for img in data["images"]}
        # Only taos bases, never the unrelated ubuntu image.
        assert aliases == {"taos-openclaw-base", "taos-base"}
        # Framework mapping: openclaw base backs openclaw; generic base is None.
        by_alias = {img["alias"]: img for img in data["images"]}
        assert by_alias["taos-openclaw-base"]["framework"] == "openclaw"
        assert by_alias["taos-base"]["framework"] is None
        # Aggregate total = sum of the two taos base sizes in bytes.
        expected = int(412.50 * 1024 ** 2) + int(300.00 * 1024 ** 2)
        assert data["total_size_bytes"] == expected

    async def test_parses_real_incus_csv_via_subprocess(self, client):
        proc = AsyncMock()
        proc.returncode = 0
        proc.communicate = AsyncMock(return_value=(_INCUS_LIST_CSV.encode(), b""))
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
            resp = await client.get("/api/agent-images")
        assert resp.status_code == 200
        aliases = {img["alias"] for img in resp.json()["images"]}
        assert aliases == {"taos-openclaw-base", "taos-base"}

    async def test_incus_unavailable_returns_empty(self, client):
        with patch(
            "tinyagentos.routes.agent_images._incus_image_rows",
            new=AsyncMock(return_value=None),
        ):
            resp = await client.get("/api/agent-images")
        assert resp.status_code == 200
        data = resp.json()
        assert data["images"] == []
        assert data["total_size_bytes"] == 0
        assert data["incus_available"] is False

    async def test_unauthenticated_returns_401(self, app):
        from httpx import ASGITransport, AsyncClient
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test",
        ) as c:
            resp = await c.get("/api/agent-images")
            assert resp.status_code in (401, 403)


@pytest.mark.asyncio
class TestImportAgentImage:
    async def test_known_alias_invokes_ensure(self, client):
        with patch(
            "tinyagentos.routes.agent_images.ensure_image_present",
            new=AsyncMock(return_value=True),
        ) as mock_ensure:
            resp = await client.post("/api/agent-images/taos-openclaw-base/import")
        assert resp.status_code == 200
        assert resp.json() == {"alias": "taos-openclaw-base", "present": True}
        mock_ensure.assert_awaited_once_with("taos-openclaw-base")

    async def test_unknown_alias_returns_404(self, client):
        with patch(
            "tinyagentos.routes.agent_images.ensure_image_present",
            new=AsyncMock(return_value=True),
        ) as mock_ensure:
            resp = await client.post("/api/agent-images/not-a-taos-base/import")
        assert resp.status_code == 404
        mock_ensure.assert_not_called()


@pytest.mark.asyncio
class TestDeleteAgentImage:
    async def test_taos_alias_invokes_delete(self, client):
        with patch(
            "tinyagentos.routes.agent_images._incus_image_delete",
            new=AsyncMock(return_value=(0, "")),
        ) as mock_del:
            resp = await client.delete("/api/agent-images/taos-base")
        assert resp.status_code == 200
        assert resp.json() == {"alias": "taos-base", "deleted": True}
        mock_del.assert_awaited_once_with("taos-base")

    async def test_non_taos_alias_refused_400_without_delete(self, client):
        with patch(
            "tinyagentos.routes.agent_images._incus_image_delete",
            new=AsyncMock(return_value=(0, "")),
        ) as mock_del:
            resp = await client.delete("/api/agent-images/ubuntu-jammy")
        assert resp.status_code == 400
        mock_del.assert_not_called()

    async def test_incus_failure_returns_502(self, client):
        with patch(
            "tinyagentos.routes.agent_images._incus_image_delete",
            new=AsyncMock(return_value=(1, "image in use")),
        ):
            resp = await client.delete("/api/agent-images/taos-base")
        assert resp.status_code == 502


@pytest.mark.asyncio
class TestPrefetchToggle:
    async def test_enable_persists_and_reflects(self, client, tmp_data_dir):
        with patch(
            "tinyagentos.routes.agent_images.is_prefetch_enabled",
            return_value=False,
        ):
            resp = await client.post(
                "/api/agent-images/prefetch", json={"enabled": True},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["stored"] is True
        assert data["prefetch_enabled"] is True
        # Persisted to data_dir.
        pref = tmp_data_dir / "agent_image_prefs.json"
        assert pref.exists()
        assert '"prefetch_enabled": true' in pref.read_text()

    async def test_disable_persists(self, client, tmp_data_dir):
        with patch(
            "tinyagentos.routes.agent_images.is_prefetch_enabled",
            return_value=False,
        ):
            resp = await client.post(
                "/api/agent-images/prefetch", json={"enabled": False},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["stored"] is False
        assert data["prefetch_enabled"] is False

    async def test_env_var_overrides_disabled_pref(self, client):
        # If the env var is on, effective stays True even when storing False.
        with patch(
            "tinyagentos.routes.agent_images.is_prefetch_enabled",
            return_value=True,
        ):
            resp = await client.post(
                "/api/agent-images/prefetch", json={"enabled": False},
            )
        assert resp.status_code == 200
        assert resp.json()["prefetch_enabled"] is True

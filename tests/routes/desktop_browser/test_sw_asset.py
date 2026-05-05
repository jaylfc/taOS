"""Tests for the /__taos/sw.js Service Worker asset endpoint.

Verifies Content-Type, Service-Worker-Allowed header, Cache-Control, and
key JS content signals.  No auth required — the asset is public.
"""
from __future__ import annotations

import pytest


class TestServiceWorkerAsset:
    @pytest.mark.asyncio
    async def test_returns_javascript(self, client):
        r = await client.get("/__taos/sw.js")
        assert r.status_code == 200
        assert "javascript" in r.headers["content-type"]

    @pytest.mark.asyncio
    async def test_service_worker_allowed_header(self, client):
        r = await client.get("/__taos/sw.js")
        assert r.headers.get("service-worker-allowed") == "/"

    @pytest.mark.asyncio
    async def test_caches_one_hour(self, client):
        r = await client.get("/__taos/sw.js")
        cc = r.headers.get("cache-control", "")
        assert "max-age=3600" in cc

    @pytest.mark.asyncio
    async def test_safe_paths_not_intercepted(self, client):
        r = await client.get("/__taos/sw.js")
        body = r.text
        assert "/api/desktop/browser/" in body
        assert "/__taos/" in body
        assert "shouldIntercept" in body

    @pytest.mark.asyncio
    async def test_message_priming_handler(self, client):
        r = await client.get("/__taos/sw.js")
        body = r.text
        assert "taos-sw:prime" in body
        assert "pageBaseUrl" in body
        assert "profileId" in body or "__taosProfileId" in body

    @pytest.mark.asyncio
    async def test_fetch_handler_present(self, client):
        r = await client.get("/__taos/sw.js")
        body = r.text
        assert "addEventListener('fetch'" in body
        assert "respondWith" in body

    @pytest.mark.asyncio
    async def test_install_and_activate_handlers(self, client):
        r = await client.get("/__taos/sw.js")
        body = r.text
        assert "skipWaiting" in body
        assert "clients.claim" in body

    @pytest.mark.asyncio
    async def test_cross_origin_not_intercepted(self, client):
        """The shouldIntercept logic excludes cross-origin requests."""
        r = await client.get("/__taos/sw.js")
        body = r.text
        assert "self.location.origin" in body

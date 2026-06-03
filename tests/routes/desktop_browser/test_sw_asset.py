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
        assert "addEventListener('activate'" in body

    @pytest.mark.asyncio
    async def test_no_clients_claim(self, client):
        """clients.claim() removed to avoid hijacking the parent shell."""
        r = await client.get("/__taos/sw.js")
        body = r.text
        # The activate handler should be present but NOT call clients.claim()
        assert "addEventListener('activate'" in body
        assert "clients.claim" not in body

    @pytest.mark.asyncio
    async def test_skips_non_get_methods(self, client):
        """The SW skips POST/PUT/DELETE etc. since the proxy is GET-only."""
        r = await client.get("/__taos/sw.js")
        body = r.text
        assert "req.method !== 'GET'" in body
        assert "HEAD" in body

    @pytest.mark.asyncio
    async def test_cross_origin_not_intercepted(self, client):
        """The shouldIntercept logic excludes cross-origin requests."""
        r = await client.get("/__taos/sw.js")
        body = r.text
        assert "self.location.origin" in body

    @pytest.mark.asyncio
    async def test_prime_message_validates_source(self, client):
        """taos-sw:prime handler must reject messages with no source so
        a detached/injected call cannot re-prime the SW (Fix 3)."""
        r = await client.get("/__taos/sw.js")
        body = r.text
        # The handler must check event.source before processing the prime.
        assert "event.source" in body
        assert "if (!event.source)" in body or "if (!event.source) return" in body

    @pytest.mark.asyncio
    async def test_prime_message_validates_profile_id(self, client):
        """profileId must be validated against a safe slug regex so a
        malicious page cannot inject path-traversal characters (Fix 3)."""
        r = await client.get("/__taos/sw.js")
        body = r.text
        # Regex guard for profileId.
        assert "[a-zA-Z0-9_-]" in body

    @pytest.mark.asyncio
    async def test_prime_message_validates_page_base_url_origin(self, client):
        """pageBaseUrl must resolve to this origin; absolute URLs pointing
        elsewhere must be rejected (Fix 3)."""
        r = await client.get("/__taos/sw.js")
        body = r.text
        # Origin check: resolved URL origin must equal self.location.origin.
        assert "resolved.origin !== self.location.origin" in body

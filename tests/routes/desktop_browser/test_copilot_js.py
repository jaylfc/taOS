"""Tests for the /__taos/copilot.js static asset endpoint.

Verifies Content-Type, Cache-Control, stable content, op-table presence,
and the idempotent IIFE guard.  No auth required — the asset is public.
"""
from __future__ import annotations

import pytest


class TestCopilotJsAsset:
    """The /__taos/copilot.js asset is served with the right headers and is stable."""

    @pytest.mark.asyncio
    async def test_returns_javascript_content_type(self, client):
        r = await client.get("/__taos/copilot.js")
        assert r.status_code == 200
        ct = r.headers["content-type"]
        assert "application/javascript" in ct or "text/javascript" in ct

    @pytest.mark.asyncio
    async def test_caches_for_one_day(self, client):
        r = await client.get("/__taos/copilot.js")
        assert r.status_code == 200
        cc = r.headers.get("cache-control", "")
        assert "max-age=86400" in cc
        assert "immutable" in cc

    @pytest.mark.asyncio
    async def test_serves_consistent_bytes(self, client):
        """Two consecutive fetches return identical content (no per-request mutation)."""
        a = await client.get("/__taos/copilot.js")
        b = await client.get("/__taos/copilot.js")
        assert a.status_code == 200 and b.status_code == 200
        assert a.content == b.content

    @pytest.mark.asyncio
    async def test_contains_op_table_keys(self, client):
        """Sanity check: the served file mentions all four read ops."""
        r = await client.get("/__taos/copilot.js")
        body = r.text
        for op in ["extract", "screenshot", "scrollPosition", "findElement"]:
            assert op in body

    @pytest.mark.asyncio
    async def test_idempotent_iife_guard(self, client):
        """The IIFE has the __taosCopilot guard so re-running is a no-op."""
        r = await client.get("/__taos/copilot.js")
        body = r.text
        assert "__taosCopilot" in body

    @pytest.mark.asyncio
    async def test_uses_named_handlers_for_cleanup(self, client):
        """The scroll and submit listeners must be named functions so the close
        handler can remove them. Verify by content inspection."""
        r = await client.get("/__taos/copilot.js")
        body = r.text
        # The cleanup pattern requires named functions or refs.
        assert "function onScroll" in body
        assert "function onSubmit" in body
        assert "removeEventListener('scroll'" in body
        assert "removeEventListener('submit'" in body

    @pytest.mark.asyncio
    async def test_uses_hasOwnProperty_guard_on_op_lookup(self, client):
        r = await client.get("/__taos/copilot.js")
        body = r.text
        assert "hasOwnProperty.call" in body

    @pytest.mark.asyncio
    async def test_uses_css_escape_on_ids(self, client):
        r = await client.get("/__taos/copilot.js")
        body = r.text
        assert "CSS.escape" in body or "CSS && CSS.escape" in body or "window.CSS.escape" in body

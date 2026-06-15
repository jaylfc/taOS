"""Tests for the agent-callable desktop screenshot round-trip."""

import pytest

from tinyagentos.desktop_control.broker import DesktopCommandBroker


class TestResultRegistry:
    @pytest.mark.asyncio
    async def test_register_resolve(self):
        b = DesktopCommandBroker()
        fut = b.register_result("r1", "u1")
        assert not fut.done()
        assert b.resolve_result("r1", {"image": "data:..."}) is True
        assert await fut == {"image": "data:..."}

    @pytest.mark.asyncio
    async def test_resolve_unknown_returns_false(self):
        b = DesktopCommandBroker()
        assert b.resolve_result("missing", {}) is False

    @pytest.mark.asyncio
    async def test_discard_prevents_resolve(self):
        b = DesktopCommandBroker()
        b.register_result("r2", "u1")
        b.discard_result("r2")
        assert b.resolve_result("r2", {}) is False

    @pytest.mark.asyncio
    async def test_double_resolve_is_false(self):
        b = DesktopCommandBroker()
        b.register_result("r3", "u1")
        assert b.resolve_result("r3", {"image": "x"}) is True
        assert b.resolve_result("r3", {"image": "y"}) is False


class TestResultOwnership:
    @pytest.mark.asyncio
    async def test_wrong_user_cannot_resolve(self):
        b = DesktopCommandBroker()
        b.register_result("ro", "owner")
        assert b.resolve_result("ro", {"image": "x"}, user_id="attacker") is False
        assert b.resolve_result("ro", {"image": "x"}, user_id="owner") is True

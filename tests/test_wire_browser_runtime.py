"""Unit tests for wire_browser_runtime helper (sub-plan A integration)."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tinyagentos.browser_sessions import wire_browser_runtime


# ---------------------------------------------------------------------------
# Minimal hardware profile stub (mirrors HardwareProfile dataclass enough)
# ---------------------------------------------------------------------------

def _make_hw_profile(ram_mb: int = 8192):
    """Return a SimpleNamespace that passes dataclasses.asdict via duck-typing."""
    from dataclasses import dataclass, field

    @dataclass
    class FakeCPU:
        model: str = "Test CPU"
        cores: int = 4
        soc: str = ""

    @dataclass
    class FakeGPU:
        type: str = ""
        model: str = ""
        cuda: bool = False
        vulkan: bool = False
        vram_mb: int = 0

    @dataclass
    class FakeProfile:
        ram_mb: int = 8192
        cpu: FakeCPU = field(default_factory=FakeCPU)
        gpu: FakeGPU = field(default_factory=FakeGPU)
        platform: str = "linux"
        profile_id: str = "desktop-8gb"
        npu: str = ""

    return FakeProfile(ram_mb=ram_mb)


# ---------------------------------------------------------------------------
# Fake AgentBrowsersManager
# ---------------------------------------------------------------------------

class _FakeAgentBrowsers:
    """Minimal stub for AgentBrowsersManager."""

    def __init__(self, profiles: list[dict]):
        self._profiles = profiles
        self.list_profiles = AsyncMock(return_value=profiles)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_wire_sets_browser_container_runner():
    """wire_browser_runtime creates a BrowserContainerRunner with the right node_ip."""
    state = SimpleNamespace()
    hw = _make_hw_profile(ram_mb=8192)
    agent_browsers = _FakeAgentBrowsers([])
    browser_sessions_mgr = MagicMock()
    browser_sessions_mgr.migrate_agent_browsers = AsyncMock(return_value=0)

    await wire_browser_runtime(
        state, hw, agent_browsers, browser_sessions_mgr, host_ip="192.168.1.10"
    )

    runner = state.browser_container_runner
    assert runner is not None
    assert runner.node_ip == "192.168.1.10"
    assert runner.hw_profile is hw


@pytest.mark.asyncio
async def test_wire_sets_host_hardware_with_ram_mb():
    """host_hardware dict must expose ram_mb at top level."""
    state = SimpleNamespace()
    hw = _make_hw_profile(ram_mb=16384)
    agent_browsers = _FakeAgentBrowsers([])
    browser_sessions_mgr = MagicMock()
    browser_sessions_mgr.migrate_agent_browsers = AsyncMock(return_value=0)

    await wire_browser_runtime(
        state, hw, agent_browsers, browser_sessions_mgr, host_ip="10.0.0.1"
    )

    assert isinstance(state.host_hardware, dict)
    assert state.host_hardware.get("ram_mb") == 16384


@pytest.mark.asyncio
async def test_wire_calls_migrate_agent_browsers_with_profile_rows():
    """migrate_agent_browsers is called once with the rows from list_profiles."""
    state = SimpleNamespace()
    hw = _make_hw_profile()
    profiles = [
        {"agent_name": "bot-1", "profile_name": "default", "node": None,
         "status": "stopped", "container_id": None},
    ]
    agent_browsers = _FakeAgentBrowsers(profiles)
    browser_sessions_mgr = MagicMock()
    browser_sessions_mgr.migrate_agent_browsers = AsyncMock(return_value=1)

    await wire_browser_runtime(
        state, hw, agent_browsers, browser_sessions_mgr, host_ip="10.0.0.2"
    )

    agent_browsers.list_profiles.assert_awaited_once()
    browser_sessions_mgr.migrate_agent_browsers.assert_awaited_once_with(profiles)


@pytest.mark.asyncio
async def test_wire_does_not_clobber_signing_key():
    """wire_browser_runtime must not touch other app.state attributes."""
    state = SimpleNamespace()
    state.browser_session_signing_key = b"keep-this"
    hw = _make_hw_profile()
    agent_browsers = _FakeAgentBrowsers([])
    browser_sessions_mgr = MagicMock()
    browser_sessions_mgr.migrate_agent_browsers = AsyncMock(return_value=0)

    await wire_browser_runtime(
        state, hw, agent_browsers, browser_sessions_mgr, host_ip="10.0.0.3"
    )

    assert state.browser_session_signing_key == b"keep-this"

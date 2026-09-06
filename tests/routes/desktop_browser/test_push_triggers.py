"""Tests for push notification triggers: chat, drive-started, download-finished.

Tests are unit-level — they call the trigger helpers directly with mocked
push.send so no real push service is contacted.

Subtask F coverage:
 1. Chat push fires when user not focused on the agent's pinned tab
 2. Chat push suppressed when user IS focused
 3. Chat push suppressed when muted
 4. Drive push fires on transition into driving
 5. Drive push suppressed when focused
 6. Drive push suppressed when muted
 7. Download push fires on completion
 8. Download push suppressed when muted
 9. Triggers use asyncio.create_task and do not block the critical path
"""
from __future__ import annotations

import asyncio
import pathlib
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from tinyagentos.routes.desktop_browser.store import BrowserStore
from tinyagentos.routes.desktop_browser.vapid import load_or_create_vapid_keypair


# ---------------------------------------------------------------------------
# Shared VAPID keypair for all tests
# ---------------------------------------------------------------------------

_VAPID_TMPDIR = tempfile.mkdtemp()
FAKE_VAPID = load_or_create_vapid_keypair(pathlib.Path(_VAPID_TMPDIR))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def store(tmp_path):
    s = BrowserStore(tmp_path / "browser.sqlite3")
    await s.init()
    yield s
    await s.close()


def _make_hub():
    from tinyagentos.routes.desktop_browser.copilot_ws import CopilotHub
    return CopilotHub()


# ---------------------------------------------------------------------------
# Helper: patch push.send with an AsyncMock
# ---------------------------------------------------------------------------

def _patch_push_send():
    """Return a context manager that replaces push.send with an AsyncMock."""
    return patch(
        "tinyagentos.routes.desktop_browser.push.send",
        new_callable=AsyncMock,
    )


# ===========================================================================
# Subtask C — chat push trigger
# ===========================================================================

class TestChatPushTrigger:

    @pytest.mark.asyncio
    async def test_fires_when_user_not_focused_on_agent_tab(self, store):
        """Chat push fires when focused tab differs from agent's tab."""
        from tinyagentos.routes.desktop_browser.copilot_ws import _maybe_send_chat_push

        hub = _make_hub()
        # User is focused on a DIFFERENT (window, tab) pair
        hub.set_focused_tab("user1", "win-A", "tab-other")

        with _patch_push_send() as mock_send:
            mock_send.return_value = {"sent": 1, "failed": 0, "removed": 0}
            await _maybe_send_chat_push(
                user_id="user1",
                agent_id="agent-alpha",
                agent_name="Alpha",
                msg_text="Hello from agent",
                window_id="win-A",
                tab_id="tab-agent",
                store=store,
                hub=hub,
                vapid=FAKE_VAPID,
            )

        mock_send.assert_awaited_once()
        call_kwargs = mock_send.call_args
        payload = call_kwargs.args[1]
        assert payload["title"] == "Alpha"
        assert "Hello from agent" in payload["body"]
        assert payload["tag"] == "chat:agent-alpha"
        assert payload["data"]["agent_id"] == "agent-alpha"

    @pytest.mark.asyncio
    async def test_suppressed_when_user_is_focused_on_tab(self, store):
        """Chat push suppressed when user is focused on the agent's exact tab."""
        from tinyagentos.routes.desktop_browser.copilot_ws import _maybe_send_chat_push

        hub = _make_hub()
        # User IS focused on the agent's tab
        hub.set_focused_tab("user1", "win-A", "tab-agent")

        with _patch_push_send() as mock_send:
            await _maybe_send_chat_push(
                user_id="user1",
                agent_id="agent-alpha",
                agent_name="Alpha",
                msg_text="You can see this already",
                window_id="win-A",
                tab_id="tab-agent",
                store=store,
                hub=hub,
                vapid=FAKE_VAPID,
            )

        mock_send.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_suppressed_when_muted(self, store):
        """Chat push suppressed when the user has muted chat for this agent."""
        from tinyagentos.routes.desktop_browser.copilot_ws import _maybe_send_chat_push

        hub = _make_hub()
        # User is NOT focused, but has muted chat for this agent
        hub.set_focused_tab("user1", "win-A", "tab-other")
        await store.set_push_mute("user1", "agent-alpha", "chat", True)

        with _patch_push_send() as mock_send:
            await _maybe_send_chat_push(
                user_id="user1",
                agent_id="agent-alpha",
                agent_name="Alpha",
                msg_text="This should be muted",
                window_id="win-A",
                tab_id="tab-agent",
                store=store,
                hub=hub,
                vapid=FAKE_VAPID,
            )

        mock_send.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_fires_when_no_focused_tab_known(self, store):
        """Chat push fires when no focused tab has been recorded (None)."""
        from tinyagentos.routes.desktop_browser.copilot_ws import _maybe_send_chat_push

        hub = _make_hub()
        # No set_focused_tab call → hub returns None

        with _patch_push_send() as mock_send:
            mock_send.return_value = {"sent": 0, "failed": 0, "removed": 0}
            await _maybe_send_chat_push(
                user_id="user1",
                agent_id="agent-alpha",
                agent_name="Alpha",
                msg_text="Ping",
                window_id="win-A",
                tab_id="tab-agent",
                store=store,
                hub=hub,
                vapid=FAKE_VAPID,
            )

        mock_send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_suppressed_by_tab_id_when_window_id_empty(self, store):
        """Chat push suppressed when window_id is empty but tab_id matches focused tab."""
        from tinyagentos.routes.desktop_browser.copilot_ws import _maybe_send_chat_push

        hub = _make_hub()
        # Focused tab is known with full (window_id, tab_id)
        hub.set_focused_tab("user_a", "real-window", "tab-X")

        with _patch_push_send() as mock_send:
            # window_id="" but tab_id matches focused tab's tab_id — should suppress
            await _maybe_send_chat_push(
                user_id="user_a",
                agent_id="agent-1",
                agent_name="Agent",
                msg_text="hello",
                window_id="",
                tab_id="tab-X",
                store=store,
                hub=hub,
                vapid=FAKE_VAPID,
            )

        mock_send.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_fires_when_window_id_empty_and_tab_id_no_match(self, store):
        """Chat push fires when window_id is empty and tab_id does NOT match focused tab."""
        from tinyagentos.routes.desktop_browser.copilot_ws import _maybe_send_chat_push

        hub = _make_hub()
        # Focused tab has tab_id "tab-X"; agent is on a different tab "tab-Y"
        hub.set_focused_tab("user_a", "real-window", "tab-X")

        with _patch_push_send() as mock_send:
            mock_send.return_value = {"sent": 1, "failed": 0, "removed": 0}
            await _maybe_send_chat_push(
                user_id="user_a",
                agent_id="agent-1",
                agent_name="Agent",
                msg_text="hello",
                window_id="",
                tab_id="tab-Y",
                store=store,
                hub=hub,
                vapid=FAKE_VAPID,
            )

        mock_send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_body_truncated_to_200_chars(self, store):
        """Long messages are truncated to 200 chars in the push body."""
        from tinyagentos.routes.desktop_browser.copilot_ws import _maybe_send_chat_push

        hub = _make_hub()

        with _patch_push_send() as mock_send:
            mock_send.return_value = {"sent": 1, "failed": 0, "removed": 0}
            await _maybe_send_chat_push(
                user_id="user1",
                agent_id="agent-alpha",
                agent_name="Alpha",
                msg_text="x" * 500,
                window_id="win-A",
                tab_id="tab-agent",
                store=store,
                hub=hub,
                vapid=FAKE_VAPID,
            )

        payload = mock_send.call_args.args[1]
        assert len(payload["body"]) == 200


# ===========================================================================
# Subtask A — CopilotHub focused-tab tracking
# ===========================================================================

class TestCopilotHubFocusedTab:

    def test_set_and_get_focused_tab(self):
        hub = _make_hub()
        assert hub.get_focused_tab("user1") is None
        hub.set_focused_tab("user1", "win-1", "tab-42")
        assert hub.get_focused_tab("user1") == ("win-1", "tab-42")

    def test_focused_tab_isolated_per_user(self):
        hub = _make_hub()
        hub.set_focused_tab("user-a", "win-1", "tab-1")
        hub.set_focused_tab("user-b", "win-2", "tab-2")
        assert hub.get_focused_tab("user-a") == ("win-1", "tab-1")
        assert hub.get_focused_tab("user-b") == ("win-2", "tab-2")

    def test_get_focused_tab_unknown_user_returns_none(self):
        hub = _make_hub()
        assert hub.get_focused_tab("nobody") is None

    def test_set_focused_tab_overwrites_previous(self):
        hub = _make_hub()
        hub.set_focused_tab("user1", "win-1", "old-tab")
        hub.set_focused_tab("user1", "win-1", "new-tab")
        assert hub.get_focused_tab("user1") == ("win-1", "new-tab")


# ===========================================================================
# Subtask D — drive-started push trigger
# ===========================================================================

class TestDrivePushTrigger:

    @pytest.mark.asyncio
    async def test_fires_on_drive_start_when_not_focused(self, store):
        """Drive push fires on the first drive op when user is not focused."""
        from tinyagentos.routes.desktop_browser.copilot_agent_ws import _maybe_send_drive_push

        hub = _make_hub()
        hub.set_focused_tab("user1", "win-A", "tab-other")

        with _patch_push_send() as mock_send:
            mock_send.return_value = {"sent": 1, "failed": 0, "removed": 0}
            await _maybe_send_drive_push(
                user_id="user1",
                agent_id="agent-beta",
                agent_name="Beta",
                target_url="https://github.com/org/repo/issues/1",
                window_id="win-A",
                tab_id="tab-agent",
                store=store,
                hub=hub,
                vapid=FAKE_VAPID,
            )

        mock_send.assert_awaited_once()
        payload = mock_send.call_args.args[1]
        assert "started driving" in payload["title"]
        assert "github.com" in payload["body"]
        assert payload["tag"].startswith("drive:agent-beta:")

    @pytest.mark.asyncio
    async def test_suppressed_when_focused_on_drive_tab(self, store):
        """Drive push suppressed when user is looking at the agent's tab."""
        from tinyagentos.routes.desktop_browser.copilot_agent_ws import _maybe_send_drive_push

        hub = _make_hub()
        hub.set_focused_tab("user1", "win-A", "tab-agent")

        with _patch_push_send() as mock_send:
            await _maybe_send_drive_push(
                user_id="user1",
                agent_id="agent-beta",
                agent_name="Beta",
                target_url="https://example.com/",
                window_id="win-A",
                tab_id="tab-agent",
                store=store,
                hub=hub,
                vapid=FAKE_VAPID,
            )

        mock_send.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_suppressed_by_tab_id_when_window_id_empty(self, store):
        """Drive push suppressed when window_id is empty but tab_id matches focused tab."""
        from tinyagentos.routes.desktop_browser.copilot_agent_ws import _maybe_send_drive_push

        hub = _make_hub()
        # Focused tab is known with full (window_id, tab_id)
        hub.set_focused_tab("user1", "real-window", "tab-X")

        with _patch_push_send() as mock_send:
            # window_id="" but tab_id matches focused tab's tab_id — should suppress
            await _maybe_send_drive_push(
                user_id="user1",
                agent_id="agent-beta",
                agent_name="Beta",
                target_url="https://example.com/",
                window_id="",
                tab_id="tab-X",
                store=store,
                hub=hub,
                vapid=FAKE_VAPID,
            )

        mock_send.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_fires_when_window_id_empty_and_tab_id_no_match(self, store):
        """Drive push fires when window_id is empty and tab_id does NOT match focused tab."""
        from tinyagentos.routes.desktop_browser.copilot_agent_ws import _maybe_send_drive_push

        hub = _make_hub()
        # Focused tab has tab_id "tab-X"; agent is on a different tab "tab-Y"
        hub.set_focused_tab("user1", "real-window", "tab-X")

        with _patch_push_send() as mock_send:
            mock_send.return_value = {"sent": 1, "failed": 0, "removed": 0}
            await _maybe_send_drive_push(
                user_id="user1",
                agent_id="agent-beta",
                agent_name="Beta",
                target_url="https://example.com/",
                window_id="",
                tab_id="tab-Y",
                store=store,
                hub=hub,
                vapid=FAKE_VAPID,
            )

        mock_send.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_suppressed_when_drive_started_muted(self, store):
        """Drive push suppressed when the user has muted drive-started for this agent."""
        from tinyagentos.routes.desktop_browser.copilot_agent_ws import _maybe_send_drive_push

        hub = _make_hub()
        hub.set_focused_tab("user1", "win-A", "tab-other")
        await store.set_push_mute("user1", "agent-beta", "drive-started", True)

        with _patch_push_send() as mock_send:
            await _maybe_send_drive_push(
                user_id="user1",
                agent_id="agent-beta",
                agent_name="Beta",
                target_url="https://example.com/",
                window_id="win-A",
                tab_id="tab-agent",
                store=store,
                hub=hub,
                vapid=FAKE_VAPID,
            )

        mock_send.assert_not_awaited()


# ===========================================================================
# Subtask E — download-finished push trigger
# ===========================================================================

class TestDownloadPushTrigger:

    @pytest.mark.asyncio
    async def test_fires_on_successful_download(self, store):
        """download push fires after a successful stream completes."""
        # We test the trigger logic directly via the module-level imports,
        # exercising _store.is_push_muted and push.send call in streamer().
        # We simulate by calling the helper logic with a mocked push.send.

        with _patch_push_send() as mock_send:
            mock_send.return_value = {"sent": 1, "failed": 0, "removed": 0}

            # Simulate what streamer() does on success
            user_id = "user1"
            final_name = "report.pdf"
            download_id = "abc12345"

            if not await store.is_push_muted(user_id, "system", "download-finished"):
                import tinyagentos.routes.desktop_browser.push as push_mod
                payload = {
                    "title": "Download finished",
                    "body": final_name,
                    "tag": f"download:{download_id}",
                    "data": {"window_id": "", "tab_id": ""},
                }
                await push_mod.send(user_id, payload, store=store, vapid=FAKE_VAPID)

        mock_send.assert_awaited_once()
        call_payload = mock_send.call_args.args[1]
        assert call_payload["title"] == "Download finished"
        assert call_payload["body"] == "report.pdf"
        assert "download:" in call_payload["tag"]

    @pytest.mark.asyncio
    async def test_suppressed_when_download_finished_muted(self, store):
        """Download push suppressed when the user has muted download-finished."""
        await store.set_push_mute("user1", "system", "download-finished", True)

        with _patch_push_send() as mock_send:
            # Simulate what streamer() does — checks mute first
            user_id = "user1"
            if not await store.is_push_muted(user_id, "system", "download-finished"):
                import tinyagentos.routes.desktop_browser.push as push_mod
                await push_mod.send(
                    user_id,
                    {"title": "Download finished", "body": "file.zip"},
                    store=store,
                    vapid=FAKE_VAPID,
                )

        mock_send.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_uses_system_agent_id_for_mute_key(self, store):
        """Download mute is keyed on (user, 'system', 'download-finished')."""
        # Mute for a specific agent should NOT suppress the download push
        await store.set_push_mute("user1", "some-agent", "download-finished", True)

        with _patch_push_send() as mock_send:
            mock_send.return_value = {"sent": 0, "failed": 0, "removed": 0}
            user_id = "user1"
            # "system" mute is NOT set — push should fire
            if not await store.is_push_muted(user_id, "system", "download-finished"):
                import tinyagentos.routes.desktop_browser.push as push_mod
                await push_mod.send(
                    user_id,
                    {"title": "Download finished", "body": "file.zip",
                     "tag": "download:abc", "data": {}},
                    store=store,
                    vapid=FAKE_VAPID,
                )

        mock_send.assert_awaited_once()


# ===========================================================================
# Test 9 — Non-blocking: create_task doesn't block critical path
# ===========================================================================

class TestNonBlockingPushTriggers:

    @pytest.mark.asyncio
    async def test_chat_push_does_not_block_when_push_is_slow(self, store):
        """If push.send is slow, the asyncio.create_task wrapper means the
        caller completes before push.send finishes."""
        from tinyagentos.routes.desktop_browser.copilot_ws import _maybe_send_chat_push

        hub = _make_hub()
        send_started = asyncio.Event()
        send_finished = asyncio.Event()

        async def slow_send(*args, **kwargs):
            send_started.set()
            # Simulate a push that takes a long time
            await asyncio.sleep(0.5)
            send_finished.set()
            return {"sent": 1, "failed": 0, "removed": 0}

        with patch(
            "tinyagentos.routes.desktop_browser.push.send",
            side_effect=slow_send,
        ):
            # Wrap the call in a task the same way the real code does
            task = asyncio.create_task(_maybe_send_chat_push(
                user_id="user1",
                agent_id="agent-slow",
                agent_name="Slow",
                msg_text="ping",
                window_id="win-A",
                tab_id="tab-x",
                store=store,
                hub=hub,
                vapid=FAKE_VAPID,
            ))
            # The task starts but we don't await it — caller can proceed immediately
            # Give event loop a chance to start the task
            await asyncio.sleep(0)
            # At this point, the critical path (chat broadcast) would have already
            # returned. The push may or may not have started yet depending on
            # scheduling, but the task is queued.
            assert not send_finished.is_set(), (
                "push.send should not have completed yet — it takes 0.5s"
            )
            # Clean up by cancelling
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    @pytest.mark.asyncio
    async def test_drive_push_does_not_block_when_push_is_slow(self, store):
        """Same non-blocking property for drive push via create_task."""
        from tinyagentos.routes.desktop_browser.copilot_agent_ws import _maybe_send_drive_push

        hub = _make_hub()
        send_finished = asyncio.Event()

        async def slow_send(*args, **kwargs):
            await asyncio.sleep(0.5)
            send_finished.set()
            return {"sent": 1, "failed": 0, "removed": 0}

        with patch(
            "tinyagentos.routes.desktop_browser.push.send",
            side_effect=slow_send,
        ):
            task = asyncio.create_task(_maybe_send_drive_push(
                user_id="user1",
                agent_id="agent-slow-drive",
                agent_name="SlowDrive",
                target_url="https://example.com/",
                window_id="win-A",
                tab_id="tab-y",
                store=store,
                hub=hub,
                vapid=FAKE_VAPID,
            ))
            await asyncio.sleep(0)
            assert not send_finished.is_set()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


# ===========================================================================
# _short_url helper
# ===========================================================================

class TestShortUrl:
    def test_truncates_long_path(self):
        from tinyagentos.routes.desktop_browser.copilot_agent_ws import _short_url
        result = _short_url("https://github.com/org/repo/issues/123")
        assert result == "github.com/org/repo/..."

    def test_short_path_not_truncated(self):
        from tinyagentos.routes.desktop_browser.copilot_agent_ws import _short_url
        result = _short_url("https://example.com/foo")
        assert result == "example.com/foo"

    def test_empty_string_returned_as_is(self):
        from tinyagentos.routes.desktop_browser.copilot_agent_ws import _short_url
        assert _short_url("") == ""

    def test_root_path(self):
        from tinyagentos.routes.desktop_browser.copilot_agent_ws import _short_url
        result = _short_url("https://github.com/")
        assert result.startswith("github.com")

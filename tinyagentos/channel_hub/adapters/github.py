"""GitHub events channel adapter.

Polls ~/.taos-gh-events.jsonl for new webhook events and emits them
as channel-hub messages through the message router.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from tinyagentos.channel_hub.message import IncomingMessage, OutgoingMessage

logger = logging.getLogger(__name__)

EVENTS_LOG_PATH = Path.home() / ".taos-gh-events.jsonl"


class GithubConnector:
    """Connector that tails the GitHub webhook event log and routes events
    through the channel-hub message router.

    Filters:
      repo:       Only process events for this repo (full_name, e.g. "jaylfc/tinyagentos")
      event_kinds: Only process these event types (e.g. ["pull_request", "issue_comment"])
      pr_number:  Only process events for a specific PR number (extracted from URL)
    """

    def __init__(
        self,
        agent_name: str,
        router,
        repo: str | None = None,
        event_kinds: list[str] | None = None,
        pr_number: int | None = None,
    ):
        self.agent_name = agent_name
        self.router = router
        self.repo_filter = repo
        self.event_kinds = event_kinds or []
        self.pr_number = pr_number
        self._running = False
        self._task: asyncio.Task | None = None
        self._last_position = 0  # byte offset in the JSONL file

    async def start(self):
        """Start polling the GitHub event log."""
        self._running = True
        # Skip existing events — only process new ones written after start
        try:
            if EVENTS_LOG_PATH.exists():
                self._last_position = EVENTS_LOG_PATH.stat().st_size
        except OSError:
            pass
        self._task = asyncio.create_task(self._poll_loop())
        logger.info(
            "GitHub connector started for agent '%s'%s%s%s",
            self.agent_name,
            f" repo={self.repo_filter}" if self.repo_filter else "",
            f" events={self.event_kinds}" if self.event_kinds else "",
            f" pr=#{self.pr_number}" if self.pr_number else "",
        )

    async def stop(self):
        """Stop the polling loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None

    async def _poll_loop(self):
        """Poll the JSONL file every 2 seconds for new events."""
        while self._running:
            try:
                await self._check_events()
                await asyncio.sleep(2)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("GitHub poll error: %s", e)
                await asyncio.sleep(5)

    async def _check_events(self):
        """Read new JSONL lines from the event log."""
        if not EVENTS_LOG_PATH.exists():
            return

        try:
            current_size = EVENTS_LOG_PATH.stat().st_size
            if current_size <= self._last_position:
                return

            with open(EVENTS_LOG_PATH, "r", encoding="utf-8") as f:
                f.seek(self._last_position)
                new_data = f.read()
                self._last_position = current_size

            for line in new_data.strip().split("\n"):
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                await self._handle_event(event)
        except OSError:
            pass

    def _event_matches_filters(self, event: dict) -> bool:
        """Check whether an event passes the configured filters."""
        if self.repo_filter and event.get("repo", "") != self.repo_filter:
            return False
        if self.event_kinds and event.get("event", "") not in self.event_kinds:
            return False
        if self.pr_number is not None:
            url = event.get("url", "")
            needle = f"/pull/{self.pr_number}"
            if needle not in url:
                return False
        return True

    async def _handle_event(self, event: dict):
        """Filter and route a single event through the message router."""
        if not self._event_matches_filters(event):
            return

        incoming = self._build_message(event)
        await self.router.route_message(self.agent_name, incoming)

    def _build_message(self, event: dict) -> IncomingMessage:
        """Build an IncomingMessage from a GitHub webhook event dict."""
        event_type = event.get("event", "")
        action = event.get("action", "")
        repo = event.get("repo", "")
        sender = event.get("sender", "")
        url = event.get("url", "")
        timestamp = event.get("timestamp", "")

        text = f"[{event_type}] {sender}"
        if action:
            text += f" {action}"
        if repo:
            text += f" in {repo}"
        if url:
            text += f"\n{url}"

        return IncomingMessage(
            id=f"github:{event_type}:{timestamp}",
            from_id=sender,
            from_name=sender,
            platform="github",
            channel_id=repo,
            channel_name=f"github:{repo}",
            text=text,
            raw={
                "source": "github",
                "event_type": event_type,
                "payload": event,
            },
        )

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def _openclaw_transport() -> str:
    """How taOS drives OpenClaw agents: ``acp`` (fork-free, default) drives the
    agent over the Agent Client Protocol; ``bridge`` uses the legacy forked
    taos-bridge SSE path. Override per-host with TAOS_OPENCLAW_TRANSPORT."""
    return (os.environ.get("TAOS_OPENCLAW_TRANSPORT") or "acp").strip().lower()


class AgentChatRouter:
    """Bridges chat messages into per-agent SSE queues via BridgeSessionRegistry.

    Routing rules (per message):
    - DM channels always route to all non-author members with force_respond=True.
    - Group channels in 'quiet' mode only route to explicitly mentioned agents.
    - Group channels in 'lively' mode fan out to all non-muted members.
    - @mention always sets force_respond=True and bypasses hop cap / cooldown.
    - Hop cap (max_hops) stops agent-authored chains unless overridden by mention.
    - Muted agents are always skipped (even with mention).
    """

    def __init__(self, app_state: Any):
        self._state = app_state
        # Holds in-flight ACP turn tasks so they aren't garbage-collected
        # before completing (asyncio keeps only weak refs to tasks).
        self._acp_tasks: set[asyncio.Task] = set()
        # Per-agent lock so turns for the same agent run sequentially (a shared
        # gateway session can't process two prompts at once) — concurrent
        # across different agents. Preserves the bridge's queued-delivery order.
        self._agent_locks: dict[str, asyncio.Lock] = {}

    async def close(self) -> None:
        # Cancel + drain any in-flight ACP turns so shutdown doesn't orphan them.
        tasks = list(self._acp_tasks)
        for t in tasks:
            t.cancel()
        for t in tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._acp_tasks.clear()

    async def _run_acp_turn(
        self, agent_name: str, text: str, trace_id, record_reply,
    ) -> None:
        """Drive one OpenClaw ACP turn under the agent's serialization lock."""
        from tinyagentos.openclaw_acp_runtime import drive_turn

        lock = self._agent_locks.setdefault(agent_name, asyncio.Lock())
        async with lock:
            await drive_turn(
                slug=agent_name, text=text, trace_id=trace_id, record_reply=record_reply,
            )

    def dispatch(self, message: dict, channel: dict) -> None:
        """Fire-and-forget entry point. Runs routing in a background task."""
        if message.get("content_type") == "system":
            return
        if message.get("state") == "streaming":
            return
        asyncio.create_task(self._route(message, channel))

    async def _route(self, message: dict, channel: dict) -> None:
        try:
            await self._route_inner(message, channel)
        except Exception as exc:  # noqa: BLE001
            logger.warning("agent chat router failed: %s", exc, exc_info=True)

    async def _route_inner(self, message: dict, channel: dict) -> None:
        from tinyagentos.agent_db import find_agent
        from tinyagentos.chat.mentions import parse_mentions

        if message.get("content_type") == "system":
            return

        settings = channel.get("settings") or {}

        thread_id = message.get("thread_id")
        if thread_id:
            from tinyagentos.chat.threads import resolve_thread_recipients
            recipients, force_by_slug = await resolve_thread_recipients(
                message, channel, self._state.chat_messages,
            )
            if not recipients:
                return
            # Thread policy key scopes hops/cooldown/rate-cap per thread.
            policy_key = f"{channel['id']}:thread:{thread_id}"
        else:
            author = message.get("author_id")
            members = list(channel.get("members") or [])
            muted = set(settings.get("muted") or [])

            channel_type = channel.get("type")
            effective_mode = "lively" if channel_type == "dm" else settings.get("response_mode", "quiet")

            mentions = parse_mentions(message.get("content") or "", members)

            candidates = [m for m in members if m and m != author and m != "user" and m not in muted]
            if not candidates:
                return

            # Leads bypass the quiet filter: they see every message in the
            # channel except their own. They are always added to recipients
            # after the normal routing decision, de-duplicating if already present.
            leads = [
                m for m in (settings.get("leads") or [])
                if m and m != author and m not in muted
            ]

            force_by_slug: dict[str, bool] = {}
            if mentions.all:
                for m in candidates:
                    force_by_slug[m] = True
                recipients = list(candidates)
            elif mentions.explicit:
                recipients = [m for m in candidates if m.lower() in mentions.explicit]
                for m in recipients:
                    force_by_slug[m] = True
            elif channel_type == "dm":
                recipients = list(candidates)
                for m in recipients:
                    force_by_slug[m] = True
            elif effective_mode == "quiet":
                recipients = []
            else:
                recipients = list(candidates)

            # Always loop leads in regardless of mode/mentions (except author
            # already excluded above, and muted already excluded from leads).
            for lead in leads:
                if lead not in recipients:
                    recipients.append(lead)

            if not recipients:
                return

            policy_key = channel["id"]

        try:
            current_hops = int((message.get("metadata") or {}).get("hops_since_user", 0) or 0)
        except (TypeError, ValueError):
            current_hops = 0
        next_hops = current_hops + 1
        max_hops = int(settings.get("max_hops", 3))

        config = self._state.config
        bridge = getattr(self._state, "bridge_sessions", None)
        policy = getattr(self._state, "group_policy", None)

        # Build the context window once per routed message, not per recipient.
        context = []
        if hasattr(self._state, "chat_messages"):
            try:
                from tinyagentos.chat.context_window import build_context_window
                if thread_id:
                    recent = await self._state.chat_messages.get_thread_messages(
                        channel_id=channel["id"], parent_id=thread_id, limit=30,
                    )
                    # Prepend the parent as the root turn.
                    parent = await self._state.chat_messages.get_message(thread_id)
                    if parent:
                        recent = [parent] + list(recent)
                else:
                    recent = await self._state.chat_messages.get_messages(
                        channel_id=channel["id"], limit=30,
                    )
                context = build_context_window(recent, limit=20, max_tokens=4000)
            except Exception:
                logger.warning("context fetch failed for channel %s", channel.get("id"), exc_info=True)
                context = []

        leads = list((settings.get("leads") or []))  # post-PR #291
        from tinyagentos.agent_manual import build_manual

        for agent_name in recipients:
            forced = force_by_slug.get(agent_name, False)
            if not forced:
                if next_hops > max_hops:
                    continue
                if policy is not None and not policy.try_acquire(policy_key, agent_name, settings):
                    continue
            agent = find_agent(config, agent_name)
            if agent is None:
                continue
            if agent.get("status") != "running":
                await self._post_system_reply(
                    agent_name, channel["id"],
                    f"[router] agent '{agent_name}' is not running (status={agent.get('status') or 'unknown'}).",
                )
                continue
            if bridge is None:
                await self._post_system_reply(
                    agent_name, channel["id"],
                    "[router] bridge registry not configured on this host.",
                )
                continue

            if agent.get("framework") == "openclaw" and _openclaw_transport() == "acp":
                # Fork-free path: drive the OpenClaw turn over ACP instead of the
                # legacy taos-bridge SSE. The agent's gateway session carries its
                # own history + AGENTS.md, so we send the user message and stream
                # the mapped replies straight into record_reply (same sink).
                # _run_acp_turn serializes turns per agent (shared session).
                task = asyncio.create_task(
                    self._run_acp_turn(
                        agent_name,
                        message.get("content", ""),
                        message.get("id"),
                        bridge.record_reply,
                    )
                )
                self._acp_tasks.add(task)
                task.add_done_callback(self._acp_tasks.discard)
            else:
                manual_text = build_manual(channel, agent_name, leads)
                agent_context = [{"role": "system", "content": manual_text}, *context]
                await bridge.enqueue_user_message(
                    agent_name,
                    {
                        "id": message.get("id"),
                        "trace_id": message.get("id"),
                        "channel_id": message.get("channel_id"),
                        "from": message.get("author_id", "user"),
                        "text": message.get("content", ""),
                        "created_at": message.get("created_at"),
                        "hops_since_user": next_hops,
                        "force_respond": forced,
                        "context": agent_context,
                        "thread_id": thread_id,
                    },
                )
            if forced and policy is not None:
                policy.record_send(policy_key, agent_name)

    async def _post_system_reply(
        self, agent_name: str, channel_id: str, content: str,
    ) -> None:
        chat_messages = self._state.chat_messages
        chat_channels = self._state.chat_channels
        hub = self._state.chat_hub
        persisted = await chat_messages.send_message(
            channel_id=channel_id,
            author_id=agent_name,
            author_type="agent",
            content=content,
            content_type="text",
            state="complete",
            metadata=None,
        )
        await chat_channels.update_last_message_at(channel_id)
        await hub.broadcast(channel_id, {"type": "message", "seq": hub.next_seq(), **persisted})

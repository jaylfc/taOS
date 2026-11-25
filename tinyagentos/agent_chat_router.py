from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any

from tinyagentos.agent_loop import AgentLoop, LoopAction
from tinyagentos.taosmd_client import AnswerContextResult, calculate_agent_budget, get_taosmd_client
from tinyagentos.task_utils import _create_supervised_task

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
        # Per-agent AgentLoop owns turn serialization: turns for the same
        # agent run sequentially (a shared gateway session can't process two
        # prompts at once) — concurrent across different agents. Messages
        # arriving mid-turn are queued by the loop and driven by the current
        # turn-holder at its safe point, preserving delivery order.
        self._agent_loops: dict[str, AgentLoop] = {}

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
        """Drive one OpenClaw ACP turn, serialized per agent by its AgentLoop."""
        from tinyagentos.openclaw_acp_runtime import drive_turn

        chat_messages = self._state.chat_messages
        chat_hub = self._state.chat_hub

        loop = self._agent_loops.setdefault(agent_name, AgentLoop())
        action = await loop.handle_message(text, msg_id=trace_id)
        if action is LoopAction.QUEUED:
            # Another turn is in flight for this agent; the current turn-holder
            # drives this message at its safe point (drain below).
            return

        # Turn-holder: drive the turn, then iteratively (never recursively)
        # drive every message queued while it ran. Each queued message keeps
        # its own trace id.
        pending: list[tuple[str, Any]] = [(text, trace_id)]
        while pending:
            cur_text, cur_trace = pending.pop(0)
            # Stamp delivered for this trace_id right before driving the turn
            if chat_messages:
                try:
                    msg = await chat_messages.get_message(cur_trace)
                    if msg:
                        receipt_row = await chat_messages.mark_delivered(cur_trace, agent_name)
                        if receipt_row and chat_hub:
                            channel_id = msg.get("channel_id")
                            if channel_id:
                                await chat_hub.broadcast(channel_id, {
                                    "type": "receipt",
                                    "seq": chat_hub.next_seq(),
                                    "message_id": receipt_row["message_id"],
                                    "agent_id": receipt_row["reader_id"],
                                    "reader_type": receipt_row["reader_type"],
                                    "delivered_at": receipt_row["delivered_at"],
                                    "seen_at": receipt_row["seen_at"],
                                })
                except Exception:
                    logger.exception("agent_chat_router: mark_delivered failed for %s", agent_name)
            try:
                await drive_turn(
                    slug=agent_name, text=cur_text, trace_id=cur_trace,
                    record_reply=record_reply,
                )
            except Exception as exc:  # noqa: BLE001
                # Log like _route does: this task has no other supervisor, and
                # a raise must not drop the messages queued behind the failed
                # turn (they are still drained and driven below).
                logger.warning(
                    "acp turn for agent %s failed: %s",
                    agent_name, exc, exc_info=True,
                )
            finally:
                # MUST run even when drive_turn raises or the task is
                # cancelled — skipping it would wedge the loop in WORKING
                # forever and silence the agent.
                queued = await loop.reach_safe_point()
            for m in queued:
                followup = await loop.handle_message(m.content, msg_id=m.id)
                if followup is LoopAction.IMMEDIATE:
                    pending.append((m.content, m.id))
                # QUEUED here means this holder already restarted the turn
                # with an earlier drained message (handle_message and the
                # drain are atomic - the lock is never held across an await,
                # so no other caller can interleave). The message sits in the
                # loop's queue and this holder's next reach_safe_point
                # re-drains it; WORKING always implies the current holder
                # reaches another safe point.

    def dispatch(self, message: dict, channel: dict) -> None:
        """Fire-and-forget entry point. Runs routing in a supervised background task."""
        if message.get("content_type") == "system":
            return
        if message.get("state") == "streaming":
            return
        task_set = getattr(self._state, "_background_tasks", None)
        if task_set is None:
            asyncio.create_task(self._route(message, channel))
        else:
            _create_supervised_task(self._route(message, channel), task_set)

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

        # Build memory context via taOSmd answer_context for each recipient.
        # This replaces the old build_context_window which only used chat history.
        # answer_context assembles dated session blocks (oldest first, original turn
        # order), respects token budget, never cuts mid-item, and reports dropped counts.
        agent_contexts: dict[str, AnswerContextResult] = {}
        # Also fetch chat history for each recipient to preserve conversation context.
        agent_histories: dict[str, list[dict]] = {}
        if hasattr(self._state, "chat_messages"):
            try:
                from tinyagentos.agent_manual import build_manual
                from tinyagentos.chat.context_window import build_context_window, estimate_tokens, history_token_budget
                from tinyagentos.chat.guest_agents import resolve_guest
                registry = getattr(self._state, "registry", None)

                for agent_name in recipients:
                    agent = find_agent(config, agent_name)
                    is_guest = False
                    if agent is None:
                        # A registry guest has no model: use default budget.
                        if await resolve_guest(self._state, agent_name):
                            is_guest = True
                        else:
                            continue
                    if not is_guest:
                        # Calculate budget from agent's model context window (share, not whole window)
                        # Measure used_tokens from manual (system prompt + tools) + conversation history
                        manual_text = build_manual(channel, agent_name, list((settings.get("leads") or [])))

                        # Fetch recent conversation history for this channel
                        messages = await self._state.chat_messages.get_messages(channel["id"], limit=100)
                        # Build context window to get the messages that will be sent (respects token budget)
                        manifest = None
                        if registry:
                            from tinyagentos.cluster.model_resolver import _find_model_manifest
                            manifest = _find_model_manifest(registry, agent.get("model", ""))
                        ctx_window = int(getattr(manifest, "context_window", 0) or 0) if manifest else 0
                        history_budget = history_token_budget(ctx_window) if ctx_window else 4000
                        history_messages = build_context_window(messages, limit=20, max_tokens=history_budget)

                        # Measure used_tokens: manual (system prompt) + conversation history
                        history_tokens = sum(estimate_tokens(m.get("content", "")) for m in history_messages)
                        used_tokens = estimate_tokens(manual_text) + history_tokens

                        budget_tokens = calculate_agent_budget(agent, registry, used_tokens) if registry else 4000

                        # Store history for later use when building the agent context
                        agent_histories[agent_name] = history_messages
                    else:
                        budget_tokens = 4000
                        agent_histories[agent_name] = []

                    # The query is the current user message content
                    query = message.get("content", "")
                    taosmd_client = get_taosmd_client(self._state)
                    result = await taosmd_client.answer_context(
                        query=query,
                        agent=agent_name,
                        budget_tokens=budget_tokens,
                        now=datetime.now(timezone.utc),
                    )
                    agent_contexts[agent_name] = result

                    # Surface dropped count in agent trace
                    if result.dropped > 0 and self._state.trace_registry:
                        try:
                            store = await self._state.trace_registry.get(agent_name)
                            await store.record(
                                "memory_truncated",
                                trace_id=message.get("id"),
                                channel_id=channel.get("id"),
                                payload={"dropped": result.dropped, "budget_tokens": budget_tokens},
                            )
                        except Exception:
                            logger.debug("Failed to record memory_truncated trace for %s", agent_name)
            except Exception:
                logger.warning("taOSmd answer_context failed for channel %s", channel.get("id"), exc_info=True)
                agent_contexts = {}
                agent_histories = {}

        leads = list((settings.get("leads") or []))  # post-PR #291

        for agent_name in recipients:
            forced = force_by_slug.get(agent_name, False)
            if not forced:
                if next_hops > max_hops:
                    continue
                if policy is not None and not policy.try_acquire(policy_key, agent_name, settings):
                    continue
            agent = find_agent(config, agent_name)
            guest = None
            if agent is None:
                from tinyagentos.chat.guest_agents import resolve_guest
                guest = await resolve_guest(self._state, agent_name)
                if guest is None:
                    continue
                agent = guest
            if guest is None and agent.get("status") != "running":
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

            if guest is None and agent.get("framework") == "openclaw" and _openclaw_transport() == "acp":
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
                # Use taOSmd answer_context result for memory context
                ac_result = agent_contexts.get(agent_name)
                memory_context = ac_result.context if ac_result else ""
                # Get preserved chat history for this agent
                history_messages = agent_histories.get(agent_name, [])

                # Build the full context: chat history + system message with manual + memory
                agent_context = []
                # Add conversation history (user/assistant messages)
                for msg in history_messages:
                    agent_context.append({
                        "role": "assistant" if msg.get("author_type") == "agent" else "user",
                        "content": msg.get("content", ""),
                    })
                # Add system message with manual + memory context
                combined_content = manual_text
                if memory_context:
                    combined_content = f"{manual_text}\n\n---\n\n{memory_context}"
                agent_context.append({"role": "system", "content": combined_content})

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
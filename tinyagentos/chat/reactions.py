"""Semantic reactions for multi-agent chat.

👎 by a human on an agent-authored message → regenerate the reply.
🙋 by an agent → "wants to reply" ephemeral flag with TTL.
Everything else is purely decorative.
"""
from __future__ import annotations

import time


def _now() -> float:
    return time.monotonic()


class WantsReplyRegistry:
    def __init__(self, ttl_seconds: int = 300) -> None:
        self._ttl = ttl_seconds
        self._entries: dict[str, dict[str, float]] = {}

    def add(self, channel_id: str, slug: str) -> None:
        self._entries.setdefault(channel_id, {})[slug] = _now()

    def list(self, channel_id: str) -> list[str]:
        now = _now()
        bucket = self._entries.get(channel_id) or {}
        alive = {s: t for s, t in bucket.items() if (now - t) < self._ttl}
        self._entries[channel_id] = alive
        return sorted(alive)


async def maybe_trigger_semantic(
    *, emoji: str, message: dict, reactor_id: str, reactor_type: str,
    channel: dict, state,
) -> None:
    if emoji == "👎" and reactor_type == "user" and message.get("author_type") == "agent":
        bridge = getattr(state, "bridge_sessions", None)
        if bridge is None:
            return

        # Regenerate = ask the agent to answer the ORIGINAL user message again.
        # The trace_id on an agent reply points at the user message that triggered it.
        msg_store = getattr(state, "chat_messages", None)
        original_text = ""
        original_id = message.get("id")
        trace_id = (message.get("metadata") or {}).get("trace_id") or message.get("id")
        if msg_store is not None and trace_id:
            try:
                original = await msg_store.get_message(trace_id)
                if original:
                    original_text = original.get("content") or ""
                    original_id = original.get("id") or original_id
            except Exception:
                original_text = ""

        context = []
        if msg_store is not None:
            try:
                from tinyagentos.chat.context_window import (
                    build_context_window,
                    history_token_budget,
                )
                from tinyagentos.cluster.model_resolver import _find_model_manifest

                # Budget from the regenerating agent's model context window.
                ctx_window = 0
                config = getattr(state, "config", None)
                author_id = message.get("author_id")
                if config is not None and author_id:
                    from tinyagentos.agent_db import find_agent

                    agent = find_agent(config, author_id)
                    model_id = (agent or {}).get("model") or ""
                    if model_id:
                        try:
                            registry = getattr(state, "registry", None)
                            manifest = _find_model_manifest(registry, model_id)
                            ctx_window = (
                                int(getattr(manifest, "context_window", 0) or 0)
                                if manifest
                                else 0
                            )
                        except Exception:  # noqa: BLE001 - advisory lookup only
                            ctx_window = 0
                max_tokens = history_token_budget(ctx_window)
                recent = await msg_store.get_messages(channel_id=message.get("channel_id"), limit=30)
                context = build_context_window(recent, limit=20, max_tokens=max_tokens)
            except Exception:
                context = []

        await bridge.enqueue_user_message(
            message["author_id"],
            {
                "id": original_id,
                "trace_id": trace_id,
                "channel_id": message.get("channel_id"),
                "from": reactor_id,
                "text": original_text,
                "hops_since_user": 0,
                "force_respond": True,
                "regenerate": True,
                "context": context,
            },
        )
        return

    if emoji == "🙋" and reactor_type == "agent":
        reg = getattr(state, "wants_reply", None)
        if reg is None:
            return
        reg.add(channel["id"], reactor_id)

    if emoji == "📌" and reactor_type == "agent" and reactor_id == message.get("author_id"):
        msg_store = getattr(state, "chat_messages", None)
        if msg_store is None:
            return
        meta = dict(message.get("metadata") or {})
        meta["pin_requested"] = True
        await msg_store.set_metadata(message["id"], meta)
        # Broadcast so clients can render the PinRequestAffordance without a refresh.
        hub = getattr(state, "chat_hub", None)
        if hub is not None:
            updated = await msg_store.get_message(message["id"])
            if updated is not None:
                await hub.broadcast(message.get("channel_id") or channel.get("id"), {
                    "type": "message_edit",
                    "seq": hub.next_seq(),
                    "message_id": message["id"],
                    "content": updated.get("content"),
                    "edited_at": updated.get("edited_at"),
                    "metadata": updated.get("metadata"),
                })
        return

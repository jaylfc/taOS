"""Thread-aware recipient resolution for agent chat routing.

Narrow-by-default scope: parent-message author (if agent), prior thread
repliers, and explicit @<slug> mentions in the new message. @all inside
a thread escalates to every channel-member agent with force_respond=true.

Muted agents are excluded. The message author is always excluded
(threads don't re-notify the speaker).
"""
from __future__ import annotations

from tinyagentos.chat.mentions import parse_mentions


async def resolve_thread_recipients(
    message: dict, channel: dict, chat_messages,
) -> tuple[list[str], dict[str, bool]]:
    """Return (recipients, force_by_slug) for a message in a thread.

    Args:
        message: the new message being routed. Must have thread_id, author_id,
                 author_type, content.
        channel: the channel dict including members and settings.muted.
        chat_messages: the ChatMessageStore (needs get_message, get_thread_messages).
    """
    author = message["author_id"]
    thread_id = message.get("thread_id")
    if not thread_id:
        return [], {}

    members = channel.get("members") or []
    muted = set((channel.get("settings") or {}).get("muted") or [])
    candidates_all = [m for m in members if m and m != author and m != "user" and m not in muted]

    mentions = parse_mentions(message.get("content") or "", members)

    # @all escalation — fan out to every agent in channel.
    if mentions.all:
        return list(candidates_all), {m: True for m in candidates_all}

    recipients: set[str] = set()
    forced: dict[str, bool] = {}

    # Parent author (if agent, and not the current author).
    parent = await chat_messages.get_message(thread_id)
    if parent and parent.get("author_type") == "agent":
        parent_author = parent.get("author_id")
        if parent_author and parent_author != author and parent_author not in muted:
            recipients.add(parent_author)

    # Prior repliers (agents only).
    prior = await chat_messages.get_thread_messages(
        channel_id=channel["id"], parent_id=thread_id, limit=200,
    )
    for m in prior:
        if m.get("author_type") == "agent":
            aid = m.get("author_id")
            if aid and aid != author and aid not in muted:
                recipients.add(aid)

    # Explicit mentions (force_respond).
    for slug in mentions.explicit:
        if slug in candidates_all:
            recipients.add(slug)
            forced[slug] = True

    return sorted(recipients), forced

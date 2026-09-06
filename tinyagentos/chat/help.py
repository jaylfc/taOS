"""/help command handler — posts short cheat sheets into the channel
as system messages. Full reference lives in docs/chat-guide.md.
"""
from __future__ import annotations

GUIDE_URL = "https://github.com/jaylfc/tinyagentos/blob/master/docs/chat-guide.md"

KNOWN_TOPICS = (
    "channels",
    "mentions",
    "hops",
    "reactions",
    "slash",
    "settings",
    "context",
    "threads",
    "attachments",
    "help",
)

_OVERVIEW = f"""**taOS chat — quick help**

- `@tom`, `@all`, `@humans` — target specific recipients
- `/` in composer opens the command picker for the current channel's agents
- `ⓘ` in the header opens channel settings (mode, members, muted, etc.)
- Right-click / hover a message for actions (reply in thread, react, etc.)

Try `/help <topic>` where topic is one of: {", ".join(t for t in KNOWN_TOPICS if t != "help")}.
Full guide: {GUIDE_URL}
"""

_TOPICS: dict[str, str] = {
    "channels": f"""**Channels**
- DM (2 members), group (many), topic (many, focused)
- Group/topic channels have a mode: `quiet` (respond when @mentioned only) or `lively` (every agent decides per message)
- DMs always lively — the 1:1 agent always replies.

Details: {GUIDE_URL}#channels-and-modes""",
    "mentions": f"""**Mentions**
- `@tom` — target one agent
- `@all` — every agent in the channel
- `@humans` — ping humans
- Case-insensitive; word boundary so `email@x.com` doesn't count

Details: {GUIDE_URL}#mentions""",
    "hops": f"""**Hops, cooldown, rate-cap**
- Hop counter resets on each user message; caps chains between agents (default 3)
- Per-agent cooldown prevents burst replies (default 5 s)
- Per-channel rate cap (default 20/min) is a circuit breaker
- `@mention` overrides all three caps

Details: {GUIDE_URL}#hops-cooldown-rate-cap""",
    "reactions": f"""**Reactions**
- Any emoji — click 😀 on a message's hover row
- `👎` by the channel's human on an agent reply → regenerate
- `🙋` by an agent → "hand raise" (shows a badge; no auto-reply)

Details: {GUIDE_URL}#reactions""",
    "slash": f"""**Slash menu**
- Type `/` at the start of a message to open the command picker
- Commands grouped by agent; fuzzy filter as you type
- Enter selects → inserts `@<agent> /<cmd>` into the composer

Details: {GUIDE_URL}#slash-menu""",
    "settings": f"""**Channel settings**
- `ⓘ` in chat header opens the settings panel (right side)
- Rename, topic, members, muted agents, mode, max hops, cooldown
- DMs have no settings panel (two-member 1:1)

Details: {GUIDE_URL}#channel-settings""",
    "context": f"""**Agent context menu**
- Right-click an agent's name or avatar anywhere for actions
- DM, (un)mute, remove, view info, jump to agent settings
- Shift+F10 on a focused message row opens the same menu

Details: {GUIDE_URL}#agent-context-menu""",
    "threads": f"""**Threads**
- Hover a message → `💬 Reply in thread` opens a right-side panel
- Thread replies have narrow routing — parent author + prior repliers + @mentions
- `@all` inside a thread escalates to every channel agent
- Hops, cooldown, rate-cap all scoped per thread

Details: {GUIDE_URL}#threads""",
    "attachments": f"""**Attachments**
- Paperclip button, drag-and-drop, or paste from clipboard
- Paperclip opens a file picker with tabs: Disk / My workspace / Agent workspaces
- Up to 10 attachments per message; 100 MB max per file
- Images render inline; 2+ images → gallery grid

Details: {GUIDE_URL}#attachments""",
    "help": f"""**/help**
- `/help` on its own — overview + topic list
- `/help <topic>` — the section for that topic
- Topics: {", ".join(t for t in KNOWN_TOPICS if t != "help")}

Full guide: {GUIDE_URL}""",
}


def handle_help(args: str) -> str:
    """Return the system-message text for `/help [topic]`."""
    topic = (args or "").strip().lower().split()
    if not topic:
        return _OVERVIEW
    key = topic[0]
    if key in _TOPICS:
        return _TOPICS[key]
    return f"Unknown help topic '{key}'. Try `/help` for the overview."

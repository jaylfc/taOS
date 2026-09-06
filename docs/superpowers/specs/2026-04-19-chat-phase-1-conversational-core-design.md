# Chat Phase 1 — Conversational Core Design

**Date:** 2026-04-19
**Scope:** Backend + bridge changes to turn the existing parallel 1:1-per-agent chat into a functional multi-agent room. No UX polish (threads, pinning, typing indicators, read receipts) — those are Phase 2.

## Goal

One sentence: agents in a group channel see each other's messages, decide per-message whether to respond, can't loop-spam the channel, respect `@mention` / `@all` addressing, and expose channel controls (mode, mute, topic, etc.) via REST admin endpoints the UI drives from right-click menus.

## Background

The current `tinyagentos/agent_chat_router.py` only dispatches messages whose `author_type == "user"` to agent members. Agent replies are persisted and broadcast to the UI, but never re-routed to the *other* agent members. Each bridge call is stateless (`[system, single user message]`), so even if routing were fixed, the agents' LLMs would never see prior turns.

Consequence observed during the 6-framework roundtable demo: each agent replied independently to the user, none of them referenced any other agent. Linus explicitly said *"Hey — just one AI here! There's no John or Elliot or team. I'm a single assistant."*

## Architecture

```
message sent (user or agent)
  │
  ├─▶ message_store.send_message ─▶ chat_hub.broadcast (existing — UI updates)
  │
  └─▶ AgentChatRouter.dispatch
        │
        ├─ bare slash in non-DM? → 400 guardrail (must @<slug> or @all)
        │
        ├─ parse mentions (@slug, @all, @humans)
        │
        ├─ pick recipients by (channel_type, mode, mentions, author, muted)
        │
        ├─ per recipient: check hop cap, cooldown, rate cap
        │
        └─ enqueue user_message event to each bridge
             │   (event now carries: context window, force_respond flag, hops)
             │
             ▼
           bridge (in container)
             │
             ├─ build LLM call: [system + identity + NO_RESPONSE rule]
             │                  [...context messages as "<author>: <text>"]
             │                  [current message]
             │
             ├─ suppress if output == "NO_RESPONSE" AND !force_respond
             │   (record message_suppressed trace event)
             │
             └─ POST reply back to /api/openclaw/sessions/{slug}/reply
                   │
                   └─ bridge_session._handle_reply
                         │ (existing + new: re-dispatches via router
                         │  so other agents see the reply)
                         ▼
                       loop
```

## Data Model

### `channel.settings` (jsonb) — new fields

| Field | Type | Default | Notes |
|---|---|---|---|
| `response_mode` | `"quiet"` \| `"lively"` | `"quiet"` for groups/topics, DMs force-lively | see Modes below |
| `max_hops` | int | 3 | hops-since-user cap for lively; `@mention` overrides |
| `cooldown_seconds` | int | 5 | per-agent min gap between its own messages |
| `rate_cap_per_minute` | int | 20 | channel-wide agent-message cap (circuit breaker) |
| `muted` | `string[]` | `[]` | agent slugs muted in this channel |

Migration: existing channels get defaults at read time in `channel_store._parse_channel` (missing keys → defaults); no schema migration required since `settings` is jsonb.

### `message.metadata` — new field

| Field | Type | Default | Notes |
|---|---|---|---|
| `hops_since_user` | int | 0 | 0 on user messages; incremented by router on agent-authored re-dispatch |

Stored in the existing `metadata` jsonb column. Absence = 0.

## Channel Modes

- **`quiet`** (default for new groups/topics): agents respond *only* when `@mentioned` (by slug, `@all`, or explicit DM). Unmentioned messages fan out to nobody. Zero loop risk.
- **`lively`**: every member (except author, except muted) receives the message and decides via NO_RESPONSE whether to reply. Used for panel-style discussions.
- **DMs**: channel with `type == "dm"` and exactly two members is always treated as lively with `force_respond=true` for the agent side. `response_mode` setting on a DM is ignored.

## Mention Parser (`tinyagentos/chat/mentions.py`)

Public surface:

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class MentionSet:
    explicit: tuple[str, ...]   # slugs of channel members explicitly @-tagged
    all: bool                   # "@all" present
    humans: bool                # "@humans" present

def parse_mentions(text: str, members: list[str]) -> MentionSet: ...
```

Rules:
- Regex: `(?<![A-Za-z0-9_])@([A-Za-z0-9_-]+)(?![A-Za-z0-9_])` — whole-word boundary so `email@tom.com` doesn't match.
- Case-insensitive comparison against `members` list (canonicalised to lowercase).
- Special tokens: `@all` (case-insensitive) → sets `all=True`; `@humans` → sets `humans=True`. These take precedence over treating `all`/`humans` as slugs even if a member happens to be named that.
- Slugs that aren't current channel members are silently treated as plain text (not added to `explicit`).
- Output is deterministic: `explicit` is sorted, deduplicated.

> Callers (the POST /api/chat/messages handler) also use `parse_mentions` to enforce the bare-slash guardrail: a `/`-prefixed message in a non-DM channel must have `mentions.explicit or mentions.all`, else the message is rejected with 400.

## Router Fanout (`tinyagentos/agent_chat_router.py`)

### Dispatch trigger

`dispatch()` now accepts any message type, including agent-authored. The previous guard `if message.get("author_type") != "user"` is removed; replaced with:

```python
def dispatch(self, message, channel):
    if message.get("content_type") == "system":
        return  # system messages don't route
    if message.get("state") != "complete":
        return  # streaming placeholders don't route yet
    asyncio.create_task(self._route(message, channel))
```

### Recipient selection

```python
async def _route_inner(self, message, channel):
    author = message["author_id"]
    author_type = message["author_type"]
    members = channel.get("members") or []
    settings = channel.get("settings") or {}
    muted = set(settings.get("muted") or [])

    # Resolve effective mode
    channel_type = channel.get("type")
    if channel_type == "dm":
        effective_mode = "lively"
    else:
        effective_mode = settings.get("response_mode", "quiet")

    mentions = parse_mentions(message["content"] or "", members)

    # Candidate set: agent members, minus author, minus muted
    candidates = [
        m for m in members
        if m != author and m != "user" and m not in muted
    ]

    # Resolve recipients and force_respond
    force_by_slug = {}
    if mentions.all:
        for m in candidates: force_by_slug[m] = True
        recipients = list(candidates)
    elif mentions.explicit:
        recipients = [m for m in candidates if m in mentions.explicit]
        for m in recipients: force_by_slug[m] = True
    elif channel_type == "dm":
        recipients = list(candidates)  # force_respond=True for DMs
        for m in recipients: force_by_slug[m] = True
    elif effective_mode == "quiet":
        recipients = []
    else:  # lively
        recipients = list(candidates)  # NO_RESPONSE-gated

    if not recipients:
        return

    # Hop/cooldown/rate enforcement (delegated to GroupPolicy)
    current_hops = (message.get("metadata") or {}).get("hops_since_user", 0)
    next_hops = current_hops + 1   # the recipient's reply would be this many hops from the last user turn

    policy = self._state.group_policy  # new singleton, see below
    for agent_name in recipients:
        forced = force_by_slug.get(agent_name, False)
        if not forced:
            if next_hops > settings.get("max_hops", 3):
                continue
            if not policy.may_send(channel["id"], agent_name, settings):
                continue
        await self._enqueue(agent_name, message, channel, next_hops, forced)
```

`_enqueue` is a thin wrapper that calls the existing `bridge.enqueue_user_message` with added fields `hops_since_user`, `force_respond`, and `context`.

### Re-dispatch on agent reply

`bridge_session._handle_reply` currently persists the reply and broadcasts. After a successful `final` persistence, it also calls `router.dispatch(persisted_msg, channel)`. The router's `_route_inner` treats the agent's own slug as the author and excludes it from candidates — so loops need the hop cap to terminate.

### Hops propagation end-to-end

1. User posts M0. `message_store.send_message` defaults `metadata.hops_since_user = 0`.
2. Router reads M0.hops=0, computes `next_hops = 1`, enqueues `event.hops_since_user = 1` to each recipient.
3. bridge keeps the event's `hops_since_user` in an in-memory map keyed by `trace_id` (same pattern as the existing `_pending_msg_ids`).
4. bridge posts the reply; `routes/openclaw.reply_ingest` passes through to `bridge_session._handle_reply`, which looks up the pending hops, sets `reply_message.metadata.hops_since_user = that value` before calling `message_store.send_message`.
5. Router re-dispatches the reply. Now current_hops = 1, next_hops = 2, and the cycle continues until `next_hops > max_hops` or all candidates return NO_RESPONSE.

## Group Policy (`tinyagentos/chat/group_policy.py`)

In-memory, single-process (matches current architecture — one uvicorn process owns the router). Thread-safe via `asyncio.Lock`.

```python
class GroupPolicy:
    """Tracks per-channel per-agent cooldowns and channel-wide rate caps."""

    def __init__(self): ...

    def may_send(self, channel_id: str, agent: str, settings: dict) -> bool:
        """True if the agent is allowed to send now in this channel.
        Checks cooldown_seconds and rate_cap_per_minute."""

    def record_send(self, channel_id: str, agent: str) -> None:
        """Record a successful enqueue so subsequent may_send calls see it."""
```

Internal state:
- `_last_send_at: dict[(channel_id, agent), float]` — per-agent cooldown timestamps
- `_recent_sends: dict[channel_id, deque[float]]` — sliding 60s window of send timestamps; `maxlen=256` to bound memory

`may_send` returns `False` if either the cooldown hasn't elapsed or the channel's 60-second count exceeds `rate_cap_per_minute`. Rate-cap rejections are logged at DEBUG.

## NO_RESPONSE Protocol

### System prompt addition (bridge-side)

Each bridge's system prompt gets appended (after the existing framework-identity prompt):

```
You are in a multi-agent group chat. Current members: <comma-separated list>.
Channel mode: <quiet|lively>.

Recent conversation:
<author_1>: <content_1>
<author_2>: <content_2>
...

If you were not explicitly @mentioned and this message is not for you, reply with exactly: NO_RESPONSE
Otherwise reply naturally. Keep responses short in group chats.
```

When `force_respond=true`, this paragraph is replaced with a shorter one:

```
You are in a multi-agent group chat with: <list>. You were directly addressed. Reply naturally; do not output NO_RESPONSE.
```

### Response checking

Each bridge's reply-building path:

```python
def _maybe_suppress(reply: str, force_respond: bool) -> str | None:
    if force_respond:
        return reply
    stripped = reply.strip().lower().strip(".!,;:")
    if stripped == "no_response":
        return None  # signal to bridge: do not POST
    return reply
```

If `_maybe_suppress` returns `None`:
- Bridge records a trace event of kind `message_suppressed` with payload `{"reason": "no_response"}` via the same trace path as `message_out`.
- Bridge does NOT POST to `/api/openclaw/sessions/{slug}/reply`.

## Context Threading

### Shape

The `user_message` event payload enqueued by the router now carries:

```json
{
  "id": "...",
  "trace_id": "...",
  "channel_id": "...",
  "from": "tom",
  "text": "current message content",
  "hops_since_user": 2,
  "force_respond": false,
  "context": [
    {"author_id": "user",   "author_type": "user",  "content": "..."},
    {"author_id": "tom",    "author_type": "agent", "content": "..."},
    {"author_id": "linus",  "author_type": "agent", "content": "..."}
  ]
}
```

### Window

Before enqueuing, the router asks `message_store` for the channel's last messages with `limit=20`, oldest-first. It then truncates to `max_tokens=4000` using a naive 4-chars-per-token heuristic, dropping oldest first until under budget. System messages (admin-endpoint echoes, system notifications) are excluded. The current message is NOT duplicated in `context` — it's always the last turn.

### Bridge consumption

Each bridge formats `context` into the conversation block above the current turn. Framework-specific notes:

- **hermes** / **pocketflow**: construct `messages: [system, *context_as_user_turns, user_current]`. Context turns are sent as `user` role with `"{author}: {content}"` prefix; not split into user/assistant roles since taOS doesn't know the model's conversational stance for those turns.
- **smolagents**: concatenate context + current into the `agent.run(...)` string with the framework preamble already planned.
- **langroid**: use `system_message=` for identity + rules; pass context-then-current as the message to `llm_response`.
- **openai-agents-sdk**: `instructions` = identity+rules, then `Runner.run_sync(agent, "<context>\n\nCurrent: <text>")`.
- **openclaw fork**: context is already maintained fork-side per sessionKey. `context` in the event is *informational* only for now; a follow-up spec will thread it into fork-side state. For Phase 1, openclaw ignores the field.

### Error handling

If the message store call fails, the router enqueues with `context: []` and logs a warning. Bridge still functions (degraded — no memory of prior turns in this turn), so fail-open is acceptable.

## Reactions (`tinyagentos/chat/reactions.py`)

### Storage

Reactions are already stored in `message_store.reactions` (jsonb, shape: `{emoji: [user_id, ...]}`); existing `add_reaction` / `remove_reaction` methods stay.

### HTTP surface

- `POST /api/chat/messages/{id}/reactions` — body `{"emoji": "👍", "author_id": "tom", "author_type": "agent"}`; validates, calls `add_reaction`, broadcasts `{"type": "reaction_added", ...}` via `chat_hub`.
- `DELETE /api/chat/messages/{id}/reactions/{emoji}?author_id=tom` — calls `remove_reaction`, broadcasts `reaction_removed`.

### Semantic reactions

Two special-cased after `add_reaction`:

- **`👎` on an agent's message, added by the human user who owns the channel**: triggers regeneration. `reactions.py:maybe_trigger_regenerate(channel, message, reactor)` enqueues a fresh `user_message` event to the original agent with `force_respond=true`, `regenerate=true` flag set (bridges use this to skip NO_RESPONSE and emit a new reply with slightly different phrasing request in the prompt). The original message is NOT deleted; the new reply appears as a sibling.
- **`🙋` added by an agent to any message**: persists as a reaction, and additionally sets an ephemeral "wants_reply" flag on the agent for this channel (TTL 5 minutes). Exposed via `GET /api/chat/channels/{id}/wants_reply` for the UI to render a badge. No automatic response is triggered — the human/another agent must `@mention` to actually pull them in.

All other emojis are purely decorative.

## Slash commands (design change: UI-driven, not text-intercepted)

Earlier drafts of this spec had taOS intercept `/mute @tom`, `/lively`,
etc. as text messages and dispatch them to a backend handler. That
collides with agent-framework command namespaces (OpenClaw, SmolAgents,
Hermes all have their own `/` commands) — taOS would either mask the
framework's command or be stuck maintaining a blocklist.

Resolved design:
- taOS does **not** intercept `/` at all. Framework slash commands
  (`/help`, `/clear`, etc.) pass through as plain text and reach agents
  via normal routing.
- Channel admin (mute, mode, topic, etc.) happens via REST endpoints
  that the UI (Phase 2) drives from right-click menus and channel
  settings popovers.
- **Guardrail:** in a non-DM channel, a message whose content starts
  with `/` (after `lstrip()`) must `@<slug>` or `@all` at least one
  agent. Otherwise the POST handler returns 400. This prevents a bare
  `/help` from broadcasting to every agent in the channel.

### REST admin endpoints

- `PATCH /api/chat/channels/{id}` — body may include `response_mode`,
  `max_hops`, `cooldown_seconds`, `topic`, `name`. Each optional. Returns
  400 on validation failure.
- `POST /api/chat/channels/{id}/members` — body `{action, slug}` with
  `action` in `{"add","remove"}`.
- `POST /api/chat/channels/{id}/muted` — body `{action, slug}` with
  `action` in `{"add","remove"}`.

### Phase 2 follow-up (separate spec)

- Slash autocomplete menu in the composer: typing `/` opens a picker
  grouped by agent (`tom: /help, /clear …`). Picking an entry sends
  `@<slug> /<cmd>`. Cancel (Esc) closes the picker without sending.
- Per-framework command discovery. Likely approach: static manifest
  (`tinyagentos/frameworks.py` gains a `slash_commands` field per
  entry) as the initial version, with a bridge `/capabilities`
  endpoint as the long-term direction.

## Components Summary

**New files:**

| Path | Purpose |
|---|---|
| `tinyagentos/chat/mentions.py` | `parse_mentions(text, members) -> MentionSet` |
| `tinyagentos/chat/group_policy.py` | Cooldown + rate-cap tracker |
| `tinyagentos/chat/reactions.py` | Semantic-reaction dispatcher (👎 regenerate, 🙋 wants_reply) |
| `tests/test_chat_mentions.py` | Parser unit tests |
| `tests/test_chat_group_policy.py` | Policy unit tests |
| `tests/test_chat_reactions.py` | Reactions handler unit tests |
| `tests/test_routes_chat_slash_guard.py` | Bare-slash guardrail tests |
| `tests/test_routes_chat_admin.py` | Admin endpoint tests |

**Modified:**

| Path | Change |
|---|---|
| `tinyagentos/agent_chat_router.py` | Accept agent-authored messages, mention-aware fanout, hop propagation, call group_policy |
| `tinyagentos/routes/chat.py` | Bare-slash guardrail; admin endpoints (PATCH channel, POST members/muted); reactions POST/DELETE |
| `tinyagentos/bridge_session.py` | Accept `force_respond`, `context`, `hops_since_user` in event payload; re-dispatch agent reply via router; trace `message_suppressed` |
| `tinyagentos/routes/openclaw.py` | Event payload includes new fields; reply endpoint accepts optional `regenerate` |
| `tinyagentos/chat/channel_store.py` | Default-settings backfill on read; new helpers: `set_response_mode`, `set_hops`, `set_cooldown`, `mute`, `unmute` |
| `tinyagentos/chat/message_store.py` | Propagate `metadata.hops_since_user` on send; no schema change (jsonb) |
| `tinyagentos/scripts/install_hermes.sh` | Build context block, NO_RESPONSE suppression, `force_respond` handling |
| `tinyagentos/scripts/install_smolagents.sh` | Same as above, framework-appropriate prompt shape |
| `tinyagentos/scripts/install_langroid.sh` | Same |
| `tinyagentos/scripts/install_pocketflow.sh` | Same |
| `tinyagentos/scripts/install_openai_agents_sdk.sh` + dashed copy | Same |
| `tests/test_agent_chat_router.py` | Extended for multi-agent fanout, hop propagation, NO_RESPONSE |

## Error Handling

- Bare `/` in non-DM channel without `@<slug>` or `@all` → 400 with a user-facing message; client surfaces inline.
- Admin endpoint bad args → 400 with a specific error (e.g., `invalid response_mode: foo`).
- `message_store` context fetch fails → enqueue with `context: []`, log WARNING.
- Rate cap hit → silent drop; trace records a suppressed-due-to-rate-cap event for the agent.
- Bridge returns NO_RESPONSE when `force_respond=true` (agent violating contract) → log WARNING, treat as empty reply but still POST a visible placeholder `"(no reply)"`; don't re-dispatch.
- Circular mention reference (A mentions B, B mentions A, ...) → hop counter catches this; when hop cap reached, no further fanout occurs. Already-mentioned agents still get forced through, so `@A` → `@B` → `@A` is legal but bounded.
- Bridge context-fetch failure → proceed without context (degraded, functional).
- Ephemeral `wants_reply` state lost on uvicorn restart → acceptable; it's UX hint only.

## Testing

### Unit

- **Mentions parser**: basic `@slug`; multiple slugs; `@all`, `@humans`; invalid slug (non-member); word-boundary rejection (`email@x.com`); case-insensitive; deduplication.
- **Group policy**: cooldown blocks; cooldown elapses; rate cap blocks when ≥ cap; rate cap resets after 60s; different channels independent; different agents independent.
- **Slash commands**: each of 11 commands with good args; each with bad args; unknown `/foo`; commands that mutate settings verify persistence; `/help` lists all commands.
- **Reactions**: `👎` by human owner triggers `regenerate`; `👎` by different human does not; `🙋` by agent sets wants_reply with TTL; other emojis do nothing special.

### Integration

- **Lively mode fanout**: 3-agent channel, `response_mode=lively`, human posts one message. Verify all 3 receive it; 2 return `NO_RESPONSE` and are suppressed; 1 replies; reply fans out to the other 2 who both return `NO_RESPONSE`; chain ends.
- **Quiet mode**: 3 agents in channel, `response_mode=quiet`, human posts unmentioned message. Verify no agent is notified.
- **`@mention` in quiet**: human posts "`@tom` ping". Verify only tom is notified with `force_respond=true`. Verify tom's reply is NOT re-dispatched to the other agents (quiet + unmentioned).
- **`@all` override at hop cap**: construct a lively chain at `hops_since_user = max_hops`. Human posts "`@all` restart". Verify all agents are notified with `force_respond=true` and hops reset to 0.
- **Hop cap termination**: 2 agents in lively mode respond to each other. Verify the chain terminates at `max_hops=3`.
- **Cooldown**: agent replies, 1 second later another message arrives; agent is skipped; 6 seconds later another message; agent is reached again.
- **Rate cap**: send 25 messages in 10 seconds to a lively channel. Verify agent deliveries drop after 20 within-minute.

### E2E

- **Playwright roundtable test** (extends existing): post `@all "what framework are you on?"` in the 6-agent roundtable channel; verify all 6 agents reply and their replies mention at least one other agent's name (proving context threading worked).

## Out of Scope (Phase 2 or later)

- Threads
- Pinning
- Typing indicators
- Read receipts
- Ephemeral messages
- Rich attachments / embeds / cards beyond existing text
- UI controls for mode/hops/cooldown/mute (backend ready; UI lands in Phase 2)
- Cross-host routing (multi-process / clustered setups)
- `@here`, `@role` mentions
- Persistent `wants_reply` state across restart
- Taosmd-backed semantic context retrieval (Phase 2 — uses rolling window today)

## Open Questions

None at spec time. Any ambiguity discovered during planning or implementation should be flagged back to this spec for amendment, not resolved silently.

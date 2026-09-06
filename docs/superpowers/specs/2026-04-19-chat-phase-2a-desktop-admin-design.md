# Chat Phase 2a — Desktop Chat Admin + Live Signal Design

**Date:** 2026-04-19
**Scope:** Desktop UI for the Phase 1 admin endpoints, plus typing + thinking indicators. No mobile — that is a separate phase aiming for Slack/Discord-level polish. No threads/pins/attachments — those are Phase 2b. No per-framework typing phases — that is task #41 (Phase 3).

## Goal

One sentence: make Phase 1's control plane discoverable in the desktop chat UI (right-side channel settings panel, agent context menu, slash-command autocomplete) and give humans honest "something is happening" feedback (human typing + agent thinking indicators).

## Non-goals (explicit)

- No new backend capabilities beyond Phase 1's surface, apart from two new ephemeral endpoints (`/typing`, `/thinking`) and the `slash_commands` manifest field on frameworks.
- No mobile/touch variants. Mobile chat polish is its own phase.
- No threads, pins, read receipts, attachments, ephemeral messages — Phase 2b.
- No per-framework typing phases — Phase 3 (#41).
- No UI surface for DM channels' knobs (response_mode, hops, etc. are meaningless in a 1:1 DM and the settings button is hidden).

## Architecture

```
desktop/MessagesApp
  ├─ ChannelSettingsPanel (slide-over, right-side)
  │    ├─ Overview     — rename + topic
  │    ├─ Members      — list + add/remove
  │    ├─ Moderation   — response_mode, muted
  │    └─ Advanced     — max_hops, cooldown_seconds
  │        → all four call existing Phase 1 REST admin endpoints
  │
  ├─ AgentContextMenu (right-click any agent surface)
  │    → DM / (Un)mute / Remove / View info / Jump to settings
  │
  ├─ SlashMenu (composer popup when `/` is first char)
  │    → reads frameworks.py slash_commands manifest
  │    → on select: inserts "@<slug> /<cmd> " at composer start
  │    → Phase 1 bare-slash guardrail enforces at backend
  │
  └─ TypingFooter (strip above composer)
       ├─ humans-typing (POST /api/chat/channels/{id}/typing + WS broadcast)
       └─ agent-thinking (bridge heartbeat POST /thinking around LLM call)
```

## Data Model

### `tinyagentos/frameworks.py` — new optional field per framework

```python
"hermes": {
    # ...existing fields...
    "slash_commands": [
        {"name": "help", "description": "List available commands"},
        {"name": "clear", "description": "Clear the session context"},
        # ...
    ],
}
```

- Field is optional. Missing or `[]` → slash menu shows "(no commands)" for that agent.
- Seeded for the 6 frameworks taOS actively supports (openclaw, hermes, smolagents, langroid, pocketflow, openai-agents-sdk). Static; Phase 2b/3 can replace with a bridge `/capabilities` endpoint.
- Shape validated at app startup via the existing `validate_framework_manifest` helper.

### No channel-store or message-store schema changes

Phase 1 already landed everything we need (`response_mode`, `max_hops`, `cooldown_seconds`, `muted`, `metadata.hops_since_user`). Typing + thinking state is ephemeral and lives in memory (`TypingRegistry` on `app.state`, same pattern as `WantsReplyRegistry`).

## Channel Settings slide-over

### Trigger and layout

- An `ⓘ` icon in the chat header next to the channel name; click opens the panel; `Esc` closes.
- Right-side slide-over, ~360 px wide, overlays chat with a dim scrim on the rest. Uses existing shell primitives (same pattern as the Agent Settings window when reduced).
- In DMs, the icon is hidden entirely — no knob is meaningful for a 2-member 1:1.

### Sections

Collapsible stack; first three open by default, `Advanced` collapsed.

**Overview**
- Channel name — inline editable, 100-char cap, saves on blur via `PATCH /api/chat/channels/{id}` with `{name}`
- Topic — textarea, 500-char cap, saves on blur with `{topic}`
- Type badge (read-only): `Group` / `Topic` / `DM`

**Members**
- List of current members with avatar + slug. Each row has a `Remove` button (suppressed for the current user and for DM counterparts).
- Footer: `Add agent` dropdown of known agents not in the channel. Selecting one calls `POST /api/chat/channels/{id}/members` with `{action: "add", slug}`.

**Moderation**
- `response_mode` toggle: `quiet` / `lively` (pill selector). Changes `PATCH {response_mode}`.
- Muted agents — chip list. Each chip has `×` to unmute (`POST /muted {action: "remove", slug}`). Footer: `Mute agent` dropdown of unmuted member agents.

**Advanced**
- `max_hops` — slider 1..10, default 3. `PATCH {max_hops}` on release.
- `cooldown_seconds` — slider 0..60, default 5. `PATCH {cooldown_seconds}` on release.

### Error handling

- Any `PATCH` / `POST` rejection surfaces as an inline red text under the control (`"max_hops must be 1..10"`). Optimistic UI: roll back the control to its prior value on 4xx.

## Agent Context Menu

### Triggers

- **Right-click** (desktop) on any rendering of an agent:
  - Message author name or avatar in transcript
  - Member-list row in the settings slide-over
  - Agent chip anywhere else (e.g., member-count pill)
- `contextmenu` event preventDefault'd; menu opens at cursor.
- Keyboard: the same menu opens on `Shift+F10` when the agent row is focused. Arrow keys navigate, Enter selects, Esc dismisses.

### Items (in order)

1. `DM @<slug>` — opens or creates the DM channel for `(user, slug)`. If one exists, switches to it; otherwise calls `POST /api/chat/channels` with `{type: "dm", members: ["user", slug]}`.
2. `Mute in this channel` / `Unmute in this channel` — toggle. Calls `POST /muted {action: "add"|"remove", slug}`. Not shown in DMs.
3. `Remove from channel` — calls `POST /members {action: "remove", slug}`. Not shown in DMs.
4. **separator**
5. `View agent info` — opens a small read-only popover (framework, model, status, last-seen). No endpoint added; reads from the existing agents list already held on the client.
6. `Jump to agent settings` — opens the Agents app focused on this agent slug (uses existing app-routing primitive).

### Shared component

Implemented once as `AgentContextMenu` and mounted via a render-prop / context. The caller passes the agent slug + an optional `channelId` (omitted means "no channel-scoped items"). Two identical-looking trigger paths: `onContextMenu` on DOM nodes and a `showContextMenu(x, y, slug, channelId)` imperative handle for non-DOM surfaces.

## Slash Autocomplete Menu

### Trigger and dismiss

- Opens when the composer's value **starts with** `/` (first character, no leading whitespace). `/` typed mid-message does not open it. This is a client-side predicate on input events.
- `Esc` closes. Clicking outside closes. Deleting the `/` closes.
- Picking an entry inserts `@<slug> /<cmd> ` at position 0, sets the cursor at end, closes the menu. Composer still editable — user can add args.

### Discovery

- Reads the `slash_commands` field per framework from `GET /api/frameworks/latest` (existing endpoint) augmented with a sibling `GET /api/frameworks/slash-commands` that returns `{slug: [{name, description}]}` for every agent in the current channel. One fetch per channel on menu open; cached for 5 minutes.
- Unknown frameworks with no `slash_commands` field → empty list for that slug; menu shows "(no commands available)" under their group.

### Layout

- Inline popup anchored to the composer, `max-height: 240px`, grouped by agent in channel member order.
- In a DM (one agent) the grouping header collapses — flat list.
- Each row: `/<cmd>` on the left, `description` (dimmed) on the right, `@<slug>` on the right edge as a muted tag.

### Filter

- Unified fuzzy match on `<slug> <command-name> <command-description>` using a simple sublist-match scorer (no fuzzy library needed for MVP).
- Arrow keys navigate across groups; Enter selects the highlighted row; Tab cycles focus between groups.

### Send-shape contract

- Selection inserts exactly `@<slug> /<cmd> ` (trailing space).
- User hits Enter as normal to send. The message passes the Phase 1 bare-slash guardrail because it now has an explicit `@<slug>` mention.
- Sending bare `/foo` without using the menu → server returns 400 (Phase 1 guardrail). Client surfaces the error as an inline toast near the composer. This is the only hard-backstop; no client-side block is needed.

## Typing + Thinking Indicators

### Data flow

**Humans-typing:**
```
composer keystroke → debounce(1s) → POST /api/chat/channels/{id}/typing {author_id:"user"}
                                 → TypingRegistry.mark(channel, slug, "human", now)
                                 → chat_hub.broadcast("typing", {slug, kind:"human"})
auto-clear: TTL 3s of no refresh
```

**Agent-thinking:**
```
bridge receives user_message → POST /api/chat/channels/{id}/thinking {slug, state:"start"}
                            → TypingRegistry.mark(channel, slug, "agent", now, ttl=45s)
                            → chat_hub.broadcast("thinking", {slug, state:"start"})
bridge posts reply (or suppress) → POST /thinking {slug, state:"end"}
                                → TypingRegistry.clear(channel, slug)
                                → chat_hub.broadcast("thinking", {slug, state:"end"})
ghost-clear: if no heartbeat in 45s, registry auto-clears and broadcasts end
```

### `TypingRegistry` (new)

```python
# tinyagentos/chat/typing_registry.py

class TypingRegistry:
    """In-memory per-channel typing / thinking heartbeat tracker.

    Humans refresh via keystroke-debounced POSTs; agent bridges fire
    start/end around their LLM call. Stale entries auto-clear after a
    per-kind TTL.
    """

    def __init__(self, human_ttl: int = 3, agent_ttl: int = 45) -> None: ...

    def mark(self, channel_id: str, slug: str, kind: Literal["human", "agent"]) -> None: ...
    def clear(self, channel_id: str, slug: str) -> None: ...
    def list(self, channel_id: str) -> dict[str, list[str]]:
        """Returns {"human": [slug...], "agent": [slug...]} with live entries only."""
```

### Endpoints

```
POST /api/chat/channels/{channel_id}/typing
    body: {"author_id": "<slug>"}
    → mark(channel_id, slug, "human"); broadcast; 200 OK

POST /api/chat/channels/{channel_id}/thinking
    body: {"slug": "<slug>", "state": "start" | "end"}
    → mark | clear; broadcast; 200 OK
    → Must accept the Bearer local-token (same auth pattern the bridges already use)

GET  /api/chat/channels/{channel_id}/typing
    → {"human": [...], "agent": [...]} (fallback if WS is down; UI polls at 3s)
```

### Bridge integration

Each `install_<framework>.sh` gains two helper calls around its `_run` block:

```python
async def _thinking(c: httpx.AsyncClient, ch_id: str, state: str) -> None:
    try:
        await c.post(
            f"{BRIDGE_URL}/api/chat/channels/{ch_id}/thinking",
            json={"slug": AGENT_NAME, "state": state},
            headers={"Authorization": f"Bearer {LOCAL_TOKEN}"},
            timeout=5,
        )
    except Exception:
        pass  # typing indicator is best-effort; never block a reply on it

# inside handle(c, evt, ch):
await _thinking(c, evt.get("channel_id"), "start")
try:
    reply = await run_model(...)
finally:
    await _thinking(c, evt.get("channel_id"), "end")
```

All 6 bridges get the same snippet. Best-effort — network failure on `/thinking` never blocks the reply.

### UI rendering

`TypingFooter` renders between the last message and the composer:

- Line 1: humans — `"<name> is typing…"` / `"<name> and <other> are typing…"` / `"<name> and N others are typing…"`
- Line 2: agents — `"<slug> is thinking… · <slug2> is thinking…"`

Fades in/out with short CSS transitions. Empty when nothing is active.

## Components

### New files

| Path | Responsibility |
|---|---|
| `desktop/src/apps/chat/ChannelSettingsPanel.tsx` | Right-side slide-over with 4 collapsible sections |
| `desktop/src/apps/chat/AgentContextMenu.tsx` | Shared right-click / Shift+F10 menu |
| `desktop/src/apps/chat/SlashMenu.tsx` | `/`-triggered composer autocomplete popup |
| `desktop/src/apps/chat/TypingFooter.tsx` | Two-line typing + thinking strip |
| `desktop/src/lib/use-typing-emitter.ts` | Debounced humans-typing emitter hook |
| `desktop/src/lib/channel-admin-api.ts` | Thin REST client for the Phase 1 admin endpoints (PATCH channel, POST members/muted) |
| `tinyagentos/chat/typing_registry.py` | In-memory TypingRegistry |
| `tests/test_chat_typing.py` | Unit tests for registry + route tests for POST endpoints |

### Modified files

| Path | Change |
|---|---|
| `tinyagentos/routes/chat.py` | Three new endpoints: POST `/typing`, POST `/thinking`, GET `/typing` |
| `tinyagentos/app.py` | Wire `TypingRegistry` onto `app.state.typing` |
| `tinyagentos/frameworks.py` | Populate `slash_commands` list for 6 frameworks |
| `tinyagentos/scripts/install_hermes.sh` | 2-line thinking emitter around LLM call |
| `tinyagentos/scripts/install_smolagents.sh` | Same |
| `tinyagentos/scripts/install_langroid.sh` | Same |
| `tinyagentos/scripts/install_pocketflow.sh` | Same |
| `tinyagentos/scripts/install_openai_agents_sdk.sh` + dashed copy | Same |
| `desktop/src/apps/MessagesApp.tsx` | Mount the 4 new components; composer `/` keypress handling; typing emitter hook; WS handler for new broadcast shapes |

## Error Handling

- **PATCH / POST admin failure** → optimistic UI rolls back; inline error under the control.
- **Slash commands manifest missing for a framework** → empty group shown with "(no commands)". Not an error.
- **Bridge `/thinking` POST fails** → swallow, log DEBUG. Typing indicator is best-effort; never block a reply on it.
- **Bridge `/thinking` arrives after server restart** → registry is empty, start/end treated normally, no stale state.
- **Human typing POST fails** → silently drop that keystroke batch; next successful POST refreshes the TTL.
- **GhosT agent thinking (no heartbeat in 45s)** → registry auto-clears; UI fades the entry.
- **Slash menu fetch fails** → menu shows a single "(couldn't load commands)" row; Esc still dismisses.
- **Right-click menu on an agent that was just removed** → items either work (backend 404 is surfaced) or are disabled (`Remove` / `Mute` require the agent to still be a member).

## Testing

### Unit

- `TypingRegistry` — `mark` refreshes TTL; `list` omits expired entries; `clear` is idempotent; different channels are independent.
- `frameworks.py` — every entry with `slash_commands` has well-formed `{name, description}` dicts; empty / missing tolerated.

### Route

- `POST /typing` — marks human, broadcasts `{"type":"typing","kind":"human","slug":"user"}` on the channel's `chat_hub`.
- `POST /thinking` — auth-gated (requires bearer local token); `state=start` marks; `state=end` clears.
- `GET /typing` — returns `{"human":[...],"agent":[...]}`; stale entries excluded.

### Playwright E2E

- Open a group channel, right-click an agent's name in the transcript → context menu appears with correct items.
- Click settings icon → panel slides in → flip response_mode → assert PATCH fires with `{"response_mode":"lively"}`.
- Type `/` in composer → slash menu opens with agents + commands; arrow-down selects a row; Enter inserts `@tom /help ` into composer.
- Type bare `/foo` and Send in a group → inline error toast; message not persisted.
- In a DM, settings icon is hidden.
- Human typing emits `/typing` (observed via route instrumentation) and the footer strip appears on a peer client.

## Out of Scope (Phase 2b / Phase 3 / Mobile)

- Threads, pinning, read receipts, rich attachments, ephemeral messages — **Phase 2b**.
- Per-framework typing phases (`tom is calling search`, `tom is writing`) — **Phase 3**, task #41.
- Mobile/touch variants of every surface (long-press menus, bottom-sheet settings, touch-friendly slash picker) — **separate Mobile Chat Polish phase** aiming for Slack/Discord-level quality.
- Slash-command discovery via bridge `/capabilities` endpoint — future, task to be opened when we tackle it.
- Per-message actions that aren't reactions (edit, delete-your-own-message, copy link, mark unread) — Phase 2b or later.

## Open Questions

None at spec time. Any ambiguity discovered during planning or implementation should be flagged back to this spec for amendment, not resolved silently.

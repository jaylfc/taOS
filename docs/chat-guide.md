# taOS Chat — User Guide

taOS chat is a multi-agent messaging room. Users and agents share channels. Agents
have autonomy: they decide whether to reply based on who is addressed, what the
message contains, and the channel's response policy. You can run structured 1:1
conversations or complex multi-agent workflows inside the same interface.

There are three channel types: **DM** (1:1 between you and one agent), **group**
(multiple humans and multiple agents in one room), and **topic** (a group channel
with a named focus area — useful for scoping an agent team to a project or domain).

Agents run in LXC containers and connect via HTTP bridges. taOS supports multiple
agent frameworks: OpenClaw, Hermes, SmolAgents, Langroid, PocketFlow, and the
OpenAI Agents SDK.

---

## Channels and modes

**Quick:** Channel type determines default agent behavior; response mode controls
whether agents reply silently or eagerly.

### Types

- **DM** — always lively. The single agent always replies. No settings panel.
- **Group** — multiple humans and agents. Response mode applies.
- **Topic** — same as group but carries a named topic shown in the header. Useful
  for domain-scoped teams (e.g. a `devops` channel with `don` and `tom`).

### Response modes

Response mode applies to group and topic channels only. Set it in the channel
settings panel (see [Channel settings](#channel-settings)).

- **`quiet`** (default) — agents only reply when explicitly `@mentioned` or
  when `@all` is used. Best when a channel has many agents and you want low noise.
- **`lively`** — every agent in the channel sees every message and decides
  independently whether to respond. Use this when you want agents to converse
  with each other or to react to ambient context.

### Examples

- A `#research` topic channel with four agents in `quiet` mode: only the agent
  you address will reply.
- A `#brainstorm` group channel in `lively` mode: all agents may weigh in on
  every message, creating a back-and-forth discussion.

---

## Mentions

**Quick:** `@slug` targets one agent; `@all` targets every agent; `@humans` pings
human members only.

### Rules

- `@tom` — addresses the agent with slug `tom` (case-insensitive). A word-boundary
  check is applied: `email@tom.com` is **not** a mention.
- `@all` — forces all channel members to reply, bypassing `quiet` mode.
- `@humans` — pings human channel members. Not an agent trigger.
- Unknown `@names` (slugs not in the channel member list) are silently ignored.
- Agents resolve slugs at dispatch time against the channel member list.

### Mention vs. no mention in quiet mode

| Input | Quiet mode | Lively mode |
|---|---|---|
| `@tom what's the weather` | Only `tom` replies | Only `tom` replies |
| `@all stand-up` | Every agent replies | Every agent replies |
| `tell tom about the weather` | `tom` does **not** reply | `tom` may or may not reply |

### @mention override

An explicit `@mention` bypasses hop limits, cooldown, and rate caps (see
[Hops, cooldown, rate-cap](#hops-cooldown-rate-cap)). An explicitly addressed
agent always gets the chance to reply regardless of other throttle state.

---

## Beads task verbs

**Quick:** In a project's A2A channel, agents and humans can create and update
kanban tasks by including verb lines in their messages. The board updates live
via SSE — cards animate into the new column the moment the verb is processed.

### Scope

Beads verbs only fire in a project's **A2A coordination channel** (the channel
named `a2a` with `kind: a2a` in its settings). Verb lines in any other channel
are ignored. Normal chat text can appear in the same message as verbs — the
bridge dispatches the verbs and otherwise lets the message route normally.

### Verb reference

| Verb | Syntax | Effect |
|---|---|---|
| `/new` | `/new "<title>" [@<assignee>]` | Create a new task in this project. Title must be quoted (double or single quotes). `@<assignee>` is optional — if present it must match a project member's name (case-insensitive). If the name doesn't resolve, the task is created unassigned and a notice is logged. |
| `/claim` | `/claim <tsk-id>` | Claim an existing task as the next worker. Only works if the task is currently open and unclaimed. |
| `/release` | `/release <tsk-id>` | Return a claimed task to the ready queue. |
| `/close` | `/close <tsk-id> [<note>]` | Close a task. Optional note (everything after the task id) is stored as the close reason and posted to the channel. |

Task IDs use the form `tsk_<id>` or `tsk-<id>` (e.g. `tsk_3a9f21`).

### Examples

```
/new "Draft chapter 1" @john
/new "Cover illustration"
/claim tsk_3a9f21
/close tsk_3a9f21 shipped in PR #42
/release tsk_7b0c14
```

Multiple verb lines in a single message are all processed, in document order.

### Live board updates

After each verb, the Kanban board refreshes via SSE — no manual page reload
needed. When a task is closed and its dependents become unblocked, the bridge
posts a `⚡ <tsk-id> ready` notification in the A2A channel so agents know
which tasks just became actionable.

---

## Hops, cooldown, rate-cap

**Quick:** Three independent throttles prevent agent flooding; an explicit `@mention`
overrides all three.

### Hop counter

Each human-originated message resets the hop counter to 0. Every time an agent
replies to a previous agent reply, `hops_since_user` increments. Once
`hops_since_user >= max_hops` (default: `3`), the router stops dispatching until
a human sends the next message.

- Configurable per channel via `max_hops` in the settings panel.
- Purpose: caps open-ended agent-to-agent chains that drift from user intent.

### Cooldown

Each agent has a per-channel cooldown: it cannot reply within `cooldown_s`
(default: `5s`) of its own last reply in the same channel.

- Configurable per channel via `cooldown_s` in the settings panel.
- Purpose: prevents burst-flooding when multiple triggers fire rapidly.

### Rate cap

Each channel has a per-minute message cap (default: `20` agent messages per
minute). If agents exceed this, the next dispatch attempt is dropped.

- Configurable per channel in the settings panel.
- Purpose: circuit breaker for runaway lively-mode conversations.

### @mention override

An explicit `@mention` bypasses all three throttles. Use `@tom` or `@all` when
you need a response regardless of throttle state.

### Thread scoping

In threads, hop/cooldown/rate-cap scope **per thread**, not per channel. The
policy key is `channel_id:thread:parent_id`. A noisy thread cannot lock out the
rest of the channel.

---

## Reactions

**Quick:** Emoji reactions are expressive by default; two emoji carry special
agent-behavior semantics.

### Adding a reaction

Hover a message → click the 😀 button in the hover toolbar → the emoji picker
opens. Any Unicode emoji is valid.

### Special reactions

- `👎` — added by a channel's human on an **agent's reply** → the agent is
  prompted to regenerate that reply. The original message is replaced in-place.
- `🙋` — added by an agent → "hand raise" badge. Signals that the agent wants to
  contribute next. Does **not** auto-dispatch a reply; the agent will not speak
  until addressed or until a human's next message triggers it in lively mode.

### All other emoji

Pure expression. No routing side-effect. Counts are shown on the message; any
channel member can add or remove them.

---

## Slash menu

**Quick:** Type `/` in the composer to open a fuzzy-searchable list of all agent
commands available in the current channel.

### Opening and using the menu

1. Place the cursor at the start of the composer and type `/`.
2. The menu opens, listing commands grouped by agent.
3. Type more characters to fuzzy-filter (`/he` narrows to `/help`,
   `/hermes-version`, etc.).
4. Press **Enter** (or click) to select — this inserts `@<agent> /<cmd>` into
   the composer, ready to send or edit.

### Guardrail

In group and topic channels, a bare slash message (no `@<slug>` or `@all`) that
reaches the server returns **400 — Bad Request**. This prevents a command like
`/clear` from broadcasting to every agent simultaneously.

- `/help` is exempt from this guardrail (it is a taOS control command, not a
  framework command routed to agents).
- DM channels are also exempt — there is only one agent and addressing is implicit.

### Examples

- `/hermes-version` in a group channel → guardrail fires; you must send
  `@don /hermes-version`.
- `/help threads` in any channel → taOS processes it locally and posts a system
  message; no guardrail.

---

## Channel settings

**Quick:** Click ⓘ in the chat header to open the settings slide-over for the
current channel.

### Editable fields

| Field | Default | Notes |
|---|---|---|
| Name | (set at creation) | Group and topic channels only |
| Topic | — | Topic channels only |
| Members | — | Add or remove humans and agents |
| Muted agents | — | Listed agents won't auto-reply; still receive explicit @mentions |
| Response mode | `quiet` | `quiet` or `lively` |
| `max_hops` | `3` | Integer ≥ 1 |
| `cooldown_s` | `5` | Seconds ≥ 0 |
| Rate cap | `20` | Agent messages per minute |

### Constraints

- DM channels have **no settings panel** — they are fixed 2-member 1:1s.
- The settings panel shares the right-side slot with the thread panel. Opening one
  closes the other.
- Changes take effect immediately for new dispatches; in-flight replies are not
  recalled.

---

## Agent context menu

**Quick:** Right-click an agent's name or avatar anywhere in chat to open a
context menu with per-agent actions.

### Opening the menu

- **Mouse:** right-click the agent's name or avatar in a message header, the
  member sidebar, or the thread panel.
- **Keyboard:** focus a message row and press **Shift+F10**.

### Actions

- **DM** — opens a direct message channel with that agent (or navigates to the
  existing one).
- **Mute / unmute** — toggles the agent's muted state in the current channel
  without opening the settings panel.
- **Remove from channel** — removes the agent from the member list. Requires
  channel admin rights.
- **View agent info** — opens a summary card: slug, framework, container status,
  model, uptime.
- **Jump to agent settings** — navigates to that agent's entry in the Agents app.

---

## Threads

**Quick:** Reply to any message in a side thread; routing and throttles scope
narrowly to that thread.

### Starting a thread

Hover a message → click 💬 **Reply in thread** → the thread panel opens on the
right side of the screen.

### Routing rules

Thread replies are routed **narrowly** to:

- The parent message's author (if an agent).
- Any agents who have already replied in this thread.
- Any agents explicitly `@mentioned` in the reply text.

`@all` inside a thread escalates to every channel agent, the same as in the main
channel.

### Context window

The agent's context window for a thread reply is built from **thread replies
only** (not main-channel messages), with the parent message prepended at the top.
This keeps the context focused and avoids irrelevant channel history.

### Throttle scoping

Hops, cooldown, and rate-cap all scope **per thread** (policy key:
`channel_id:thread:parent_id`). A thread that hits its hop limit does not affect
the channel's main timeline.

### Panel behavior

The thread panel shares the right-side slot with the channel settings panel.
Opening one closes the other.

---

## Attachments

**Quick:** Attach files via the paperclip button, drag-and-drop, or paste; up to
10 files, 100 MB each.

### How to attach

- **Paperclip button** — click in the composer toolbar → file picker opens with
  tabs: **Disk** (local files), **My workspace** (your agent workspace),
  **Agent workspaces** (files written by agents you have access to).
- **Drag-and-drop** — drag a file onto the composer area.
- **Paste** — paste from clipboard (images and text files).

### Limits

- Up to **10 attachments** per message.
- Up to **100 MB** per file.

### Image rendering

- 1 image → full-size inline (max 560 × 400 px).
- 2–4 images → 2-column grid.
- 5+ images → first 4 shown; the 4th tile shows an `+N more` overlay.
- Click any image to open the lightbox; navigate with ← / → or press **Esc** to
  close.

### Non-image files

Rendered as a file tile: icon + filename + size, with a clickable download link.

### Workspace files

Selecting a file from **My workspace** or **Agent workspaces** creates a
reference-only attachment. The message stores the path; the file stays in place.
Use this to point agents at files they (or you) have already written without
copying them into the message.

### Agent visibility

Agents receive attachments as a structured payload. A short footer is appended to
the prompt context describing each attachment:

```
User attached: doc.pdf (application/pdf, 200 KB)
User attached: screenshot.png (image/png, 84 KB)
```

Image attachments are passed as vision content where the agent's model supports
it.

---

## /help

**Quick:** `/help` lists all topics; `/help <topic>` shows a cheat sheet for one
topic.

### Usage

```
/help
/help channels
/help mentions
/help hops
/help reactions
/help slash
/help settings
/help context
/help threads
/help attachments
/help help
```

### Behavior

- Handled entirely in-app — no agent receives the message.
- Posted as a **system message** in the current channel. Only you see it; agents
  do not react to it and it does not increment hop counters.
- `/help` bypasses the bare-slash guardrail in group and topic channels — you
  never need to prefix it with an `@mention`.
- The cheat sheet for each topic includes a link to the relevant section anchor
  in this guide.

### Topic-to-anchor map

| Topic | Section |
|---|---|
| `channels` | [#channels-and-modes](#channels-and-modes) |
| `mentions` | [#mentions](#mentions) |
| `beads` | [#beads-task-verbs](#beads-task-verbs) |
| `hops` | [#hops-cooldown-rate-cap](#hops-cooldown-rate-cap) |
| `reactions` | [#reactions](#reactions) |
| `slash` | [#slash-menu](#slash-menu) |
| `settings` | [#channel-settings](#channel-settings) |
| `context` | [#agent-context-menu](#agent-context-menu) |
| `threads` | [#threads](#threads) |
| `attachments` | [#attachments](#attachments) |
| `help` | [#help](#help) |

---

## Edit and delete your own messages

Hover a message → `⋯` → **Edit** (your own text only). Press **Enter** to save, **Esc** to cancel. A small `(edited)` marker shows next to the author line afterward.

`⋯` → **Delete** removes your message with a tombstone ("_This message was deleted_"). Thread replies remain anchored to the parent so the thread still makes sense. You cannot delete another user or agent's messages.

Edits are text-only — attachments, thread placement, and metadata cannot be changed after send.

## Pinning

Hover a message → `⋯` → **Pin**. The channel header shows a `📌 N` badge with the pin count. Click the badge to see the pinned list; each entry has a **Jump to →** action that scrolls to the original message.

Up to **50 pins per channel**. Pinning a 51st message returns an error.

Only humans can pin. Agents can **request** a pin by adding a 📌 reaction to their own message — humans see a `@agent wants to pin this` pill below the message with a one-click **📌 Pin this** button.

## Copy link and deep links

`⋯` → **Copy link** copies a URL in the form `https://<host>/chat/<channel>?msg=<message>` to your clipboard. Opening that URL in taOS scrolls to the message and briefly outlines it in yellow. Paste into email, Slack, docs, tickets — anywhere you share links.

## Mark unread

`⋯` → **Mark unread** rewinds your read cursor to just before that message. The channel list's unread badge updates accordingly. No notifications re-fire.

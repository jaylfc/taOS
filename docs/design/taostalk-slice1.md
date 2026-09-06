> **PARTIALLY SUPERSEDED 2026-07-26.**
>
> Anything in this doc that translates a captured CLI session into synthetic chat
> rows is superseded - there is no session mirroring and no tmux capture. The
> content-block renderers and the taOStalk surface work remain valid.
>
> Current statement: jaylfc/taOS#2150.

# taOStalk Slice 1: Tier 0 Network Push+Poll Session Bridge

**Spec + phased plan for #1953 (epic #1952-1957).** Status: draft for owner review.
**Baseline:** origin/dev.

Position inherited from taostalk-acp-bridge.md: ACP/filesystem attach is Tier 1
(same-host only). Tier 0 network push+poll is the universal baseline. Slice 1
ships it: a running remote agent's live turn-by-turn session (thinking, tool
calls, streamed text, status, questions) appears in a taOS chat surface, and the
operator can reply/steer from taOS.

Recon confirmed against dev at three load-bearing points: `_ALLOWED_SCOPES`
(agent_registry.py) has no chat scope and the equality test
`tests/test_agent_internal_mint.py` asserts it equals the consent-flow
`VALID_SCOPES`; `_AGENT_TOKEN_PATHS` (auth_middleware.py) is registry feeds + bus
read/write + task/canvas regexes only, closed by design; `_resolve_send_identity`
forces the bus `from` to the caller's registry handle; `bridge_session.record_reply`
resolves channel via trace_id with a DM fallback.

## 1. North-star walkthrough

1. **Messages app (taOStalk surface):** Jay opens Messages. Under the agent's
   entry there is a channel `#session-taos-dev` with a live badge (driven by the
   existing `thinking` WS event).
2. **The live thread:** @taOS-dev, running off-host, is mid-task. Jay sees one
   message bubble per turn filling in live: a collapsible Thinking section, a
   "Ran tool: Bash" card that flips running to done, streamed answer text via the
   existing `message_delta` WS event, then the bubble settles to `complete` on
   turn end.
3. **A question surfaces:** the agent emits a `question` event; it renders as a
   highlighted card in the thread. No new inbox, no Decisions dependency.
4. **The reply:** Jay types in the normal Messages composer. The row lands via
   the existing cookie-auth chat path; a controller-side forwarder relays it onto
   the agent's bus thread; @taOS-dev, already polling `GET /api/a2a/bus/messages`,
   picks it up and continues.
5. **The payoff (collab epic #2012):** a delegated external agent from another
   taOS user has the same registry-JWT + `a2a_send` dialect, so its donated
   session appears in the recipient's taOStalk with zero extra plumbing. "Watch
   the donated agent think" is the demo.

Nothing here requires a new client, a new socket, or a new auth plane. That is
the point of Tier 0.

## 2. The transport decision (the crux)

A remote registry-JWT agent cannot write chat today: `/api/chat/messages` is not
in `_AGENT_TOKEN_PATHS`, and no chat scope exists. Two candidate fixes:

**Option A - A2A bus as the wire.** The agent posts JSON session-event envelopes
(as the `body` string) to a dedicated bus thread via `POST /api/a2a/bus/send`
(scope `a2a_send`, `from` forced to caller). A new controller-side
**SessionEventTranslator**, modeled on `bridge_session.record_reply`, consumes
those threads (bus SSE stream with poll fallback), parses envelopes, writes
`chat_messages` rows with `content_blocks`, and broadcasts via ChatHub. Reply
path: a forwarder relays operator messages from the session channel back onto the
same bus thread; the agent polls `GET /api/a2a/bus/messages?since=` or subscribes
to `/api/a2a/bus/stream` (`a2a_receive`).

**Option B - agent-token chat ingest.** A new scope (`session_events`) added to
`_ALLOWED_SCOPES` + `VALID_SCOPES` (the equality test forces both), a new
`POST /api/chat/sessions/events` ingest endpoint added to `_AGENT_TOKEN_PATHS`,
self-attribution like `_resolve_send_identity`.

**Decision: Option A.**

- It is the shipped dialect. @taOS-dev already talks to taOS over exactly this
  pair, and every delegated agent from the merged collab epic holds
  `a2a_send`/`a2a_receive`. Slice 1 works day one for both flagship demos with
  zero grant migration, zero re-consent, zero scope churn.
- Auth planes stay separated. The registry-JWT plane keeps touching only the
  closed bus/feeds/task/canvas allowlist. Chat rows are written exclusively by
  trusted controller code. No CSRF or middleware changes. Option B widens the
  closed allowlist, forces the `VALID_SCOPES` sync (consent UI, mint route,
  equality test), and puts an agent-writable endpoint in front of the chat store,
  all for a slice whose job is to prove the surface.
- The envelope is transport-agnostic. The translator consumes an envelope, not a
  bus message. If event volume ever justifies a direct ingest hop, Option B
  becomes a carrier swap behind the same translator, not a redesign.

Accepted costs of A: JSON-in-a-string envelopes (versioned), double persistence
(bus row + chat row; bus retention already handles this), and one long-running
translator task in the controller. All cheap. Reply/steer path, explicitly: the
operator reply is written by the forwarder to the bus thread; the agent's
steering input arrives via its existing `a2a_receive` poll or SSE subscription.
No new agent-side capability beyond "also watch this thread."

Rejected: a new WebSocket ingest for agents (new auth surface + connection
management for zero slice-1 benefit) and direct bus-to-browser delivery bypassing
chat rows (loses history, search, and the already-solved ChatHub delivery).

## 3. Session-event schema (minimal; taxonomy growth is slice 2)

Bus thread naming: `taostalk:{agent_handle}`. Envelope, JSON-encoded into the bus
`body`:

```json
{
  "v": 1,
  "type": "turn_start | text_delta | thinking | tool_call | tool_result | status | question | turn_end | error",
  "session_id": "sess-...",
  "turn_id": "turn-...",
  "seq": 14,
  "ts": "2026-07-19T10:00:00Z",
  "data": { }
}
```

`seq` is per-turn monotonic; the translator orders by it and tolerates gaps (bus
delivery is at-least-once with cursor). One chat row per turn:

| Event | Effect on the chat row (existing fields only) |
|---|---|
| `turn_start` | Create row: sender = agent, `state=streaming`, `content_type="session_turn"`, `metadata={session_id, turn_id}`. Broadcast `message`. |
| `text_delta` | Append `data.text` to content + the trailing `{kind:"text"}` block. Broadcast `message_delta`. |
| `thinking` | Append/extend `{kind:"thinking", text, collapsed:true}` block; fire the existing `/thinking` phase signal (`phase=thinking`) so TypingFooter lights up unchanged. |
| `tool_call` | Append `{kind:"tool_call", call_id, name, input_preview, status:"running"}` block. Phase `tool`. |
| `tool_result` | Find block by `call_id`, set `status:"done"|"error"`, `result_preview` (truncate ~2KB). Unmatched result becomes standalone. |
| `status` | Append `{kind:"status", text}` block. |
| `question` | Append `{kind:"question", text, options?}` block; row stays `streaming`. Reply is a normal chat message, no decisions dependency. |
| `turn_end` | Set final content, `state=complete`. Broadcast `message_state`. |
| `error` | `state=error` + a status block with the message. |

Nine event types, four block kinds. Everything richer (diffs, files, images,
interactive components, per-framework blocks) is explicitly slice 2 (#1954).

## 4. Minimal renderer (slice 1 scope)

All net-new, since `MessagesApp.tsx`'s `Message` interface has no `content_blocks`
and `renderContent()` is markdown-plus-one-canvas-case:

- Extend the `Message` TS interface with `content_blocks?: ContentBlock[]` (plus
  the already-persisted `state`).
- A tiny dispatcher in `renderContent()`: if `content_blocks` is non-empty, map
  blocks through `switch(block.kind)`; else fall through to today's markdown path
  (zero regression risk for ordinary messages).
- Four block components: **TextBlock** (reuse the markdown renderer),
  **ThinkingBlock** (collapsed-by-default disclosure, dim), **ToolCallBlock**
  (compact card: name, input preview, running spinner or done check),
  **StatusBlock** (single muted line; `question` renders through it with an accent
  border and "reply below" hint in slice 1).
- **Unknown-kind fallback:** a dim "unsupported block" line with the kind name.
  This is the slice-2 seam.
- Wire nothing new on WS: `message`, `message_delta`, `message_state`, `thinking`
  already reach subscribed browsers via ChatHub; TypingFooter already renders
  phases.

Deferred to slice 2 (#1954), stubbed by the dispatcher + fallback: the renderer
registry (kind to component registration), question as an interactive answer
component, tool-specific cards, diff/code/file/image blocks, per-harness
renderers. Slice 1 hardcodes the four-case switch on purpose; slice 2 replaces
the switch with the registry without touching the block schema.

## 5. Auth / security

- **Scope used:** `a2a_send` for push, `a2a_receive` for steering. No scope
  additions, no `_ALLOWED_SCOPES` / `_AGENT_TOKEN_PATHS` / equality-test edits in
  slice 1 (that is Option B's upgrade path).
- **Self-attribution:** `_resolve_send_identity` already forces `from` to the
  caller's registry handle. The translator additionally enforces **thread-from
  binding**: events in `taostalk:{handle}` are dropped and logged unless bus
  `from == handle`. So agent A cannot inject blocks into agent B's session even by
  posting to B's thread.
- **Channel targeting:** one **per-agent session channel** (`#session-{slug}`),
  created by the connect wizard, members = operator + agent, with
  `settings.taostalk_agent = handle` as the translator's binding. The translator
  writes only to the bound channel. Session boundaries are `status` blocks;
  per-session threads deferred.
- **Operator reply authorization:** replies use the existing cookie-authenticated
  Messages composer, so the session/CSRF plane governs them unchanged; the
  forwarder relays only messages authored by human members of a bound session
  channel. Note: `POST /api/chat/messages` has no in-handler auth check and is
  protected only by not being on the agent-token allowlist; slice 1 does not
  change it but flags it as a hardening follow-up (DP-6).
- **Plane composition:** browser plane (cookies + WS) reads; agent plane
  (registry JWT) writes only to the bus; controller code is the sole bridge. The
  `/thinking` heartbeat stays host-local (`validate_local_token`); remote agents
  get the same visual effect because the translator fires the phase signal
  server-side.
- **Abuse caps in the translator:** max event size (bus already caps body),
  per-turn block-count cap, preview truncation, and a per-agent event-rate ceiling
  with drop+log.

## 6. "Connect a session" onboarding (acceptance requirement, thin)

Scenario: the agent already has a registry identity + token (collab-epic
delegated agents qualify automatically).

Wizard in the Messages app, three steps, one modal:

1. **Pick agent:** dropdown of registry agents holding `a2a_send`.
2. **Create surface:** one backend call creates (or finds) `#session-{slug}` with
   the binding and registers the thread with the translator.
3. **Hand over the snippet:** a copyable per-harness snippet that emits envelope
   events to `POST {taos}/api/a2a/bus/send` with `thread: "taostalk:{handle}"` and
   polls `GET /api/a2a/bus/messages` for steering. Slice 1 ships generic
   curl/bash + one worked hook example (OpenClaw/claude-code hook shape). A "Test
   connection" button waits for the agent's first `status` event and shows a
   green check.

No credential minting, no harness detection, no adapter matrix.

## 7. Reconciliation with adjacents

**Reused:** ChatHub broadcast (delivery to desktop, untouched), `chat_messages`
content_blocks/state/metadata fields (already in the schema, unused by the
frontend until now), the `bridge_session.record_reply` mapping shape (the
translator is its registry-JWT-facing sibling; `bridge_session` itself stays
openclaw-only and unmodified), the A2A bus as wire + steering return path,
existing channel/DM machinery, the `thinking` phase WS event + TypingFooter.

**Left alone:** `coding_sessions/` (tmux capture-pane snapshots are pane-shaped,
not turn-shaped; only its registration pattern informed the wizard), Observatory
(fleet dispatch steering), ACP/filesystem attach (Tier 1, same-host, later),
Neko browser, Decisions app.

**Hard constraint honored:** slice 1 depends on nothing unmerged. In particular
`decisions_write` is NOT on dev (the taostalk-acp-bridge.md claim is stale);
`question` events render as passive highlighted blocks answered by ordinary chat
replies. Collab payoff: delegated agents from #2012 already speak this dialect,
so their sessions light up in taOStalk with only the wizard step.

## 8. Phased plan

**P0 - transport + translator (lead).** Envelope schema + versioning;
SessionEventTranslator (bus SSE consume with poll fallback, envelope parse,
thread-from enforcement, chat-row mapping per section 3, caps); reply forwarder
(session-channel human rows to bus thread); channel binding storage; unit tests
incl. out-of-order seq, oversized events, impostor thread posts. Verify: pytest +
a scripted fake agent driving a full turn into a real channel.

**P1 - wizard backend (lead).** Create/find-bound-channel endpoint, snippet
payload generation, test-connection wait endpoint.

**P2 - frontend + snippets (fleet builders, many claimable cards, each
independently landable):**
1. `Message` interface + `ContentBlock` types
2. Dispatcher in `renderContent()` with markdown fallback
3. TextBlock
4. ThinkingBlock (collapsible)
5. ToolCallBlock (running/done states)
6. StatusBlock + question accent variant
7. Unknown-kind fallback block
8. Live-badge on bound session channels (from `thinking` events)
9. Wizard modal steps 1-2 (agent pick + create)
10. Wizard step 3 (snippet display + copy + test-connection poll)
11. PowerShell twin of the bash snippet (worker-script policy)
12. Component tests per block; Playwright e2e: fake agent posts a turn, blocks
    render, reply round-trips
13. ARIA/keyboard pass on wizard + blocks

**P3 - demo + docs (lead, on the Pi via the control API).** @taOS-dev's real
session visible + steerable in taOStalk; screenshot proof; docs sweep of the
taostalk-acp-bridge.md corrections (the decisions_write claim).

**hognek:** nothing required; optional reciprocal card = a session-event emitter
hook for their harness.

**Minimal demo path:** P0 + cards 1, 2, 3, 4 + reply forwarder = an existing
remote agent's session visible turn-by-turn and replyable in taOStalk.

## 9. Decision points for the owner

1. **Transport: A2A bus + controller translator (A) vs new chat scope + ingest
   (B).** Recommended: A. Ships on the already-granted dialect, keeps the agent
   auth surface closed, works instantly for @taOS-dev and delegated agents; B
   stays documented as a later carrier swap behind the same envelope.
2. **Session surface: per-agent `#session-{slug}` channel (rec) vs the agent's
   existing DM vs per-session channel.** Recommended: per-agent channel. DM
   flooding is real during long sessions; per-session channels add lifecycle
   churn for no slice-1 benefit. DM stays a translator fallback.
3. **Question events: passive highlighted block + normal reply (rec) vs blocking
   on decisions_write.** Recommended: passive block. decisions_write is unmerged;
   slice 1 must not depend on it. Interactive answer component is slice 2.
4. **Reply forwarding: all human messages in the bound channel forwarded (rec) vs
   mention-gated.** Recommended: all. It is a dedicated channel; if you typed
   there, you meant the agent.
5. **Snippet coverage: generic curl/bash + ps1 plus one OpenClaw hook example
   (rec) vs a multi-harness matrix.** Recommended: minimal pair. The adapter
   matrix belongs with the harness-swap epic.
6. **Hardening follow-up: file an issue for the unauthenticated
   `POST /api/chat/messages` handler (rec: yes, separate from slice 1).**

## Acceptance for #1953

A running remote registry-JWT agent's session appears live (thinking, tool cards,
streamed text, status) in a taOStalk channel; the operator's typed reply reaches
the agent over its existing bus poll; the Connect-a-session wizard produces a
working snippet confirmed by a test event; zero new scopes, zero unmerged
dependencies; the full renderer registry is deferred to #1954.

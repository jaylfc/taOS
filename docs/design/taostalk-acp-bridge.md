> **SUPERSEDED 2026-07-26 - DO NOT BUILD FROM THIS DOC.**
>
> This design proposes mirroring a CLI session into taOStalk, including PTY
> capture as a fallback. That approach was **rejected by Jay**: there is no
> session mirroring, no PTY, no tmux, and no capturing terminal output into chat.
>
> The current model is that **the A2A thread IS the conversation** - an agent
> reads the bus and posts to the bus, and taOStalk renders the thread. This is
> harness-agnostic because participation needs an HTTP client and nothing else.
> A design that requires a terminal multiplexer or knowledge of how a particular
> CLI draws its screen is the wrong design.
>
> Current statement: jaylfc/taOS#2150. Retained for history; nothing is deleted.

# taOStalk: live agent sessions in taOS chat over ACP

## Goal

Bring live CLI agent sessions (this Claude Code session as `@taOS-dev`, plus the
`@taOSmd-dev` and `@taOS-website-dev` sessions) into the taOS Messages / taOStalk
app as first-class chat channels, so the operator reads and replies from taOS and
the agent responds in real time. Anything a coding CLI can present to the operator
in its own terminal (formatted text, code, diffs, tool activity, multiple-choice
questions, permission prompts, plans, files, progress) must render in taOStalk with
full parity. This is the surface that lets the operator move day-to-day comms and
project management into taOS.

## Non-goals

- Replacing the A2A bus. A2A stays the agent-to-agent coordination transport; ACP
  is the human-in-the-loop session transport. They coexist.
- Managing an external agent's model. taOS owns the agent identity, grants, and the
  channel; the model lives on the agent's side. We steer via chat + ACP.

## Decisions (locked with owner)

- **Transport: ACP (Agent Client Protocol)**, real-time bidirectional. Core is
  **harness-agnostic**; required v1 adapters are **Claude Code, kilo, grok, opencode**.
- **Interactive questions live in chat AND the Decisions app, synced.** Answering in
  either place updates both and round-trips to the agent.
- **Full presentation parity** with the coding-CLI terminal is the acceptance bar.

## What already exists (build on, do not reinvent)

- `tinyagentos/adapters/acp_adapter.py`: an ACP client (JSON-RPC over stdio) speaking
  `session/new`, `session/prompt`, streaming `session/update`, `tool_call` /
  `tool_call_update`, and `session/request_permission` (a request that expects a
  response). Already maps updates to taOS reply kinds via `bridge_session.py`.
- `tinyagentos/bridge_session.py`: `record_reply` bridge for session output.
- Chat message model (`tinyagentos/chat/message_store.py`, `channel_store.py`): each
  message already carries `content_blocks`, `embeds`, `components`, `attachments`,
  `metadata` (all JSON). The renderable surface is a data field, not a rewrite.
- Agent DM channels: every taOS agent gets a 1:1 `type="dm"` channel; the A2A bus is
  already surfaced in Messages.
- Decisions system (`tinyagentos/decisions/decision_store.py`):
  `DECISION_TYPES = (single_select, multi_select, approve_deny, free_text)` with
  `options`, `answer`, `from_agent`, `project_id`. This IS the AskUserQuestion model.
  The agent-side write permission (`decisions_write` scope + gating) landed this cycle.

## Architecture

```
CLI harness (Claude Code | kilo | grok | opencode)
        |  ACP (JSON-RPC / stdio)
   harness adapter  ---->  ACP session manager (harness-agnostic core)
        |                       |
        |                  session <-> channel registry (1 session = 1 DM channel)
        v                       v
   content-block mapper  --->  chat message (content_blocks + components)
        ^                       |
        |                  taOStalk renderer registry (per block type)
   user input / answers  <----  operator (chat reply, choice selection)
        |
   request_permission / ask  <-> Decision (single/multi_select/approve_deny) synced to Decisions app
```

- **ACP session manager (core, harness-agnostic):** owns ACP connections, maps each
  session to a chat channel, tracks presence/lifecycle (connect, active turn, idle,
  disconnect). Generalizes the current OpenClaw-specific bridge.
- **Harness adapters:** thin per-CLI shims that launch/attach the harness over ACP.
  Claude Code and opencode speak ACP; kilo and grok get a wrapper if they do not
  expose ACP natively (fallback: PTY capture -> ACP-shaped events). Adapter registry
  keyed by harness name, resolved the same way agent framework adapters already are.
- **Content-block mapper:** translates ACP `session/update` payloads (and the richer
  terminal surface) into taOS chat `content_blocks` + `components`. This is the parity
  layer.
- **taOStalk renderer registry (frontend):** a pluggable set of renderers, one per
  block type, in the chat message view. Adding a new presentable type = adding a
  renderer + a mapper entry.
- **Interactive questions:** an agent question (ACP `request_permission`, or an
  AskUserQuestion-equivalent) creates a Decision (typed single/multi_select/
  approve_deny/free_text with `options`) that renders inline in chat as choice buttons
  AND appears in the Decisions inbox. The operator's selection writes the Decision
  `answer` (either surface), which round-trips over ACP as the permission/prompt
  response. Reuses the decisions_write plumbing.

## Presentation parity target

| Coding-CLI surface | taOStalk content-block / component |
|---|---|
| Markdown / formatted text | rich text block |
| Code block | syntax-highlighted CodeBlock (exists) |
| Single / multi choice question | choice-button component -> `single_select`/`multi_select` decision (synced) |
| Permission / approve-deny prompt | approve/deny component -> `approve_deny` decision |
| Plan (ExitPlanMode) | plan card with approve-to-proceed |
| Tool use (name + status + detail) | collapsible tool-call activity card |
| File diff | diff viewer block |
| Table | table block |
| File / image | attachment / inline image (attachments exist) |
| Artifact (rendered HTML) | sandboxed artifact frame |
| Progress / status | live status line |
| Thinking (visible reasoning) | collapsible thinking block |
| Mermaid / diagram | rendered diagram |

## Data flow: an interactive question, end to end

1. Agent (over ACP) emits `session/request_permission` or an AskUserQuestion with
   options.
2. ACP manager -> content-block mapper creates a Decision (`single_select` etc.,
   `from_agent`, `project_id`, `options`) and posts a chat message whose `components`
   reference that decision id.
3. taOStalk renders choice buttons inline; the Decisions app shows the same pending
   decision (single source of truth = the decision row).
4. Operator selects an option in chat OR answers in Decisions -> both write the same
   decision `answer` + `answered_at`.
5. ACP manager observes the answered decision -> sends the ACP permission/prompt
   response -> the agent's turn continues.

## Decomposition (buildable slices)

1. **ACP bridge core + Claude Code adapter.** Harness-agnostic session manager
   (session <-> channel), bidirectional TEXT streaming. Acceptance: this Claude Code
   session appears live in taOStalk; operator replies from taOS and the agent responds.
2. **Content-block renderer registry.** Markdown, code, diff, table, image/file,
   tool-call card. Acceptance: a session that streams those types renders them.
3. **Interactive questions, synced.** Choice/approve components inline in chat AND in
   Decisions, answer round-trips over ACP. Acceptance: agent asks a single_select in
   chat, operator picks in either surface, agent proceeds.
4. **Harness adapters: kilo, grok, opencode.** Bring the required v1 set to parity
   with the Claude Code adapter (native ACP where available, PTY->ACP wrapper else).
5. **Parity finish + taOStalk surface.** Plans, thinking, artifacts/HTML, progress,
   mermaid; session-list UX (live/idle/disconnected), presence, per-session channel
   entry. Acceptance: the parity table above is fully covered.

## Testing

- Core: unit-test the ACP session manager (session<->channel mapping, lifecycle) and
  the content-block mapper (each ACP update -> expected content_block/component) with
  recorded ACP fixtures.
- Questions: test the Decision round-trip (question -> decision row -> answer in chat
  and in Decisions -> ACP response) end to end.
- Renderers: vitest per renderer (given a content_block, renders the expected UI).
- Adapters: a smoke test per harness that a `session/prompt` produces a streamed
  reply into the mapped channel.

## Open questions for the plan phase

- Where the ACP session manager runs (controller process vs a per-session worker) and
  how it survives a controller restart mid-session.
- Whether kilo/grok expose ACP natively or need the PTY->ACP wrapper (scoped in slice 4).
- Auth: an ACP session binds to the agent's registry identity + the operator's session;
  confirm the channel membership + grant model reuses the existing DM-channel gating.

## ACP wiring per harness (researched 2026-07-17)

All four required harnesses expose ACP natively or via a first-class adapter, over
newline-delimited JSON-RPC on stdin/stdout. **No PTY-to-ACP wrapper is needed** (this
supersedes the earlier slice-4 assumption). Each adapter is just the correct launch
command + auth passthrough; taOS's existing `acp_adapter.py` already speaks this wire.

| Harness | ACP entry point | Notes |
|---|---|---|
| Claude Code | `@agentclientprotocol/claude-agent-acp` (npm, Apache-2.0; renamed from `@zed-industries/claude-code-acp`) | Wraps the Claude Code SDK, translates to ACP JSON-RPC. Auth via `/login` (API key or Claude Code billing). |
| Kilo | `kilo acp` (native) | CLI invoked with the `acp` argument; JSON-RPC handshake over stdin/stdout. |
| opencode | `opencode acp` (native) | Starts an ACP server exposing its agent loop + tool registry + interactive permissions. |
| grok | `grok --acp` (native; listed as "Grok Build" in the ACP registry) | JSON-RPC-over-stdio agent-server mode. Verify exact flag against the installed grok CLI version. |

Reference implementations to mine (do not fork): `openclaw/acpx` (headless stateful ACP
client), Zed's open-source Claude Code adapter, and the ACP spec at agentclientprotocol.com.

**Consequence for the plan:** slice 4 becomes "register the launch command + auth per
harness" (thin), not "build wrappers". Each harness owns its own model/tools/auth
(consistent with the external-agent model); taOS owns the session-to-channel bridge,
identity, grants, and the rendered surface.

## Attach-existing is the primary path (adopt the claude-command-center mechanism)

Correction to the ACP-spawn framing above: the primary need is bringing ALREADY-RUNNING
CLI sessions into taOS, not spawning fresh ones. Vanilla ACP is spawn-oriented (the client
launches the agent binary), so attach-existing is a separate, per-harness mechanism.
`amirfish1/claude-command-center` (CCC) already solves it for Claude Code; adopt (do not
fork) its approach:

**Claude Code attach (proven by CCC):**
- **Discover** running sessions: scan `~/.claude/sessions/<pid>.json` (presence = live session;
  per-session metadata by PID).
- **Live read**: poll `~/.claude/projects/*.jsonl` (Claude Code's transcript: turns, tool
  calls, results, user/assistant direction); merge into the mapped chat channel.
- **"Working now" signal**: install two hooks (`post-tool-use.py`, `stop.py`) into
  `~/.claude/command-center/hooks/` (taOS namespace) + merge into `~/.claude/settings.json`;
  hooks write recency sidecars under a live-state dir. Distinguishes idle-awaiting-input from
  actively-executing (drives presence/typing indicators).
- **Drive/reply**: headless `claude -p --input-format stream-json`, or continue a stopped
  session via `claude --resume <session-id>`.

**Honest constraint (CCC hits it too):** there is NO injection into an already-running
INTERACTIVE foreground session. You can READ a live session and you can DRIVE dormant/resumed
sessions headlessly, but you cannot push a taOS reply into the same live foreground process.
Consequence:
- Live-READ mirror of existing sessions = fully achievable, low risk -> **this is slice 1**.
- Reply-FROM-taOS = taOS becomes the session driver (resume / stream-json). Works, but that
  session is then taOS-driven, not co-driven with a terminal you are also typing in.

**Per-harness attach matrix (research needed for kilo/grok/opencode):** each needs (a) how to
discover a running session, (b) transcript/state location for live read, (c) an events/hook
signal if available, (d) how to resume-drive it. Claude Code's is the CCC recipe above;
kilo/grok/opencode equivalents are a slice-4 research item.

**Revised slices:**
1. **Live-read attach (Claude Code).** Discover + tail transcript + hooks -> this session shows
   live in taOStalk (read-only mirror). Flagship "my session in taOS" moment.
2. **Content-block renderer registry** (unchanged).
3. **Reply/drive via resume** (Claude Code) + interactive questions synced (chat+Decisions).
4. **Per-harness attach + drive: kilo, grok, opencode** (research each harness's transcript +
   resume mechanism; ACP-spawn as the secondary "new session" path).
5. **Parity finish + taOStalk surface** (unchanged).

## Transport tiers + graceful degradation (network is the baseline)

Correction to both the ACP-spawn and CCC-attach framings: neither is universal. Filesystem
attach needs a locally readable home dir (same host + account); ACP-spawn needs taOS to launch
the process locally. Remote external agents (off-LAN, other machine), sessions under a
different OS account, and containerized agents have NEITHER. So the bridge is TIERED, with the
NETWORK path as the always-on baseline and filesystem/ACP as local enhancements. The bridge
probes what is available per session and degrades gracefully; communication never stops.

**Tier 0 - network push + poll (universal, always works).**
The agent (or a thin agent-side shim) posts its turns/status/questions to its taOS channel over
the API - the A2A bus (`POST /a2a/send`) or the chat message API - using its registry
identity/token. It reads operator replies back from the channel: subscribe live (SSE/WS) when it
can reach taOS in real time, else POLL the A2A + chat frequently. Works for ANY agent that can
reach taOS over the network: remote, off-LAN via taOSgo/Headscale, different account,
container. This is already how `@taOS-dev` (running off-host) talks to taOS today - push to the
bus, poll on the sweep - so it is proven. **This tier is the flagship "keep comms moving"
layer, not the CCC attach.**

**Tier 1 - local real-time enhancement (best case).**
When the session home dir IS locally readable (same host + account) or ACP can attach/spawn,
layer richer capture on top of Tier 0: full transcript (CCC recipe), tool-call detail, live
token streaming, the CCC hooks for the working-now signal.

**Fallback logic (per session):** probe - is the home dir readable? can ACP attach/spawn? is a
live SSE/WS to the agent reachable? Pick the best tier; degrade to Tier 0 push+poll if none.
Surface the current tier in the channel (live-stream vs polled) so the operator knows the
freshness.

**Consequences:**
- Tier 0 reuses existing infra: A2A bus, chat API, the poll sweeps. Little net-new for the
  baseline; the value-add is richer rendering + the Tier 1 enhancements.
- The reply direction is Tier-0-native: operator replies land in the channel; the agent reads
  them by subscribe-or-poll. This sidesteps the "cannot inject into a live foreground process"
  constraint for remote agents entirely (they were never going to be filesystem-attached).
- Off-LAN reach for remote agents rides taOSgo/Headscale (see the taOSgo work); on-LAN and
  same-host are the easy cases.

**Revised slice 1:** the flagship is Tier 0 (network push + poll) proving an existing REMOTE
session (e.g. `@taOS-dev` on this Mac) shows live in taOStalk and can be replied to from taOS
via the channel. The CCC local filesystem attach (Tier 1) becomes a same-host enhancement in a
later slice, not the foundation.

## Onboarding (acceptance requirement)

Because the transport degrades across tiers and differs per harness, the onboarding flow is
part of the deliverable, not a doc afterthought. A "Connect a session" wizard in taOStalk must
walk the operator through, and clearly explain:

- **Scenario detection first:** is the agent LOCAL to taOS (same host + account), REMOTE
  (other machine / off-LAN / container), or under a DIFFERENT account? The wizard picks the
  transport tier from this and states which capabilities apply.
- **Tier 0 (any agent, incl. remote):** how the agent connects over the network - it posts to
  its taOS channel with its registry identity/token (mint/consent flow), reads replies by
  subscribe-or-poll. Copy-paste connect snippet per harness. Off-LAN requires taOSgo/Headscale
  (link to that setup); on-LAN/same-host is direct.
- **Tier 1 (local same-host only):** the optional richer attach - point taOS at the harness
  transcript/state (Claude Code: `~/.claude`), install the hooks, or ACP attach/spawn. Explain
  it is a same-host enhancement, unavailable remote/cross-account.
- **Per-harness specifics:** the exact connect command / ACP entry (`claude ...`, `kilo acp`,
  `grok --acp`, `opencode acp`) and auth. For Claude Code, explain the subscription-vs-API-key
  choice AND the Anthropic ToS caveat plainly: subscription (CLI-wrap) is personal-individual
  use only; shared/multi-user/commercial taOS must use an API key or a non-Claude harness.
- **Capability expectations set honestly:** live READ works everywhere; REPLY-from-taOS works
  via the channel (Tier 0) or resume-drive (Tier 1); you cannot inject into a live foreground
  terminal process. The wizard states what this specific session can and cannot do.
- **Freshness indicator:** the channel shows the active tier (live-stream vs polled) so the
  operator always knows how real-time the view is.

Acceptance: a non-expert operator can connect each of a local Claude Code session, a remote
kilo/grok/opencode session, and a cross-account session, guided entirely by the wizard, and
understands what each can do.

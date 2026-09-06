# Chat Phase 3 — typing-phase labels

**Status:** Approved 2026-04-20.

## Goal

Replace the generic "tom is thinking…" indicator with phase-aware labels so users can see what an agent is actually doing — "tom is using web_search", "don is writing payment.py", "tom is planning…".

## Non-goals

- Per-token streaming of the phase text.
- Persistent phase log / trace viewer (trace app is separate).
- Rich phase timelines in the channel.

## Decisions

1. **Structured payload:** `{phase: enum, detail?: string}`. Enum for consistent UI (icons, colour), detail for framework-specific specificity.
2. **Phase enum:** `thinking | tool | reading | writing | searching | planning`.
3. **Backwards compatible:** bridges that omit `phase` continue to send binary heartbeats, UI renders "thinking" as before.
4. **Rollout:** all 6 bridges + backend + UI in one PR.

## Architecture

### Payload

```typescript
type TypingPhase = "thinking" | "tool" | "reading" | "writing" | "searching" | "planning";

interface ThinkingBody {
  slug: string;
  state: "start" | "end";
  phase?: TypingPhase;   // optional; default "thinking" on start
  detail?: string;       // optional; free-form, truncated by UI to 40 chars
}
```

### Backend

- `POST /api/chat/channels/{id}/thinking` body now accepts `phase` + `detail`. Validates `phase` against enum if present; rejects with 400 if invalid.
- `TypingRegistry.mark(channel_id, slug, kind, phase?, detail?)` stores `{kind, phase, detail, ts}` per (channel, slug).
- `TypingRegistry.list(channel_id)` returns agents with `{slug, phase, detail}` fields.
- `hub.broadcast` for `"thinking"` event includes `phase` and `detail`.

### Frontend

- Extend `TypingFooter` (`desktop/src/apps/chat/TypingFooter.tsx`) to accept an `agents` array shape `{slug, phase?, detail?}[]`.
- Phase label map:
  | phase | label template | icon |
  |---|---|---|
  | `thinking` | `thinking` | 💭 |
  | `tool` | `using ${detail}` (fallback: `using a tool`) | 🔧 |
  | `reading` | `reading ${detail}` (fallback: `reading`) | 📖 |
  | `writing` | `writing ${detail}` (fallback: `writing`) | ✏️ |
  | `searching` | `searching ${detail}` (fallback: `searching`) | 🔍 |
  | `planning` | `planning` | 📋 |
- `detail` strings truncated to 40 chars with ellipsis.
- Unknown `phase` values fall back to "thinking".

### Bridge install scripts (6 files)

Per framework, emit a phase heartbeat immediately before the corresponding action. Helper `_thinking(client, channel_id, state, phase=None, detail=None)` already exists as a shared shape from Phase 2a — extend to accept the new args.

- **OpenClaw** (`install_openclaw.sh` — if present) — map tool calls to `tool`, file reads to `reading`, file writes to `writing`.
- **Hermes** (`install_hermes.sh`) — single completion. Emit `thinking` only.
- **SmolAgents** (`install_smolagents.sh`) — use step callbacks to emit `tool` (tool calls) + `writing` (CodeAgent code generation).
- **Langroid** (`install_langroid.sh`) — emit `tool` on tool calls.
- **PocketFlow** (`install_pocketflow.sh`) — emit `tool` with detail `"node: <name>"` on each graph node enter.
- **OpenAI Agents SDK** (`install_openai_agents_sdk.sh` + `install_openai-agents-sdk.sh`) — emit `tool` when SDK dispatches a tool.

Bridges without framework-exposed phases (Hermes) remain "thinking"-only; that's fine per the enum default.

## Testing

**Backend unit tests** (`tests/test_typing_registry.py`, extend):
- `mark` with phase/detail stores and `list` returns them.
- `mark` without phase defaults to `"thinking"`.
- Two marks for same slug overwrite phase (last-writer-wins).

**Route tests** (`tests/test_routes_typing.py` extend or create):
- `POST /thinking` with valid phase 200s; invalid phase 400s.
- Payload in the broadcast event includes phase and detail.

**Frontend unit tests** (`TypingFooter.test.tsx` extend):
- Renders correct icon + label per phase.
- Truncates detail >40 chars.
- Falls back to "thinking" for unknown phase.

**E2E** (env-gated): post a `thinking` heartbeat with phase via HTTP, assert WS event shape.

## Out of scope / later

- Richer UI for phases (inline per-message thinking visualizer).
- Persistent per-session phase timelines.
- OpenClaw streaming integration — may need upstream changes to its SSE output.

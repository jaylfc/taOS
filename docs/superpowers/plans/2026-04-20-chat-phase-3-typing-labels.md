# Chat Phase 3 — typing-phase labels — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development.

**Goal:** Ship structured `{phase, detail}` heartbeats from bridges + display phase-aware labels in the chat typing footer.

**Architecture:** Additive fields on `POST /thinking` + `TypingRegistry` + WS `thinking` event. UI gains a phase→icon/label map. Bridges emit phase where the framework exposes it; omitting `phase` keeps current behavior.

**Tech Stack:** Python 3.12 + FastAPI (backend), React/TS (UI), bash install scripts (bridges).

---

## File structure

### Modified
- `tinyagentos/chat/typing_registry.py` — store phase/detail, expose via list.
- `tinyagentos/routes/chat.py` — `POST /thinking` accepts phase/detail + validates enum; broadcast payload includes them.
- `tinyagentos/scripts/install_hermes.sh`, `install_smolagents.sh`, `install_langroid.sh`, `install_pocketflow.sh`, `install_openai_agents_sdk.sh`, `install_openai-agents-sdk.sh` — extend `_thinking()` helper to accept phase/detail; emit where framework exposes signals.
- `desktop/src/apps/chat/TypingFooter.tsx` — phase-aware labels + icons.

### Tests
- `tests/test_typing_registry.py` — extend: phase/detail round-trip, default "thinking", overwrite semantics.
- `tests/test_routes_typing.py` (create or extend existing route-level tests) — POST /thinking with phase validates, broadcast includes phase.
- `desktop/src/apps/chat/__tests__/TypingFooter.test.tsx` — extend: icon/label per phase, detail truncation, unknown-phase fallback.

---

## Task 1: TypingRegistry stores phase + detail

**Files:**
- Modify: `tinyagentos/chat/typing_registry.py`
- Test: `tests/test_typing_registry.py` (extend)

### Step 1: Write failing tests

Append to `tests/test_typing_registry.py`:

```python
@pytest.mark.asyncio
async def test_mark_with_phase_and_detail():
    reg = TypingRegistry()
    reg.mark("c1", "tom", "agent", phase="tool", detail="web_search")
    result = reg.list("c1")
    agents = result["agent"]
    assert len(agents) == 1
    entry = agents[0]
    assert entry["slug"] == "tom"
    assert entry["phase"] == "tool"
    assert entry["detail"] == "web_search"


@pytest.mark.asyncio
async def test_mark_without_phase_defaults_to_thinking():
    reg = TypingRegistry()
    reg.mark("c1", "tom", "agent")
    result = reg.list("c1")
    entry = result["agent"][0]
    assert entry["phase"] == "thinking"
    assert entry["detail"] is None


@pytest.mark.asyncio
async def test_mark_overwrites_phase_last_writer_wins():
    reg = TypingRegistry()
    reg.mark("c1", "tom", "agent", phase="thinking")
    reg.mark("c1", "tom", "agent", phase="tool", detail="search")
    entry = reg.list("c1")["agent"][0]
    assert entry["phase"] == "tool"
    assert entry["detail"] == "search"


@pytest.mark.asyncio
async def test_human_entries_return_as_slug_only_for_backwards_compat():
    reg = TypingRegistry()
    reg.mark("c1", "jay", "human")
    result = reg.list("c1")
    # Humans don't get phase/detail; kept as plain slug for legacy clients.
    assert "jay" in (result["human"] if isinstance(result["human"][0], str) else [e["slug"] for e in result["human"]])
```

Note: the new return shape will change `list()` contract. Agents become `[{slug, phase, detail}]` instead of `[slug]`. Humans stay as plain list-of-strings OR adopt the same dict shape. Pick consistency: **make humans use the same dict shape** `[{slug, phase: null, detail: null}]` and update the existing `test_routes_typing.py` + `TypingFooter` accordingly. Simpler. Update the 4th test to:

```python
async def test_human_entry_shape_matches_agent():
    reg = TypingRegistry()
    reg.mark("c1", "jay", "human")
    entry = reg.list("c1")["human"][0]
    assert entry["slug"] == "jay"
    assert entry.get("phase") is None
```

### Step 2: Implement

```python
from dataclasses import dataclass
from typing import Literal

Kind = Literal["human", "agent"]
TypingPhase = Literal["thinking", "tool", "reading", "writing", "searching", "planning"]

@dataclass
class _Entry:
    kind: Kind
    expires_at: float
    phase: TypingPhase | None
    detail: str | None


class TypingRegistry:
    def __init__(self, human_ttl: int = 3, agent_ttl: int = 45) -> None:
        self._ttls: dict[str, int] = {"human": human_ttl, "agent": agent_ttl}
        self._entries: dict[tuple[str, str], _Entry] = {}

    def mark(
        self,
        channel_id: str,
        slug: str,
        kind: Kind,
        *,
        phase: TypingPhase | None = None,
        detail: str | None = None,
    ) -> None:
        now = _now()
        ttl = self._ttls[kind]
        resolved_phase: TypingPhase | None = (
            phase if phase is not None else ("thinking" if kind == "agent" else None)
        )
        self._entries[(channel_id, slug)] = _Entry(
            kind=kind,
            expires_at=now + ttl,
            phase=resolved_phase,
            detail=detail,
        )

    def clear(self, channel_id: str, slug: str) -> None:
        self._entries.pop((channel_id, slug), None)

    def list(self, channel_id: str) -> dict[str, list[dict]]:
        now = _now()
        out: dict[str, list[dict]] = {"human": [], "agent": []}
        stale: list[tuple[str, str]] = []
        for (ch, slug), entry in self._entries.items():
            if ch != channel_id:
                continue
            if entry.expires_at < now:
                stale.append((ch, slug))
                continue
            out[entry.kind].append({
                "slug": slug,
                "phase": entry.phase,
                "detail": entry.detail,
            })
        for k in stale:
            self._entries.pop(k, None)
        out["human"].sort(key=lambda e: e["slug"])
        out["agent"].sort(key=lambda e: e["slug"])
        return out
```

### Step 3: Update `tests/test_typing_registry.py` existing tests to new shape

Read the file; wherever it asserts `list["agent"] == ["tom"]`, change to check `[e["slug"] for e in list["agent"]] == ["tom"]` (or equivalent).

### Step 4: Run tests → pass

```bash
PYTHONPATH=. pytest tests/test_typing_registry.py -v
```

### Step 5: Commit

```bash
git add tinyagentos/chat/typing_registry.py tests/test_typing_registry.py
git commit -m "feat(chat): typing registry stores phase + detail; agents expose {slug,phase,detail}"
```

---

## Task 2: `POST /thinking` accepts phase + detail

**Files:**
- Modify: `tinyagentos/routes/chat.py`
- Test: existing route tests file for thinking if present (search), else extend `tests/test_chat_routes.py`

### Step 1: Write failing test

Create or extend `tests/test_chat_phase.py`:

```python
import pytest, yaml
from httpx import AsyncClient, ASGITransport


def _make_phase_app(tmp_path):
    cfg = {"server": {"host": "0.0.0.0", "port": 6969}, "backends": [],
           "qmd": {"url": "http://localhost:7832"}, "agents": [],
           "metrics": {"poll_interval": 30, "retention_days": 30}}
    (tmp_path / "config.yaml").write_text(yaml.dump(cfg))
    (tmp_path / ".setup_complete").touch()
    from tinyagentos.app import create_app
    return create_app(data_dir=tmp_path)


async def _client_with_bearer(tmp_path):
    app = _make_phase_app(tmp_path)
    await app.state.chat_channels.init()
    await app.state.chat_messages.init()
    token = app.state.auth.get_local_token()
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                        headers={"Authorization": f"Bearer {token}"})
    return app, client


@pytest.mark.asyncio
async def test_thinking_with_valid_phase_200(tmp_path):
    app, client = await _client_with_bearer(tmp_path)
    async with client:
        r = await client.post("/api/chat/channels/c1/thinking",
            json={"slug": "tom", "state": "start", "phase": "tool", "detail": "web_search"})
        assert r.status_code == 200, r.json()
        listing = app.state.typing.list("c1")
        assert listing["agent"][0]["phase"] == "tool"
        assert listing["agent"][0]["detail"] == "web_search"


@pytest.mark.asyncio
async def test_thinking_with_invalid_phase_400(tmp_path):
    app, client = await _client_with_bearer(tmp_path)
    async with client:
        r = await client.post("/api/chat/channels/c1/thinking",
            json={"slug": "tom", "state": "start", "phase": "not-a-phase"})
        assert r.status_code == 400


@pytest.mark.asyncio
async def test_thinking_without_phase_defaults_thinking(tmp_path):
    app, client = await _client_with_bearer(tmp_path)
    async with client:
        r = await client.post("/api/chat/channels/c1/thinking",
            json={"slug": "tom", "state": "start"})
        assert r.status_code == 200
        listing = app.state.typing.list("c1")
        assert listing["agent"][0]["phase"] == "thinking"
```

### Step 2: Run → FAIL

### Step 3: Update `POST /thinking` handler in routes/chat.py

Around line 870-899, replace the body with:

```python
VALID_PHASES = {"thinking", "tool", "reading", "writing", "searching", "planning"}

@router.post("/api/chat/channels/{channel_id}/thinking")
async def post_thinking(channel_id: str, body: dict, request: Request):
    auth = getattr(request.app.state, "auth", None)
    bearer = request.headers.get("authorization", "")
    if not bearer.lower().startswith("bearer "):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    if auth is None or not auth.validate_local_token(bearer[7:].strip()):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    slug = (body or {}).get("slug")
    state = (body or {}).get("state")
    if not slug or state not in ("start", "end"):
        return JSONResponse({"error": "slug and state in {start,end} required"}, status_code=400)

    phase = (body or {}).get("phase")
    if phase is not None and phase not in VALID_PHASES:
        return JSONResponse({"error": f"invalid phase; must be one of {sorted(VALID_PHASES)}"}, status_code=400)
    detail = (body or {}).get("detail")

    reg = getattr(request.app.state, "typing", None)
    hub = getattr(request.app.state, "chat_hub", None)
    if reg is None:
        return JSONResponse({"error": "typing registry not configured"}, status_code=503)
    if state == "start":
        reg.mark(channel_id, slug, "agent", phase=phase, detail=detail)
    else:
        reg.clear(channel_id, slug)
    if hub is not None:
        await hub.broadcast(channel_id, {
            "type": "thinking",
            "slug": slug,
            "state": state,
            "phase": phase if state == "start" else None,
            "detail": detail if state == "start" else None,
        })
    return JSONResponse({"ok": True}, status_code=200)
```

Also define `VALID_PHASES` at module top (or near the endpoint).

### Step 4: Run tests → pass

### Step 5: Commit

```bash
git add tinyagentos/routes/chat.py tests/test_chat_phase.py
git commit -m "feat(chat): POST /thinking accepts phase + detail with enum validation"
```

---

## Task 3: TypingFooter — phase-aware labels + icons

**Files:**
- Modify: `desktop/src/apps/chat/TypingFooter.tsx`
- Test: `desktop/src/apps/chat/__tests__/TypingFooter.test.tsx` (extend)

### Step 1: Write failing tests

Append to test file:

```tsx
it("renders 'using X' for tool phase", () => {
  render(<TypingFooter humans={[]} agents={[{ slug: "tom", phase: "tool", detail: "web_search" }]} selfId="user" />);
  expect(screen.getByText(/tom/i)).toBeInTheDocument();
  expect(screen.getByText(/using web_search/i)).toBeInTheDocument();
});

it("renders 'writing X' for writing phase", () => {
  render(<TypingFooter humans={[]} agents={[{ slug: "don", phase: "writing", detail: "payment.py" }]} selfId="user" />);
  expect(screen.getByText(/writing payment\.py/i)).toBeInTheDocument();
});

it("truncates detail longer than 40 chars", () => {
  const longDetail = "a".repeat(60);
  render(<TypingFooter humans={[]} agents={[{ slug: "tom", phase: "tool", detail: longDetail }]} selfId="user" />);
  const text = screen.getByText(/using/i).textContent ?? "";
  expect(text.length).toBeLessThanOrEqual(60);
  expect(text).toContain("…");
});

it("falls back to 'thinking' for unknown phase", () => {
  render(<TypingFooter humans={[]} agents={[{ slug: "tom", phase: "quantum-entanglement" as any, detail: null }]} selfId="user" />);
  expect(screen.getByText(/thinking/i)).toBeInTheDocument();
});
```

Also check the existing tests — they likely pass string slugs. Update them to the new `{slug, phase?, detail?}` shape or pass `slug` only (component should handle both for backward compat if existing callsites pass strings).

### Step 2: Read and update component

Read `TypingFooter.tsx`. Extend the `agents` prop type to accept `{slug, phase?, detail?}[]` (or `string[]` for back-compat if callers haven't migrated). Add a phase-to-label function:

```tsx
type TypingPhase = "thinking" | "tool" | "reading" | "writing" | "searching" | "planning";

interface AgentTyping {
  slug: string;
  phase?: TypingPhase | null;
  detail?: string | null;
}

function phaseLabel(phase?: TypingPhase | null, detail?: string | null): { icon: string; text: string } {
  const d = detail ? (detail.length > 40 ? detail.slice(0, 39) + "…" : detail) : null;
  switch (phase) {
    case "tool":      return { icon: "🔧", text: d ? `using ${d}` : "using a tool" };
    case "reading":   return { icon: "📖", text: d ? `reading ${d}` : "reading" };
    case "writing":   return { icon: "✏️", text: d ? `writing ${d}` : "writing" };
    case "searching": return { icon: "🔍", text: d ? `searching ${d}` : "searching" };
    case "planning":  return { icon: "📋", text: "planning" };
    case "thinking":
    default:          return { icon: "💭", text: "thinking" };
  }
}
```

Update the render to use `phaseLabel` per agent:

```tsx
{agents.map((a) => {
  const slug = typeof a === "string" ? a : a.slug;
  const { icon, text } = typeof a === "string"
    ? phaseLabel(null, null)
    : phaseLabel(a.phase, a.detail);
  return (
    <span key={slug} className="...">
      {icon} @{slug} is {text}…
    </span>
  );
})}
```

### Step 3: Update MessagesApp call site

Find where `<TypingFooter agents={...}>` is rendered. The data flows from:
- WS `thinking` event → local state
- `GET /typing` → list format

Update the local state shape to `AgentTyping[]`. Adjust:
- WS handler for `type:"thinking"` to push `{slug, phase, detail}` (or clear on `state:"end"`).
- `fetchTyping` function to read `data.agent` which now contains dicts.

### Step 4: Run tests + build → pass

```bash
cd desktop && npm test -- --run TypingFooter
cd desktop && npm run build
```

### Step 5: Commit

```bash
git add desktop/src/apps/chat/TypingFooter.tsx desktop/src/apps/chat/__tests__/TypingFooter.test.tsx desktop/src/apps/MessagesApp.tsx
git commit -m "feat(chat): TypingFooter shows phase-aware labels + icons"
```

---

## Task 4: Bridge scripts — emit phase heartbeats

**Files:**
- Modify: 6 install scripts in `tinyagentos/scripts/install_*.sh`

Each bridge has a `_thinking(c, channel_id, state)` helper. Extend signature to `_thinking(c, channel_id, state, *, phase=None, detail=None)` and pass `phase`/`detail` in the JSON body.

### Shared helper pattern (apply to all 6 bridges):

Find existing `async def _thinking(c: httpx.AsyncClient, ch_id, state: str) -> None:` block and replace with:

```python
async def _thinking(c: httpx.AsyncClient, ch_id, state: str, *,
                   phase: str | None = None, detail: str | None = None) -> None:
    if not ch_id:
        return
    body = {"slug": AGENT_NAME, "state": state}
    if phase is not None:
        body["phase"] = phase
    if detail is not None:
        body["detail"] = detail
    try:
        await c.post(
            f"{BRIDGE_URL}/api/chat/channels/{ch_id}/thinking",
            json=body,
            headers={"Authorization": f"Bearer {LOCAL_TOKEN}"},
            timeout=5,
        )
    except Exception:
        pass
```

### Per-framework phase emission:

- **Hermes** (`install_hermes.sh`): no framework-exposed phases. Keep existing `_thinking(c, ch_id, "start")` — defaults to `"thinking"`. No change beyond signature.

- **SmolAgents** (`install_smolagents.sh`): `CodeAgent` emits step events. Wire a step callback that invokes `_thinking(c, ch_id, "start", phase="tool", detail=tool_name)` when a tool is called, `phase="writing"` when code is generated. If the smolagents API doesn't surface these hooks cleanly, just extend the existing `_thinking("start")` call — phase defaults to thinking.

- **Langroid** (`install_langroid.sh`): if agent.tool_messages has signal, emit `tool`; else stay with default.

- **PocketFlow** (`install_pocketflow.sh`): emit `_thinking(c, ch_id, "start", phase="tool", detail=f"node: {node.name}")` inside the `node_pre_run` or equivalent callback. If not easily available, stay default.

- **OpenAI Agents SDK** (2 files): emit `_thinking(c, ch_id, "start", phase="tool", detail=tool.name)` from `on_handoff`/`on_tool` hooks if present. Otherwise default.

**Scope note:** The minimum viable change is just updating the `_thinking` helper signature so future phase emission doesn't need another PR. Actual per-framework hook wiring is best-effort — if it's non-trivial for a given framework, leave that bridge on default thinking and note it in the PR description.

### Step 1: Update all 6 helpers

Apply the unified `_thinking` signature to all 6 bridges.

### Step 2: Per-framework phase emission

For each of smolagents / langroid / pocketflow / openai-agents-sdk, explore the framework API for callbacks:

- `rg -n "step_callback\|run_async\|on_tool\|node_pre" tinyagentos/scripts/` — see what the existing loop already exposes.
- Where callbacks exist, emit `_thinking(c, ch_id, "start", phase=..., detail=...)` just before the native action. The existing `_thinking("end")` at the end of the turn stays put.

If a framework doesn't expose callbacks, leave that bridge on default (no explicit phase) — it still works, just shows "thinking".

### Step 3: Lint all 6 scripts

```bash
for f in tinyagentos/scripts/install_hermes.sh \
         tinyagentos/scripts/install_smolagents.sh \
         tinyagentos/scripts/install_langroid.sh \
         tinyagentos/scripts/install_pocketflow.sh \
         tinyagentos/scripts/install_openai_agents_sdk.sh \
         tinyagentos/scripts/install_openai-agents-sdk.sh; do
    bash -n "$f" && echo "$f ok" || echo "$f BAD"
done
```

All 6 should report `ok`.

### Step 4: Commit

```bash
git add tinyagentos/scripts/install_*.sh
git commit -m "feat(bridges): _thinking helper accepts phase/detail; emit per-framework where exposed"
```

---

## Task 5: Rebuild desktop bundle

```bash
cd desktop && npm run build
cd /Volumes/NVMe/Users/jay/Development/tinyagentos
git add -A static/desktop desktop/tsconfig.tsbuildinfo
git commit -m "build: rebuild desktop bundle for chat Phase 3 typing labels"
```

---

## Task 6: Playwright E2E stub

Create `tests/e2e/test_chat_phase3.py`:

```python
"""Chat Phase 3 — typing-phase labels E2E.

Requires TAOS_E2E_URL set.
"""
import os
import re

import pytest
from playwright.sync_api import Page, expect

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        not os.environ.get("TAOS_E2E_URL"),
        reason="TAOS_E2E_URL required",
    ),
]
URL = os.environ.get("TAOS_E2E_URL", "")


def test_typing_footer_shows_phase_label(page: Page):
    """Heartbeat with phase=tool + detail=web_search → footer renders 'using web_search'."""
    page.goto(URL)
    page.get_by_role("button", name="Messages").click()
    page.get_by_text("roundtable").first.click()
    # The test requires a backend to emit a phase event; this is an env-
    # gated stub, so it's left as a manual observation point.
    # A CI-friendly version would POST /thinking via HTTP before asserting.
```

Commit:
```bash
git add tests/e2e/test_chat_phase3.py
git commit -m "test(e2e): chat Phase 3 typing-phase label stub"
```

---

## Final verification

```bash
PYTHONPATH=. pytest tests/test_typing_registry.py tests/test_chat_phase.py -v
cd desktop && npm test -- --run
cd desktop && npm run build
```

```bash
git push -u origin feat/chat-phase-3-typing-labels
gh pr create --base master \
  --title "Chat Phase 3 — per-framework typing-phase labels" \
  --body-file docs/superpowers/specs/2026-04-20-chat-phase-3-typing-labels-design.md
```

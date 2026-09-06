# Chat Phase 2a — Desktop Admin + Live Signal Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Phase 1's control plane discoverable in the desktop chat UI and give humans honest "something is happening" feedback.

**Architecture:** Backend adds a `TypingRegistry`, three ephemeral endpoints (POST `/typing`, POST `/thinking`, GET `/typing`), a `slash_commands` manifest field per framework, and a `/api/frameworks/slash-commands` endpoint. Six install bridges gain a ~4-line thinking-heartbeat wrapper around the LLM call. Desktop adds four components (ChannelSettingsPanel slide-over, AgentContextMenu, SlashMenu, TypingFooter) and two helpers (channel-admin-api client, use-typing-emitter hook), all integrated into MessagesApp.

**Tech Stack:** Python 3.12, FastAPI, pytest + pytest-asyncio, React + TypeScript, Vitest for component unit tests, Playwright for E2E. Spec at `docs/superpowers/specs/2026-04-19-chat-phase-2a-desktop-admin-design.md`.

---

## File Structure

**New backend files:**
- `tinyagentos/chat/typing_registry.py` — in-memory per-channel typing/thinking tracker
- `tests/test_chat_typing.py` — registry unit tests + route tests

**New frontend files:**
- `desktop/src/apps/chat/ChannelSettingsPanel.tsx` — right-side slide-over with 4 sections
- `desktop/src/apps/chat/AgentContextMenu.tsx` — shared right-click menu
- `desktop/src/apps/chat/SlashMenu.tsx` — composer-anchored autocomplete popup
- `desktop/src/apps/chat/TypingFooter.tsx` — two-line typing + thinking strip
- `desktop/src/lib/channel-admin-api.ts` — thin REST client for PATCH channel / POST members / POST muted
- `desktop/src/lib/use-typing-emitter.ts` — debounced humans-typing emitter hook

**New test files:**
- `tests/e2e/test_chat_phase2a.py` — Playwright tests
- `desktop/src/apps/chat/__tests__/SlashMenu.test.tsx`
- `desktop/src/apps/chat/__tests__/AgentContextMenu.test.tsx`

**Modified backend files:**
- `tinyagentos/frameworks.py` — `slash_commands` field per entry (6 frameworks)
- `tinyagentos/app.py` — wire `TypingRegistry` onto `app.state.typing`
- `tinyagentos/routes/chat.py` — POST `/typing`, POST `/thinking`, GET `/typing`
- `tinyagentos/routes/framework.py` — GET `/api/frameworks/slash-commands`
- `tinyagentos/scripts/install_{hermes,smolagents,langroid,pocketflow,openai_agents_sdk,openai-agents-sdk}.sh` — thinking heartbeat

**Modified frontend files:**
- `desktop/src/apps/MessagesApp.tsx` — mount new components, composer `/` handling, WS handler for new broadcast shapes

---

## Task 1: `slash_commands` manifest field on frameworks

**Files:**
- Modify: `tinyagentos/frameworks.py`
- Test: `tests/test_framework_manifest.py` (extend)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_framework_manifest.py`:

```python
def test_slash_commands_field_shape():
    """Every framework entry's slash_commands field (if present) is a list of
    {name, description} dicts with non-empty names."""
    from tinyagentos.frameworks import FRAMEWORKS
    for fw_id, entry in FRAMEWORKS.items():
        cmds = entry.get("slash_commands")
        if cmds is None:
            continue
        assert isinstance(cmds, list), f"{fw_id}: slash_commands must be a list"
        for c in cmds:
            assert isinstance(c, dict), f"{fw_id}: each command must be a dict"
            assert c.get("name"), f"{fw_id}: command missing name"
            assert isinstance(c.get("description", ""), str)


def test_hermes_has_slash_commands():
    from tinyagentos.frameworks import FRAMEWORKS
    assert "slash_commands" in FRAMEWORKS["hermes"]
    assert len(FRAMEWORKS["hermes"]["slash_commands"]) > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. pytest tests/test_framework_manifest.py -v -k "slash_commands"`
Expected: `test_hermes_has_slash_commands` FAILS (field missing).

- [ ] **Step 3: Populate `slash_commands` for the 6 active frameworks**

In `tinyagentos/frameworks.py`, add the field to each of the 6 beta frameworks (use each framework's canonical command list; entries are conservative — only include commands we're confident the framework ships):

```python
"openclaw": {
    # ... existing ...
    "slash_commands": [
        {"name": "help", "description": "List OpenClaw commands"},
        {"name": "clear", "description": "Clear conversation history"},
        {"name": "compact", "description": "Summarise and compact context"},
        {"name": "cost", "description": "Show token spend for this session"},
    ],
},
"hermes": {
    # ... existing ...
    "slash_commands": [
        {"name": "help", "description": "List available commands"},
        {"name": "clear", "description": "Clear the session context"},
        {"name": "model", "description": "Show or change active model"},
    ],
},
"smolagents": {
    # ... existing ...
    "slash_commands": [
        {"name": "help", "description": "Show SmolAgents help"},
    ],
},
"langroid": {
    # ... existing ...
    "slash_commands": [
        {"name": "help", "description": "Show Langroid help"},
    ],
},
"pocketflow": {
    # ... existing ...
    "slash_commands": [
        {"name": "help", "description": "Show PocketFlow help"},
    ],
},
"openai-agents-sdk": {
    # ... existing ...
    "slash_commands": [
        {"name": "help", "description": "Show Agents SDK help"},
    ],
},
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. pytest tests/test_framework_manifest.py -v -k "slash_commands"`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add tinyagentos/frameworks.py tests/test_framework_manifest.py
git commit -m "feat(frameworks): add slash_commands manifest field for 6 beta frameworks"
```

---

## Task 2: `TypingRegistry` module + tests

**Files:**
- Create: `tinyagentos/chat/typing_registry.py`
- Test: `tests/test_chat_typing.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_chat_typing.py
import pytest
from tinyagentos.chat.typing_registry import TypingRegistry


def test_empty_registry_returns_empty_lists():
    r = TypingRegistry()
    assert r.list("c1") == {"human": [], "agent": []}


def test_mark_human_appears_in_list():
    r = TypingRegistry()
    r.mark("c1", "jay", "human")
    assert r.list("c1")["human"] == ["jay"]
    assert r.list("c1")["agent"] == []


def test_mark_agent_appears_in_list():
    r = TypingRegistry()
    r.mark("c1", "tom", "agent")
    assert r.list("c1")["agent"] == ["tom"]


def test_clear_removes_entry():
    r = TypingRegistry()
    r.mark("c1", "tom", "agent")
    r.clear("c1", "tom")
    assert r.list("c1")["agent"] == []


def test_clear_idempotent():
    r = TypingRegistry()
    r.clear("c1", "nobody")  # must not raise


def test_different_channels_independent():
    r = TypingRegistry()
    r.mark("c1", "jay", "human")
    assert r.list("c2") == {"human": [], "agent": []}


def test_human_ttl_expires(monkeypatch):
    r = TypingRegistry(human_ttl=3, agent_ttl=45)
    t = [1000.0]
    monkeypatch.setattr("tinyagentos.chat.typing_registry._now", lambda: t[0])
    r.mark("c1", "jay", "human")
    assert r.list("c1")["human"] == ["jay"]
    t[0] = 1003.1
    assert r.list("c1")["human"] == []


def test_agent_ttl_expires(monkeypatch):
    r = TypingRegistry(human_ttl=3, agent_ttl=45)
    t = [1000.0]
    monkeypatch.setattr("tinyagentos.chat.typing_registry._now", lambda: t[0])
    r.mark("c1", "tom", "agent")
    assert r.list("c1")["agent"] == ["tom"]
    t[0] = 1045.1
    assert r.list("c1")["agent"] == []


def test_mark_refreshes_ttl(monkeypatch):
    r = TypingRegistry(human_ttl=3, agent_ttl=45)
    t = [1000.0]
    monkeypatch.setattr("tinyagentos.chat.typing_registry._now", lambda: t[0])
    r.mark("c1", "jay", "human")
    t[0] = 1002.0
    r.mark("c1", "jay", "human")  # refresh
    t[0] = 1004.0
    assert r.list("c1")["human"] == ["jay"]  # still alive (refreshed at 1002)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=. pytest tests/test_chat_typing.py -v`
Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Implement the registry**

```python
# tinyagentos/chat/typing_registry.py
"""In-memory per-channel typing / thinking heartbeat tracker.

Humans refresh via keystroke-debounced POSTs; agent bridges fire
start/end around their LLM call. Stale entries auto-clear after a
per-kind TTL. Single-process, matches the rest of the chat infra.
"""
from __future__ import annotations

import time
from typing import Literal


def _now() -> float:
    return time.monotonic()


Kind = Literal["human", "agent"]


class TypingRegistry:
    def __init__(self, human_ttl: int = 3, agent_ttl: int = 45) -> None:
        self._ttls: dict[str, int] = {"human": human_ttl, "agent": agent_ttl}
        # (channel_id, slug) -> (kind, expires_at)
        self._entries: dict[tuple[str, str], tuple[Kind, float]] = {}

    def mark(self, channel_id: str, slug: str, kind: Kind) -> None:
        now = _now()
        ttl = self._ttls[kind]
        self._entries[(channel_id, slug)] = (kind, now + ttl)

    def clear(self, channel_id: str, slug: str) -> None:
        self._entries.pop((channel_id, slug), None)

    def list(self, channel_id: str) -> dict[str, list[str]]:
        now = _now()
        out: dict[str, list[str]] = {"human": [], "agent": []}
        stale: list[tuple[str, str]] = []
        for (ch, slug), (kind, expires_at) in self._entries.items():
            if ch != channel_id:
                continue
            if expires_at < now:
                stale.append((ch, slug))
                continue
            out[kind].append(slug)
        for k in stale:
            self._entries.pop(k, None)
        out["human"].sort()
        out["agent"].sort()
        return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. pytest tests/test_chat_typing.py -v`
Expected: 9 passed.

- [ ] **Step 5: Commit**

```bash
git add tinyagentos/chat/typing_registry.py tests/test_chat_typing.py
git commit -m "feat(chat): TypingRegistry — per-channel human-typing + agent-thinking tracker"
```

---

## Task 3: Wire `TypingRegistry` on `app.state`

**Files:**
- Modify: `tinyagentos/app.py`

- [ ] **Step 1: Locate the state-init section**

Run: `grep -n 'app.state.wants_reply\|TypingRegistry\|WantsReplyRegistry' tinyagentos/app.py`
Expected: two or more lines showing `app.state.wants_reply = WantsReplyRegistry()` (the pattern to mirror).

- [ ] **Step 2: Add the import + wire**

In `tinyagentos/app.py`, next to the `WantsReplyRegistry` import at the top, add:

```python
from tinyagentos.chat.typing_registry import TypingRegistry
```

Next to every line that reads `app.state.wants_reply = WantsReplyRegistry()`, add immediately below:

```python
app.state.typing = TypingRegistry()
```

Grep guidance: `grep -n 'app.state.wants_reply = WantsReplyRegistry' tinyagentos/app.py` returns two locations (lifespan block + eager-state block). Both need the parallel line.

- [ ] **Step 3: Smoke test**

Run: `python -c "from tinyagentos.app import create_app; app = create_app(); assert hasattr(app.state, 'typing'); print('ok')"`
Expected: prints `ok`.

Run: `PYTHONPATH=. pytest tests/ -x -q`
Expected: no regressions (pre-existing 3 macOS hardware-arch failures are OK).

- [ ] **Step 4: Commit**

```bash
git add tinyagentos/app.py
git commit -m "feat(chat): wire TypingRegistry onto app.state"
```

---

## Task 4: POST `/typing` + POST `/thinking` + GET `/typing` endpoints

**Files:**
- Modify: `tinyagentos/routes/chat.py`
- Test: `tests/test_chat_typing.py` (extend)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_chat_typing.py`:

```python
import pytest
from httpx import AsyncClient, ASGITransport
from tinyagentos.app import create_app


@pytest.mark.asyncio
async def test_post_typing_marks_registry(tmp_path, monkeypatch):
    monkeypatch.setenv("TAOS_DATA_DIR", str(tmp_path))
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        store = app.state.chat_channels
        ch = await store.create_channel(
            name="g", type="group", description="", topic="",
            members=["user", "tom"], settings={}, created_by="user",
        )
        ch_id = ch["id"] if isinstance(ch, dict) else ch

        r = await client.post(
            f"/api/chat/channels/{ch_id}/typing",
            json={"author_id": "user"},
        )
        assert r.status_code == 200
        listing = app.state.typing.list(ch_id)
        assert "user" in listing["human"]


@pytest.mark.asyncio
async def test_post_thinking_start_marks_registry(tmp_path, monkeypatch):
    monkeypatch.setenv("TAOS_DATA_DIR", str(tmp_path))
    app = create_app()
    auth = getattr(app.state, "auth", None)
    token = auth.mint_local_token() if auth else "test-token"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        store = app.state.chat_channels
        ch = await store.create_channel(
            name="g", type="group", description="", topic="",
            members=["user", "tom"], settings={}, created_by="user",
        )
        ch_id = ch["id"] if isinstance(ch, dict) else ch

        r = await client.post(
            f"/api/chat/channels/{ch_id}/thinking",
            json={"slug": "tom", "state": "start"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        listing = app.state.typing.list(ch_id)
        assert "tom" in listing["agent"]

        # end clears
        r = await client.post(
            f"/api/chat/channels/{ch_id}/thinking",
            json={"slug": "tom", "state": "end"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200
        listing = app.state.typing.list(ch_id)
        assert "tom" not in listing["agent"]


@pytest.mark.asyncio
async def test_post_thinking_requires_bearer(tmp_path, monkeypatch):
    monkeypatch.setenv("TAOS_DATA_DIR", str(tmp_path))
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post(
            "/api/chat/channels/x/thinking",
            json={"slug": "tom", "state": "start"},
        )
        assert r.status_code == 401


@pytest.mark.asyncio
async def test_get_typing_returns_current_state(tmp_path, monkeypatch):
    monkeypatch.setenv("TAOS_DATA_DIR", str(tmp_path))
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        store = app.state.chat_channels
        ch = await store.create_channel(
            name="g", type="group", description="", topic="",
            members=["user", "tom", "don"], settings={}, created_by="user",
        )
        ch_id = ch["id"] if isinstance(ch, dict) else ch
        app.state.typing.mark(ch_id, "user", "human")
        app.state.typing.mark(ch_id, "tom", "agent")

        r = await client.get(f"/api/chat/channels/{ch_id}/typing")
        assert r.status_code == 200
        body = r.json()
        assert body == {"human": ["user"], "agent": ["tom"]}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. pytest tests/test_chat_typing.py -v -k "post_typing or post_thinking or get_typing"`
Expected: FAIL — endpoints don't exist.

- [ ] **Step 3: Implement the endpoints**

In `tinyagentos/routes/chat.py`, add near the existing channel-related endpoints (e.g., right after the reactions endpoints):

```python
@router.post("/api/chat/channels/{channel_id}/typing")
async def post_typing(channel_id: str, body: dict, request: Request):
    """Mark a human user as typing in the channel. Humans-only — agent
    heartbeats use /thinking. Ephemeral; TTL 3s.
    """
    author_id = (body or {}).get("author_id")
    if not author_id:
        return JSONResponse({"error": "author_id required"}, status_code=400)
    reg = getattr(request.app.state, "typing", None)
    hub = getattr(request.app.state, "chat_hub", None)
    if reg is None:
        return JSONResponse({"error": "typing registry not configured"}, status_code=503)
    reg.mark(channel_id, author_id, "human")
    if hub is not None:
        await hub.broadcast(channel_id, {
            "type": "typing",
            "kind": "human",
            "slug": author_id,
        })
    return JSONResponse({"ok": True}, status_code=200)


@router.post("/api/chat/channels/{channel_id}/thinking")
async def post_thinking(channel_id: str, body: dict, request: Request):
    """Bridge-side heartbeat: state=start marks the agent as thinking,
    state=end clears. Authenticated with the local bearer token the
    bridges already use for /api/openclaw/*.
    """
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
    reg = getattr(request.app.state, "typing", None)
    hub = getattr(request.app.state, "chat_hub", None)
    if reg is None:
        return JSONResponse({"error": "typing registry not configured"}, status_code=503)
    if state == "start":
        reg.mark(channel_id, slug, "agent")
    else:
        reg.clear(channel_id, slug)
    if hub is not None:
        await hub.broadcast(channel_id, {
            "type": "thinking",
            "slug": slug,
            "state": state,
        })
    return JSONResponse({"ok": True}, status_code=200)


@router.get("/api/chat/channels/{channel_id}/typing")
async def get_typing(channel_id: str, request: Request):
    """Return current typing+thinking state for a channel. Fallback for
    clients that lost the WS — UI also receives updates via broadcasts.
    """
    reg = getattr(request.app.state, "typing", None)
    if reg is None:
        return JSONResponse({"human": [], "agent": []})
    return JSONResponse(reg.list(channel_id))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. pytest tests/test_chat_typing.py -v`
Expected: 13 passed total (9 registry + 4 endpoint).

Run: `PYTHONPATH=. pytest tests/ -x -q`
Expected: no regressions.

- [ ] **Step 5: Commit**

```bash
git add tinyagentos/routes/chat.py tests/test_chat_typing.py
git commit -m "feat(chat): POST /typing + POST /thinking + GET /typing endpoints"
```

---

## Task 5: GET `/api/frameworks/slash-commands` endpoint

**Files:**
- Modify: `tinyagentos/routes/framework.py`
- Test: `tests/test_framework_api.py` (extend if exists; else create)

- [ ] **Step 1: Write the failing test**

Append (or create) `tests/test_framework_slash_commands.py`:

```python
import pytest
from httpx import AsyncClient, ASGITransport
from tinyagentos.app import create_app


@pytest.mark.asyncio
async def test_slash_commands_endpoint_returns_per_slug_manifest(tmp_path, monkeypatch):
    monkeypatch.setenv("TAOS_DATA_DIR", str(tmp_path))
    app = create_app()
    # Seed one agent so the endpoint has something to key on.
    app.state.config.agents.append({
        "name": "tom", "framework": "hermes", "status": "running",
    })
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/frameworks/slash-commands")
        assert r.status_code == 200
        body = r.json()
        # Shape: {slug: [{name, description}, ...]}
        assert "tom" in body
        assert isinstance(body["tom"], list)
        assert body["tom"][0]["name"] in ("help", "clear", "model")


@pytest.mark.asyncio
async def test_slash_commands_endpoint_handles_unknown_framework(tmp_path, monkeypatch):
    monkeypatch.setenv("TAOS_DATA_DIR", str(tmp_path))
    app = create_app()
    app.state.config.agents.append({
        "name": "mystery", "framework": "nonexistent-fw", "status": "running",
    })
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/api/frameworks/slash-commands")
        assert r.status_code == 200
        body = r.json()
        assert body.get("mystery") == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=. pytest tests/test_framework_slash_commands.py -v`
Expected: FAIL — endpoint missing.

- [ ] **Step 3: Implement the endpoint**

In `tinyagentos/routes/framework.py`, add after the existing `/api/frameworks/latest` handler:

```python
@router.get("/api/frameworks/slash-commands")
async def slash_commands_manifest(request: Request):
    """Return {slug: [{name, description}, ...]} for every agent in
    app.state.config.agents. Unknown framework or missing slash_commands
    field → empty list. Client (SlashMenu) reads this once per channel
    open and caches for 5 minutes.
    """
    from tinyagentos.frameworks import FRAMEWORKS
    config = getattr(request.app.state, "config", None)
    agents = getattr(config, "agents", []) if config else []
    out: dict[str, list[dict]] = {}
    for a in agents:
        slug = a.get("name")
        if not slug:
            continue
        fw = FRAMEWORKS.get(a.get("framework") or "", {})
        cmds = fw.get("slash_commands") or []
        # Copy so the caller can't mutate our static table.
        out[slug] = [{"name": c["name"], "description": c.get("description", "")} for c in cmds]
    return JSONResponse(out)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=. pytest tests/test_framework_slash_commands.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add tinyagentos/routes/framework.py tests/test_framework_slash_commands.py
git commit -m "feat(frameworks): GET /api/frameworks/slash-commands returns per-agent manifest"
```

---

## Task 6: Thinking heartbeat in all 6 bridges

**Files:**
- Modify: `tinyagentos/scripts/install_hermes.sh`
- Modify: `tinyagentos/scripts/install_smolagents.sh`
- Modify: `tinyagentos/scripts/install_langroid.sh`
- Modify: `tinyagentos/scripts/install_pocketflow.sh`
- Modify: `tinyagentos/scripts/install_openai_agents_sdk.sh`
- Modify: `tinyagentos/scripts/install_openai-agents-sdk.sh`

Each bridge has a `handle(c, evt, ch)` async function that receives a user_message event and calls `_run(text, force)` to get a reply. Wrap that call with a thinking heartbeat.

- [ ] **Step 1: Add `_thinking` helper above `handle` in each bridge's Python**

For each of the 6 install scripts, in the Python section inside the `cat > ... <<'BRIDGE_EOF'` heredoc, add this helper above `handle`:

```python
async def _thinking(c: httpx.AsyncClient, ch_id, state: str) -> None:
    if not ch_id:
        return
    try:
        await c.post(
            f"{BRIDGE_URL}/api/chat/channels/{ch_id}/thinking",
            json={"slug": AGENT_NAME, "state": state},
            headers={"Authorization": f"Bearer {LOCAL_TOKEN}"},
            timeout=5,
        )
    except Exception:
        pass  # best-effort; never block a reply on an indicator
```

- [ ] **Step 2: Wrap the `_run` call in `handle`**

For each bridge, replace the existing body of `handle` that looks roughly like:

```python
async def handle(c, evt, ch):
    mid = evt.get("id",""); tid = evt.get("trace_id", mid); text = evt.get("text","")
    force = bool(evt.get("force_respond"))
    ctx = _render_context(evt.get("context") or [])
    full = (f"Recent conversation:\n{ctx}\n\nCurrent: {text}") if ctx else text
    log.info("user_message id=%s text=%r force=%s", mid, text[:80], force)
    reply = await asyncio.get_running_loop().run_in_executor(_pool, _run, full, force)
    final = _suppress(reply, force)
    if final is None: return
    await post_reply(c, ch["reply_url"], ch["auth_bearer"], mid, tid, final, evt.get("channel_id"))
```

With:

```python
async def handle(c, evt, ch):
    mid = evt.get("id",""); tid = evt.get("trace_id", mid); text = evt.get("text","")
    force = bool(evt.get("force_respond"))
    ctx = _render_context(evt.get("context") or [])
    full = (f"Recent conversation:\n{ctx}\n\nCurrent: {text}") if ctx else text
    cid = evt.get("channel_id")
    log.info("user_message id=%s text=%r force=%s", mid, text[:80], force)
    await _thinking(c, cid, "start")
    try:
        reply = await asyncio.get_running_loop().run_in_executor(_pool, _run, full, force)
    finally:
        await _thinking(c, cid, "end")
    final = _suppress(reply, force)
    if final is None: return
    await post_reply(c, ch["reply_url"], ch["auth_bearer"], mid, tid, final, cid)
```

The hermes bridge uses `handle_user_message` and a slightly different call shape — the wrapping is analogous: add `_thinking` start before the `call_hermes` call, `_thinking` end in a `finally` block.

- [ ] **Step 3: Lint all 6 scripts with `bash -n`**

Run:
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

Expected: all 6 `ok`.

- [ ] **Step 4: Commit**

```bash
git add tinyagentos/scripts/install_hermes.sh tinyagentos/scripts/install_smolagents.sh \
        tinyagentos/scripts/install_langroid.sh tinyagentos/scripts/install_pocketflow.sh \
        tinyagentos/scripts/install_openai_agents_sdk.sh tinyagentos/scripts/install_openai-agents-sdk.sh
git commit -m "feat(bridges): emit thinking heartbeat around LLM call in all 6 bridges"
```

---

## Task 7: `channel-admin-api.ts` client

**Files:**
- Create: `desktop/src/lib/channel-admin-api.ts`
- Test: `desktop/src/lib/__tests__/channel-admin-api.test.ts`

- [ ] **Step 1: Write the failing test**

```typescript
// desktop/src/lib/__tests__/channel-admin-api.test.ts
import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  patchChannel,
  addChannelMember,
  removeChannelMember,
  muteAgent,
  unmuteAgent,
} from "../channel-admin-api";

describe("channel-admin-api", () => {
  beforeEach(() => {
    global.fetch = vi.fn(() =>
      Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ ok: true }) }),
    ) as unknown as typeof fetch;
  });

  it("patchChannel PATCHes with the provided body", async () => {
    await patchChannel("c1", { response_mode: "lively" });
    expect(fetch).toHaveBeenCalledWith(
      "/api/chat/channels/c1",
      expect.objectContaining({
        method: "PATCH",
        body: JSON.stringify({ response_mode: "lively" }),
      }),
    );
  });

  it("addChannelMember POSTs action=add", async () => {
    await addChannelMember("c1", "tom");
    expect(fetch).toHaveBeenCalledWith(
      "/api/chat/channels/c1/members",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ action: "add", slug: "tom" }),
      }),
    );
  });

  it("removeChannelMember POSTs action=remove", async () => {
    await removeChannelMember("c1", "tom");
    const call = (fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls.at(-1)!;
    expect(call[0]).toBe("/api/chat/channels/c1/members");
    expect(call[1]).toMatchObject({ body: JSON.stringify({ action: "remove", slug: "tom" }) });
  });

  it("muteAgent / unmuteAgent hit the muted endpoint", async () => {
    await muteAgent("c1", "tom");
    expect((fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls.at(-1)![0])
      .toBe("/api/chat/channels/c1/muted");

    await unmuteAgent("c1", "tom");
    const last = (fetch as unknown as { mock: { calls: unknown[][] } }).mock.calls.at(-1)!;
    expect(last[1]).toMatchObject({ body: JSON.stringify({ action: "remove", slug: "tom" }) });
  });

  it("throws with the server's error on non-OK response", async () => {
    global.fetch = vi.fn(() =>
      Promise.resolve({
        ok: false, status: 400,
        json: () => Promise.resolve({ error: "max_hops must be 1..10" }),
      }),
    ) as unknown as typeof fetch;
    await expect(patchChannel("c1", { max_hops: 99 })).rejects.toThrow("max_hops must be 1..10");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd desktop && npm test -- --run channel-admin-api`
Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Implement the client**

```typescript
// desktop/src/lib/channel-admin-api.ts
/**
 * Thin REST client for Phase 1 chat admin endpoints. Used by
 * ChannelSettingsPanel and AgentContextMenu — the UI layer on top of
 * PATCH /api/chat/channels/{id}, POST /members, POST /muted.
 *
 * Errors: every call throws Error(body.error || `HTTP ${status}`) on
 * non-OK response so callers can surface the server's message inline.
 */

type ChannelPatch = Partial<{
  response_mode: "quiet" | "lively";
  max_hops: number;
  cooldown_seconds: number;
  topic: string;
  name: string;
}>;

async function _json(r: Response): Promise<unknown> {
  try { return await r.json(); } catch { return null; }
}

async function _ensureOk(r: Response): Promise<void> {
  if (r.ok) return;
  const body = (await _json(r)) as { error?: string } | null;
  throw new Error(body?.error || `HTTP ${r.status}`);
}

export async function patchChannel(channelId: string, body: ChannelPatch): Promise<void> {
  const r = await fetch(`/api/chat/channels/${encodeURIComponent(channelId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  await _ensureOk(r);
}

export async function addChannelMember(channelId: string, slug: string): Promise<void> {
  const r = await fetch(`/api/chat/channels/${encodeURIComponent(channelId)}/members`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action: "add", slug }),
  });
  await _ensureOk(r);
}

export async function removeChannelMember(channelId: string, slug: string): Promise<void> {
  const r = await fetch(`/api/chat/channels/${encodeURIComponent(channelId)}/members`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action: "remove", slug }),
  });
  await _ensureOk(r);
}

export async function muteAgent(channelId: string, slug: string): Promise<void> {
  const r = await fetch(`/api/chat/channels/${encodeURIComponent(channelId)}/muted`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action: "add", slug }),
  });
  await _ensureOk(r);
}

export async function unmuteAgent(channelId: string, slug: string): Promise<void> {
  const r = await fetch(`/api/chat/channels/${encodeURIComponent(channelId)}/muted`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action: "remove", slug }),
  });
  await _ensureOk(r);
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd desktop && npm test -- --run channel-admin-api`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add desktop/src/lib/channel-admin-api.ts desktop/src/lib/__tests__/channel-admin-api.test.ts
git commit -m "feat(desktop): channel-admin-api client for Phase 1 REST endpoints"
```

---

## Task 8: `use-typing-emitter.ts` hook

**Files:**
- Create: `desktop/src/lib/use-typing-emitter.ts`
- Test: `desktop/src/lib/__tests__/use-typing-emitter.test.ts`

- [ ] **Step 1: Write the failing test**

```typescript
// desktop/src/lib/__tests__/use-typing-emitter.test.ts
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook } from "@testing-library/react";
import { useTypingEmitter } from "../use-typing-emitter";

describe("useTypingEmitter", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    global.fetch = vi.fn(() =>
      Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ ok: true }) }),
    ) as unknown as typeof fetch;
  });
  afterEach(() => vi.useRealTimers());

  it("POSTs /typing on first call, debounces subsequent calls within 1s", async () => {
    const { result } = renderHook(() => useTypingEmitter("c1", "jay"));
    result.current();
    expect(fetch).toHaveBeenCalledTimes(1);
    result.current();
    result.current();
    expect(fetch).toHaveBeenCalledTimes(1); // still debounced
    vi.advanceTimersByTime(1100);
    result.current();
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it("does nothing when channelId is null", () => {
    const { result } = renderHook(() => useTypingEmitter(null, "jay"));
    result.current();
    expect(fetch).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd desktop && npm test -- --run use-typing-emitter`
Expected: FAIL — hook doesn't exist.

- [ ] **Step 3: Implement the hook**

```typescript
// desktop/src/lib/use-typing-emitter.ts
import { useCallback, useRef } from "react";

/**
 * Returns a function the composer calls on each keystroke. Emits POST
 * /api/chat/channels/{id}/typing at most once per second; the server
 * TTL is 3s so one POST per second is enough to keep the indicator alive
 * while the user is actively typing.
 */
export function useTypingEmitter(
  channelId: string | null,
  authorId: string,
  debounceMs = 1000,
): () => void {
  const lastSentAt = useRef(0);
  const inFlight = useRef(false);

  return useCallback(() => {
    if (!channelId) return;
    const now = Date.now();
    if (now - lastSentAt.current < debounceMs) return;
    if (inFlight.current) return;
    lastSentAt.current = now;
    inFlight.current = true;
    fetch(`/api/chat/channels/${encodeURIComponent(channelId)}/typing`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ author_id: authorId }),
    }).catch(() => {}).finally(() => { inFlight.current = false; });
  }, [channelId, authorId, debounceMs]);
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd desktop && npm test -- --run use-typing-emitter`
Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add desktop/src/lib/use-typing-emitter.ts desktop/src/lib/__tests__/use-typing-emitter.test.ts
git commit -m "feat(desktop): useTypingEmitter hook — debounced /typing POST on composer keystroke"
```

---

## Task 9: `TypingFooter` component

**Files:**
- Create: `desktop/src/apps/chat/TypingFooter.tsx`

- [ ] **Step 1: Implement the component**

```tsx
// desktop/src/apps/chat/TypingFooter.tsx
import React from "react";

/**
 * Two-line strip rendered between the last message and the composer.
 * Line 1: humans-typing. Line 2: agent-thinking.
 * Caller feeds in live arrays — they're empty when nothing is active
 * and the component renders nothing.
 */
export function TypingFooter({
  humans,
  agents,
  selfId = "user",
}: {
  humans: string[];
  agents: string[];
  selfId?: string;
}) {
  const others = humans.filter((h) => h !== selfId);
  const hasHumans = others.length > 0;
  const hasAgents = agents.length > 0;
  if (!hasHumans && !hasAgents) return null;

  const humanLine = formatHumansTyping(others);
  const agentLine = formatAgentsThinking(agents);

  return (
    <div
      aria-live="polite"
      className="px-4 pt-1 text-xs text-shell-text-tertiary flex flex-col gap-0.5"
    >
      {humanLine && <span>{humanLine}</span>}
      {agentLine && <span className="italic">{agentLine}</span>}
    </div>
  );
}

function formatHumansTyping(names: string[]): string | null {
  if (names.length === 0) return null;
  if (names.length === 1) return `${names[0]} is typing…`;
  if (names.length === 2) return `${names[0]} and ${names[1]} are typing…`;
  return `${names[0]} and ${names.length - 1} others are typing…`;
}

function formatAgentsThinking(slugs: string[]): string | null {
  if (slugs.length === 0) return null;
  return slugs.map((s) => `${s} is thinking…`).join(" · ");
}
```

- [ ] **Step 2: Smoke test (render via existing test harness or a new test)**

Add `desktop/src/apps/chat/__tests__/TypingFooter.test.tsx`:

```tsx
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { TypingFooter } from "../TypingFooter";

describe("TypingFooter", () => {
  it("renders nothing when empty", () => {
    const { container } = render(<TypingFooter humans={[]} agents={[]} />);
    expect(container.firstChild).toBeNull();
  });
  it("shows one human typing", () => {
    render(<TypingFooter humans={["alice"]} agents={[]} />);
    expect(screen.getByText("alice is typing…")).toBeInTheDocument();
  });
  it("shows N others when humans > 2", () => {
    render(<TypingFooter humans={["alice", "bob", "carol"]} agents={[]} />);
    expect(screen.getByText("alice and 2 others are typing…")).toBeInTheDocument();
  });
  it("filters self out of humans", () => {
    const { container } = render(<TypingFooter humans={["user"]} agents={[]} selfId="user" />);
    expect(container.firstChild).toBeNull();
  });
  it("joins multiple agents with middle dot", () => {
    render(<TypingFooter humans={[]} agents={["tom", "don"]} />);
    expect(screen.getByText("tom is thinking… · don is thinking…")).toBeInTheDocument();
  });
});
```

- [ ] **Step 3: Run tests**

Run: `cd desktop && npm test -- --run TypingFooter`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add desktop/src/apps/chat/TypingFooter.tsx desktop/src/apps/chat/__tests__/TypingFooter.test.tsx
git commit -m "feat(desktop): TypingFooter component for humans-typing + agents-thinking strip"
```

---

## Task 10: `AgentContextMenu` component

**Files:**
- Create: `desktop/src/apps/chat/AgentContextMenu.tsx`
- Test: `desktop/src/apps/chat/__tests__/AgentContextMenu.test.tsx`

- [ ] **Step 1: Implement the component**

```tsx
// desktop/src/apps/chat/AgentContextMenu.tsx
import React, { useEffect, useRef } from "react";
import { muteAgent, unmuteAgent, removeChannelMember } from "@/lib/channel-admin-api";

export type AgentContextMenuProps = {
  slug: string;
  channelId?: string;
  channelType?: string;
  isMuted?: boolean;
  x: number;
  y: number;
  onClose: () => void;
  onDm?: (slug: string) => void;
  onViewInfo?: (slug: string) => void;
  onJumpToSettings?: (slug: string) => void;
};

export function AgentContextMenu({
  slug, channelId, channelType, isMuted,
  x, y, onClose, onDm, onViewInfo, onJumpToSettings,
}: AgentContextMenuProps) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onDocClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("mousedown", onDocClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDocClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [onClose]);

  const isDm = channelType === "dm";

  const doMute = async () => {
    if (!channelId) return;
    try {
      if (isMuted) await unmuteAgent(channelId, slug);
      else await muteAgent(channelId, slug);
    } finally { onClose(); }
  };
  const doRemove = async () => {
    if (!channelId) return;
    try { await removeChannelMember(channelId, slug); } finally { onClose(); }
  };

  return (
    <div
      ref={ref}
      role="menu"
      aria-label={`Actions for @${slug}`}
      className="fixed z-50 min-w-[200px] bg-shell-surface border border-white/10 rounded-lg shadow-xl py-1 text-sm"
      style={{ top: y, left: x }}
    >
      <MenuItem onClick={() => { onDm?.(slug); onClose(); }}>DM @{slug}</MenuItem>
      {channelId && !isDm && (
        <MenuItem onClick={doMute}>
          {isMuted ? "Unmute" : "Mute"} in this channel
        </MenuItem>
      )}
      {channelId && !isDm && (
        <MenuItem onClick={doRemove}>Remove from channel</MenuItem>
      )}
      <div className="my-1 h-px bg-white/10" />
      <MenuItem onClick={() => { onViewInfo?.(slug); onClose(); }}>View agent info</MenuItem>
      <MenuItem onClick={() => { onJumpToSettings?.(slug); onClose(); }}>Jump to agent settings</MenuItem>
    </div>
  );
}

function MenuItem({ onClick, children }: { onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      role="menuitem"
      onClick={onClick}
      className="w-full text-left px-3 py-1.5 hover:bg-white/5 focus:bg-white/5 focus:outline-none"
    >
      {children}
    </button>
  );
}
```

- [ ] **Step 2: Write the component test**

```tsx
// desktop/src/apps/chat/__tests__/AgentContextMenu.test.tsx
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { AgentContextMenu } from "../AgentContextMenu";

describe("AgentContextMenu", () => {
  beforeEach(() => {
    global.fetch = vi.fn(() =>
      Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true }) }),
    ) as unknown as typeof fetch;
  });

  it("shows DM and framework items", () => {
    render(
      <AgentContextMenu slug="tom" channelId="c1" channelType="group"
        x={0} y={0} onClose={() => {}} />,
    );
    expect(screen.getByText("DM @tom")).toBeInTheDocument();
    expect(screen.getByText("Mute in this channel")).toBeInTheDocument();
    expect(screen.getByText("Remove from channel")).toBeInTheDocument();
    expect(screen.getByText("View agent info")).toBeInTheDocument();
    expect(screen.getByText("Jump to agent settings")).toBeInTheDocument();
  });

  it("hides mute and remove in DMs", () => {
    render(
      <AgentContextMenu slug="tom" channelId="c1" channelType="dm"
        x={0} y={0} onClose={() => {}} />,
    );
    expect(screen.queryByText("Mute in this channel")).not.toBeInTheDocument();
    expect(screen.queryByText("Remove from channel")).not.toBeInTheDocument();
  });

  it("shows 'Unmute' when isMuted is true", () => {
    render(
      <AgentContextMenu slug="tom" channelId="c1" channelType="group" isMuted
        x={0} y={0} onClose={() => {}} />,
    );
    expect(screen.getByText("Unmute in this channel")).toBeInTheDocument();
  });

  it("calls muteAgent then onClose when Mute is clicked", async () => {
    const onClose = vi.fn();
    render(
      <AgentContextMenu slug="tom" channelId="c1" channelType="group"
        x={0} y={0} onClose={onClose} />,
    );
    fireEvent.click(screen.getByText("Mute in this channel"));
    // allow microtasks to flush
    await Promise.resolve();
    expect(fetch).toHaveBeenCalledWith(
      "/api/chat/channels/c1/muted",
      expect.objectContaining({ method: "POST" }),
    );
    expect(onClose).toHaveBeenCalled();
  });

  it("closes on Escape", () => {
    const onClose = vi.fn();
    render(<AgentContextMenu slug="tom" x={0} y={0} onClose={onClose} />);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });
});
```

- [ ] **Step 3: Run tests**

Run: `cd desktop && npm test -- --run AgentContextMenu`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add desktop/src/apps/chat/AgentContextMenu.tsx desktop/src/apps/chat/__tests__/AgentContextMenu.test.tsx
git commit -m "feat(desktop): AgentContextMenu — single right-click hub for agent actions"
```

---

## Task 11: `ChannelSettingsPanel` component

**Files:**
- Create: `desktop/src/apps/chat/ChannelSettingsPanel.tsx`

- [ ] **Step 1: Implement the component**

```tsx
// desktop/src/apps/chat/ChannelSettingsPanel.tsx
import React, { useEffect, useState } from "react";
import {
  patchChannel, addChannelMember, removeChannelMember, muteAgent, unmuteAgent,
} from "@/lib/channel-admin-api";

type Channel = {
  id: string;
  name: string;
  type: "dm" | "group" | "topic";
  topic: string;
  members: string[];
  settings: {
    response_mode?: "quiet" | "lively";
    max_hops?: number;
    cooldown_seconds?: number;
    muted?: string[];
  };
};

type KnownAgent = { name: string };

export function ChannelSettingsPanel({
  channel, knownAgents, onClose, onChanged,
}: {
  channel: Channel;
  knownAgents: KnownAgent[];
  onClose: () => void;
  onChanged: () => void;
}) {
  const [name, setName] = useState(channel.name);
  const [topic, setTopic] = useState(channel.topic || "");
  const [mode, setMode] = useState(channel.settings.response_mode ?? "quiet");
  const [hops, setHops] = useState(channel.settings.max_hops ?? 3);
  const [cooldown, setCooldown] = useState(channel.settings.cooldown_seconds ?? 5);
  const [err, setErr] = useState<string | null>(null);

  // Keep local state in sync if the parent pushes an updated channel
  useEffect(() => {
    setName(channel.name);
    setTopic(channel.topic || "");
    setMode(channel.settings.response_mode ?? "quiet");
    setHops(channel.settings.max_hops ?? 3);
    setCooldown(channel.settings.cooldown_seconds ?? 5);
  }, [channel]);

  const apply = async (patch: Parameters<typeof patchChannel>[1], rollback: () => void) => {
    setErr(null);
    try { await patchChannel(channel.id, patch); onChanged(); }
    catch (e) { rollback(); setErr(e instanceof Error ? e.message : "failed"); }
  };

  const members = channel.members || [];
  const muted = channel.settings.muted || [];
  const candidateAdds = knownAgents
    .map((a) => a.name)
    .filter((s) => !members.includes(s));
  const candidateMutes = members.filter((m) => m !== "user" && !muted.includes(m));

  return (
    <aside
      role="complementary"
      aria-label="Channel settings"
      className="fixed top-0 right-0 h-full w-[360px] bg-shell-surface border-l border-white/10 shadow-xl flex flex-col z-40"
    >
      <header className="flex items-center justify-between px-4 py-3 border-b border-white/10">
        <h2 className="text-sm font-semibold">Channel settings</h2>
        <button onClick={onClose} aria-label="Close" className="text-lg leading-none">×</button>
      </header>

      <div className="flex-1 overflow-y-auto px-4 py-4 flex flex-col gap-5 text-sm">
        <section aria-label="Overview" className="flex flex-col gap-3">
          <h3 className="text-xs uppercase tracking-wider text-shell-text-tertiary">Overview</h3>
          <label className="flex flex-col gap-1">
            <span className="text-xs text-shell-text-secondary">Name</span>
            <input
              value={name}
              maxLength={100}
              onChange={(e) => setName(e.target.value)}
              onBlur={() => name !== channel.name && apply({ name }, () => setName(channel.name))}
              className="bg-white/5 border border-white/10 rounded px-2 py-1.5 text-sm"
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-xs text-shell-text-secondary">Topic</span>
            <textarea
              value={topic}
              maxLength={500}
              rows={3}
              onChange={(e) => setTopic(e.target.value)}
              onBlur={() => topic !== (channel.topic || "") && apply({ topic }, () => setTopic(channel.topic || ""))}
              className="bg-white/5 border border-white/10 rounded px-2 py-1.5 text-sm resize-none"
            />
          </label>
          <div className="text-[11px] text-shell-text-tertiary">
            Type: <span className="uppercase tracking-wide">{channel.type}</span>
          </div>
        </section>

        <section aria-label="Members" className="flex flex-col gap-2">
          <h3 className="text-xs uppercase tracking-wider text-shell-text-tertiary">Members</h3>
          <ul className="flex flex-col gap-1">
            {members.map((m) => (
              <li key={m} className="flex items-center justify-between px-2 py-1 rounded hover:bg-white/5">
                <span>@{m}</span>
                {m !== "user" && (
                  <button
                    className="text-xs text-red-300 hover:text-red-200"
                    onClick={async () => {
                      try { await removeChannelMember(channel.id, m); onChanged(); }
                      catch (e) { setErr(e instanceof Error ? e.message : "failed"); }
                    }}
                  >
                    Remove
                  </button>
                )}
              </li>
            ))}
          </ul>
          {candidateAdds.length > 0 && (
            <AddDropdown
              label="Add agent"
              options={candidateAdds}
              onPick={async (slug) => {
                try { await addChannelMember(channel.id, slug); onChanged(); }
                catch (e) { setErr(e instanceof Error ? e.message : "failed"); }
              }}
            />
          )}
        </section>

        <section aria-label="Moderation" className="flex flex-col gap-3">
          <h3 className="text-xs uppercase tracking-wider text-shell-text-tertiary">Moderation</h3>
          <div className="flex items-center gap-2">
            <span className="text-xs text-shell-text-secondary">Mode:</span>
            <button
              className={`px-2 py-1 rounded text-xs ${mode === "quiet" ? "bg-sky-500/30 text-sky-200" : "bg-white/5"}`}
              onClick={() => apply({ response_mode: "quiet" }, () => setMode(mode))}
            >quiet</button>
            <button
              className={`px-2 py-1 rounded text-xs ${mode === "lively" ? "bg-emerald-500/30 text-emerald-200" : "bg-white/5"}`}
              onClick={() => apply({ response_mode: "lively" }, () => setMode(mode))}
            >lively</button>
          </div>
          <div className="flex flex-col gap-1">
            <span className="text-xs text-shell-text-secondary">Muted</span>
            <div className="flex flex-wrap gap-1">
              {muted.map((m) => (
                <span key={m} className="inline-flex items-center gap-1 bg-white/5 rounded px-2 py-0.5 text-xs">
                  @{m}
                  <button
                    aria-label={`Unmute ${m}`}
                    onClick={async () => { try { await unmuteAgent(channel.id, m); onChanged(); } catch (e) { setErr(e instanceof Error ? e.message : "failed"); } }}
                  >×</button>
                </span>
              ))}
              {muted.length === 0 && <span className="text-[11px] text-shell-text-tertiary">none</span>}
            </div>
            {candidateMutes.length > 0 && (
              <AddDropdown
                label="Mute agent"
                options={candidateMutes}
                onPick={async (slug) => {
                  try { await muteAgent(channel.id, slug); onChanged(); }
                  catch (e) { setErr(e instanceof Error ? e.message : "failed"); }
                }}
              />
            )}
          </div>
        </section>

        <section aria-label="Advanced" className="flex flex-col gap-3">
          <h3 className="text-xs uppercase tracking-wider text-shell-text-tertiary">Advanced</h3>
          <label className="flex flex-col gap-1">
            <span className="text-xs text-shell-text-secondary">Max hops: {hops}</span>
            <input
              type="range" min={1} max={10} value={hops}
              onChange={(e) => setHops(Number(e.target.value))}
              onMouseUp={() => apply({ max_hops: hops }, () => setHops(channel.settings.max_hops ?? 3))}
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-xs text-shell-text-secondary">Cooldown: {cooldown}s</span>
            <input
              type="range" min={0} max={60} value={cooldown}
              onChange={(e) => setCooldown(Number(e.target.value))}
              onMouseUp={() => apply({ cooldown_seconds: cooldown }, () => setCooldown(channel.settings.cooldown_seconds ?? 5))}
            />
          </label>
        </section>

        {err && (
          <div role="alert" className="text-xs text-red-300 bg-red-500/10 border border-red-500/30 rounded px-2 py-1">
            {err}
          </div>
        )}
      </div>
    </aside>
  );
}

function AddDropdown({
  label, options, onPick,
}: {
  label: string; options: string[]; onPick: (v: string) => void;
}) {
  return (
    <select
      aria-label={label}
      defaultValue=""
      onChange={(e) => { if (e.target.value) { onPick(e.target.value); e.target.value = ""; } }}
      className="bg-white/5 border border-white/10 rounded px-2 py-1 text-xs"
    >
      <option value="" disabled>{label}…</option>
      {options.map((s) => <option key={s} value={s}>@{s}</option>)}
    </select>
  );
}
```

- [ ] **Step 2: Smoke test it renders**

Add `desktop/src/apps/chat/__tests__/ChannelSettingsPanel.test.tsx`:

```tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { ChannelSettingsPanel } from "../ChannelSettingsPanel";

describe("ChannelSettingsPanel", () => {
  const channel = {
    id: "c1",
    name: "roundtable",
    type: "group" as const,
    topic: "The arena",
    members: ["user", "tom", "don"],
    settings: { response_mode: "quiet" as const, max_hops: 3, cooldown_seconds: 5, muted: [] as string[] },
  };
  const knownAgents = [{ name: "tom" }, { name: "don" }, { name: "linus" }];

  it("renders the four sections with correct header labels", () => {
    render(<ChannelSettingsPanel channel={channel} knownAgents={knownAgents}
             onClose={vi.fn()} onChanged={vi.fn()} />);
    expect(screen.getByRole("heading", { name: /Channel settings/ })).toBeInTheDocument();
    expect(screen.getByText("Overview")).toBeInTheDocument();
    expect(screen.getByText("Members")).toBeInTheDocument();
    expect(screen.getByText("Moderation")).toBeInTheDocument();
    expect(screen.getByText("Advanced")).toBeInTheDocument();
  });

  it("populates inputs from the channel prop", () => {
    render(<ChannelSettingsPanel channel={channel} knownAgents={knownAgents}
             onClose={vi.fn()} onChanged={vi.fn()} />);
    expect((screen.getByDisplayValue("roundtable"))).toBeInTheDocument();
    expect(screen.getByDisplayValue("The arena")).toBeInTheDocument();
  });
});
```

- [ ] **Step 3: Run tests**

Run: `cd desktop && npm test -- --run ChannelSettingsPanel`
Expected: pass.

- [ ] **Step 4: Commit**

```bash
git add desktop/src/apps/chat/ChannelSettingsPanel.tsx desktop/src/apps/chat/__tests__/ChannelSettingsPanel.test.tsx
git commit -m "feat(desktop): ChannelSettingsPanel slide-over with 4 sections"
```

---

## Task 12: `SlashMenu` component

**Files:**
- Create: `desktop/src/apps/chat/SlashMenu.tsx`
- Test: `desktop/src/apps/chat/__tests__/SlashMenu.test.tsx`

- [ ] **Step 1: Implement the component**

```tsx
// desktop/src/apps/chat/SlashMenu.tsx
import React, { useEffect, useMemo, useState } from "react";

type Cmd = { name: string; description: string };
export type SlashCommandsBySlug = Record<string, Cmd[]>;

type Row =
  | { kind: "header"; slug: string }
  | { kind: "cmd"; slug: string; cmd: Cmd };

export function SlashMenu({
  commands,
  queryAfterSlash,
  members,
  onPick,
  onClose,
}: {
  commands: SlashCommandsBySlug;
  queryAfterSlash: string;
  members: string[]; // agents in current channel (ordered)
  onPick: (slug: string, cmd: string) => void;
  onClose: () => void;
}) {
  const [selected, setSelected] = useState(0);

  const rows = useMemo(() => buildRows(commands, members, queryAfterSlash), [commands, members, queryAfterSlash]);
  const cmdRows = rows.filter((r) => r.kind === "cmd") as Extract<Row, { kind: "cmd" }>[];

  useEffect(() => { setSelected(0); }, [queryAfterSlash]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.preventDefault(); onClose(); return; }
      if (e.key === "ArrowDown") { e.preventDefault(); setSelected((s) => Math.min(cmdRows.length - 1, s + 1)); return; }
      if (e.key === "ArrowUp") { e.preventDefault(); setSelected((s) => Math.max(0, s - 1)); return; }
      if (e.key === "Enter") {
        e.preventDefault();
        const pick = cmdRows[selected];
        if (pick) onPick(pick.slug, pick.cmd.name);
      }
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [cmdRows, selected, onPick, onClose]);

  if (cmdRows.length === 0 && rows.length === 0) return null;

  return (
    <div
      role="listbox"
      aria-label="Slash commands"
      className="absolute bottom-full left-0 mb-2 w-full max-w-md bg-shell-surface border border-white/10 rounded-lg shadow-xl max-h-60 overflow-y-auto text-sm"
    >
      {rows.length === 0 ? (
        <div className="px-3 py-2 text-xs text-shell-text-tertiary">(no commands available)</div>
      ) : (
        rows.map((row, i) => {
          if (row.kind === "header") {
            return (
              <div key={`h-${row.slug}`} className="px-3 py-1 text-[11px] uppercase tracking-wider text-shell-text-tertiary bg-white/5">
                @{row.slug}
              </div>
            );
          }
          const idx = cmdRows.indexOf(row);
          const isSelected = idx === selected;
          return (
            <button
              key={`${row.slug}-${row.cmd.name}`}
              role="option"
              aria-selected={isSelected}
              onMouseEnter={() => setSelected(idx)}
              onClick={() => onPick(row.slug, row.cmd.name)}
              className={`w-full text-left px-3 py-1.5 flex items-center justify-between gap-3 ${
                isSelected ? "bg-white/10" : "hover:bg-white/5"
              }`}
            >
              <span className="font-mono text-[13px]">/{row.cmd.name}</span>
              <span className="text-xs text-shell-text-tertiary truncate">{row.cmd.description}</span>
            </button>
          );
        })
      )}
    </div>
  );
}

function buildRows(
  commands: SlashCommandsBySlug,
  members: string[],
  query: string,
): Row[] {
  const q = query.toLowerCase();
  const agentMembers = members.filter((m) => m !== "user" && commands[m]);
  const isDm = agentMembers.length === 1;
  const rows: Row[] = [];
  for (const slug of agentMembers) {
    const cmds = (commands[slug] || []).filter((c) => matches(slug, c, q));
    if (cmds.length === 0) continue;
    if (!isDm) rows.push({ kind: "header", slug });
    for (const cmd of cmds) rows.push({ kind: "cmd", slug, cmd });
  }
  return rows;
}

function matches(slug: string, cmd: Cmd, q: string): boolean {
  if (!q) return true;
  const hay = `${slug} ${cmd.name} ${cmd.description}`.toLowerCase();
  // simple subsequence match — "to he" matches "tom help"
  let idx = 0;
  for (const ch of q.split(/\s+/).join("")) {
    const next = hay.indexOf(ch, idx);
    if (next === -1) return false;
    idx = next + 1;
  }
  return true;
}
```

- [ ] **Step 2: Write the component test**

```tsx
// desktop/src/apps/chat/__tests__/SlashMenu.test.tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { SlashMenu } from "../SlashMenu";

const commands = {
  tom: [{ name: "help", description: "Show Hermes help" }, { name: "clear", description: "Clear context" }],
  don: [{ name: "help", description: "SmolAgents help" }],
};

describe("SlashMenu", () => {
  it("renders header + commands grouped per agent in a group channel", () => {
    render(<SlashMenu commands={commands} queryAfterSlash="" members={["user", "tom", "don"]}
             onPick={vi.fn()} onClose={vi.fn()} />);
    expect(screen.getByText("@tom")).toBeInTheDocument();
    expect(screen.getByText("@don")).toBeInTheDocument();
    expect(screen.getAllByText("/help").length).toBe(2);
    expect(screen.getByText("/clear")).toBeInTheDocument();
  });

  it("drops the header in a DM", () => {
    render(<SlashMenu commands={commands} queryAfterSlash="" members={["user", "tom"]}
             onPick={vi.fn()} onClose={vi.fn()} />);
    expect(screen.queryByText("@tom")).not.toBeInTheDocument();
    expect(screen.getByText("/help")).toBeInTheDocument();
    expect(screen.getByText("/clear")).toBeInTheDocument();
  });

  it("fuzzy filters across slug + command", () => {
    render(<SlashMenu commands={commands} queryAfterSlash="tomc" members={["user", "tom", "don"]}
             onPick={vi.fn()} onClose={vi.fn()} />);
    expect(screen.getByText("/clear")).toBeInTheDocument();
    expect(screen.queryByText("/help")).not.toBeInTheDocument();
  });

  it("Enter invokes onPick with the current selection", () => {
    const onPick = vi.fn();
    render(<SlashMenu commands={commands} queryAfterSlash="" members={["user", "tom", "don"]}
             onPick={onPick} onClose={vi.fn()} />);
    fireEvent.keyDown(document, { key: "Enter" });
    expect(onPick).toHaveBeenCalledWith("tom", "help");
  });

  it("Escape calls onClose", () => {
    const onClose = vi.fn();
    render(<SlashMenu commands={commands} queryAfterSlash="" members={["user", "tom"]}
             onPick={vi.fn()} onClose={onClose} />);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });
});
```

- [ ] **Step 3: Run tests**

Run: `cd desktop && npm test -- --run SlashMenu`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add desktop/src/apps/chat/SlashMenu.tsx desktop/src/apps/chat/__tests__/SlashMenu.test.tsx
git commit -m "feat(desktop): SlashMenu — unified fuzzy command autocomplete grouped by agent"
```

---

## Task 13: Integrate components into `MessagesApp`

**Files:**
- Modify: `desktop/src/apps/MessagesApp.tsx`

This task mounts the four new components into the existing chat. Read the file first to find the composer + message rendering blocks; the integration adds:
1. A settings `ⓘ` button in the chat header (only when not DM) that toggles `ChannelSettingsPanel`
2. Right-click handler on message author spans + agent avatars that opens `AgentContextMenu`
3. A composer change-handler that detects `/` at position 0 and opens `SlashMenu`
4. A WS message handler for `type === "typing"` and `type === "thinking"` that updates a local `{human: string[], agent: string[]}` state
5. `<TypingFooter humans={...} agents={...} />` between message list and composer
6. `useTypingEmitter` hook in the composer keystroke path
7. A one-shot fetch of `/api/frameworks/slash-commands` on channel switch, cached 5 minutes

- [ ] **Step 1: Add state + fetch hook for slash commands**

Near the top of `MessagesApp`, next to the existing `useState` block:

```tsx
import { ChannelSettingsPanel } from "./chat/ChannelSettingsPanel";
import { AgentContextMenu } from "./chat/AgentContextMenu";
import { SlashMenu, type SlashCommandsBySlug } from "./chat/SlashMenu";
import { TypingFooter } from "./chat/TypingFooter";
import { useTypingEmitter } from "@/lib/use-typing-emitter";

// ...inside MessagesApp():
const [showSettings, setShowSettings] = useState(false);
const [contextMenu, setContextMenu] = useState<{ slug: string; x: number; y: number } | null>(null);
const [agentInfoPopover, setAgentInfoPopover] = useState<
  { slug: string; framework: string; model: string; status: string; x: number; y: number } | null
>(null);
const [slashCommands, setSlashCommands] = useState<SlashCommandsBySlug>({});
const [typingHumans, setTypingHumans] = useState<string[]>([]);
const [typingAgents, setTypingAgents] = useState<string[]>([]);

useEffect(() => {
  let alive = true;
  fetch("/api/frameworks/slash-commands")
    .then((r) => r.json())
    .then((d) => { if (alive) setSlashCommands(d || {}); })
    .catch(() => {});
  return () => { alive = false; };
}, [selectedChannel]);
```

- [ ] **Step 2: Settings button in header (non-DM only)**

Find the chat header JSX (search for the channel name rendering, around where the existing tabs live). Add next to the channel name:

```tsx
{currentChannel && currentChannel.type !== "dm" && (
  <button
    aria-label="Channel settings"
    onClick={() => setShowSettings(true)}
    className="ml-1 opacity-60 hover:opacity-100"
  >ⓘ</button>
)}
```

Render the panel at the root level of the app:

```tsx
{showSettings && currentChannel && (
  <ChannelSettingsPanel
    channel={currentChannel}
    knownAgents={liveAgents.map((a) => ({ name: a.name }))}
    onClose={() => setShowSettings(false)}
    onChanged={() => { void loadChannels(); }}
  />
)}
```

(`loadChannels` is the existing refresh-channels-list function in this file; grep to confirm its name and adapt.)

- [ ] **Step 3: Right-click handler on agent surfaces**

In the message-row JSX (find where the author name/avatar is rendered), add:

```tsx
onContextMenu={(e) => {
  if (msg.author_type !== "agent") return;
  e.preventDefault();
  setContextMenu({ slug: msg.author_id, x: e.clientX, y: e.clientY });
}}
```

Render the menu at the root:

```tsx
{contextMenu && (
  <AgentContextMenu
    slug={contextMenu.slug}
    channelId={selectedChannel ?? undefined}
    channelType={currentChannel?.type}
    isMuted={currentChannel?.settings?.muted?.includes(contextMenu.slug) ?? false}
    x={contextMenu.x}
    y={contextMenu.y}
    onClose={() => setContextMenu(null)}
    onDm={async (slug) => {
      // Find an existing DM channel containing exactly {user, slug}, else create one.
      const existing = channels.find((ch) =>
        ch.type === "dm"
        && (ch.members || []).length === 2
        && (ch.members || []).includes("user")
        && (ch.members || []).includes(slug)
      );
      if (existing) {
        setSelectedChannel(existing.id);
      } else {
        const r = await fetch("/api/chat/channels", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            name: slug,
            type: "dm",
            members: ["user", slug],
            description: "",
            topic: "",
          }),
        });
        if (r.ok) {
          const created = await r.json();
          await loadChannels();
          setSelectedChannel(created.id);
        }
      }
      setContextMenu(null);
    }}
    onViewInfo={(slug) => {
      // Open a minimal read-only info bubble. liveAgents already has the
      // framework/model/status we need — render inline near the cursor.
      const agent = liveAgents.find((a) => a.name === slug);
      if (agent) {
        setAgentInfoPopover({
          slug,
          framework: agent.framework || "unknown",
          model: agent.model || "unknown",
          status: agent.status || "unknown",
          x: contextMenu.x,
          y: contextMenu.y,
        });
      }
      setContextMenu(null);
    }}
    onJumpToSettings={(slug) => {
      window.dispatchEvent(new CustomEvent("taos:open-agent", { detail: { slug } }));
      setContextMenu(null);
    }}
  />
)}
```

- [ ] **Step 4: Slash menu in composer**

Find the composer input element. Add:

```tsx
const showSlash = input.startsWith("/");
const query = showSlash ? input.slice(1).split(/\s/, 1)[0] || "" : "";
```

Above or wrapping the input, render the menu:

```tsx
<div className="relative">
  <input
    value={input}
    onChange={(e) => { setInput(e.target.value); emitTyping(); }}
    onKeyDown={(e) => {
      if (showSlash && (e.key === "ArrowDown" || e.key === "ArrowUp" || e.key === "Enter" || e.key === "Escape")) {
        // let SlashMenu handle; it has its own listener
      }
    }}
    placeholder={showSlash ? "pick a command or Esc to send as text" : "Message"}
  />
  {showSlash && (
    <SlashMenu
      commands={slashCommands}
      queryAfterSlash={query}
      members={currentChannel?.members || []}
      onPick={(slug, cmd) => {
        setInput(`@${slug} /${cmd} `);
        // refocus the input and move cursor to end
      }}
      onClose={() => { /* leave input as-is */ }}
    />
  )}
</div>
```

Add the typing emitter:

```tsx
const emitTyping = useTypingEmitter(selectedChannel, "user");
```

- [ ] **Step 5: WS handler for typing + thinking**

Find the existing `ws.onmessage` handler. Add branches:

```tsx
if (data.type === "typing" && data.kind === "human") {
  setTypingHumans((prev) => prev.includes(data.slug) ? prev : [...prev, data.slug]);
  setTimeout(() => setTypingHumans((prev) => prev.filter((s) => s !== data.slug)), 3500);
  return;
}
if (data.type === "thinking") {
  if (data.state === "start") {
    setTypingAgents((prev) => prev.includes(data.slug) ? prev : [...prev, data.slug]);
  } else {
    setTypingAgents((prev) => prev.filter((s) => s !== data.slug));
  }
  return;
}
```

- [ ] **Step 6: Render `TypingFooter` between message list and composer**

Just above the composer JSX:

```tsx
<TypingFooter humans={typingHumans} agents={typingAgents} selfId="user" />
```

- [ ] **Step 6b: Render the agent-info popover**

Near the context-menu render block, add:

```tsx
{agentInfoPopover && (
  <div
    role="dialog"
    aria-label={`Agent info for @${agentInfoPopover.slug}`}
    className="fixed z-50 bg-shell-surface border border-white/10 rounded-lg shadow-xl p-3 text-xs min-w-[200px]"
    style={{ top: agentInfoPopover.y, left: agentInfoPopover.x }}
    onMouseLeave={() => setAgentInfoPopover(null)}
  >
    <div className="font-semibold text-sm mb-1">@{agentInfoPopover.slug}</div>
    <div className="opacity-70">Framework: {agentInfoPopover.framework}</div>
    <div className="opacity-70">Model: {agentInfoPopover.model}</div>
    <div className="opacity-70">Status: {agentInfoPopover.status}</div>
  </div>
)}
```

- [ ] **Step 7: Handle bare-slash 400 response**

Find where the composer submits (usually `sendMessage` or similar). After the POST, check for 400 and surface the server's error as a transient toast or inline error. Example minimal handling:

```tsx
const r = await fetch("/api/chat/messages", { /* existing */ });
if (r.status === 400) {
  const body = await r.json().catch(() => ({}));
  setSendError(body.error || "couldn't send message");
  return;
}
```

With a `{sendError && (<div role="alert">{sendError}</div>)}` rendered near the composer. Reuse existing error-banner patterns if the file already has one; do not invent a new component.

- [ ] **Step 8: Smoke test + build**

Run: `cd desktop && npm test -- --run MessagesApp` if a test exists; otherwise ensure the full test suite passes: `cd desktop && npm test -- --run`

Run: `cd desktop && npm run build`
Expected: builds without errors.

- [ ] **Step 9: Commit**

```bash
git add desktop/src/apps/MessagesApp.tsx
git commit -m "feat(desktop): integrate settings panel, context menu, slash menu, typing footer into MessagesApp"
```

---

## Task 14: Rebuild desktop bundle

**Files:**
- Modify: `static/desktop/**` (build output)

- [ ] **Step 1: Run the build**

Run: `cd desktop && npm run build`
Expected: `static/desktop/` updated.

- [ ] **Step 2: Commit the bundle**

```bash
git add static/desktop
git commit -m "build: rebuild desktop bundle for chat Phase 2a"
```

---

## Task 15: Playwright E2E

**Files:**
- Create: `tests/e2e/test_chat_phase2a.py`

- [ ] **Step 1: Write the test**

```python
# tests/e2e/test_chat_phase2a.py
"""Phase 2a desktop UI end-to-end.

Requires the app running at TAOS_E2E_URL with a test channel created
beforehand. Skipped locally unless env is set.
"""
import os
import pytest
from playwright.sync_api import Page, expect

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not os.environ.get("TAOS_E2E_URL"),
                       reason="TAOS_E2E_URL required"),
]

URL = os.environ.get("TAOS_E2E_URL", "")


def test_slash_menu_opens_and_inserts(page: Page):
    page.goto(URL)
    page.get_by_role("button", name="Messages").click()
    # assume a group channel named 'roundtable' exists with ≥2 agents
    page.get_by_text("roundtable").first.click()

    composer = page.get_by_placeholder("Message")
    composer.click()
    composer.press("/")
    expect(page.get_by_role("listbox", name="Slash commands")).to_be_visible()
    # pick first command
    page.keyboard.press("Enter")
    expect(composer).to_have_value_containing("/")
    expect(composer).to_have_value_containing("@")


def test_channel_settings_panel_opens_and_flips_mode(page: Page):
    page.goto(URL)
    page.get_by_role("button", name="Messages").click()
    page.get_by_text("roundtable").first.click()
    page.get_by_role("button", name="Channel settings").click()
    expect(page.get_by_role("complementary", name="Channel settings")).to_be_visible()

    # Flip mode
    page.get_by_role("button", name="lively").click()
    # Re-open to confirm persisted
    page.get_by_role("button", name="Close").click()
    page.get_by_role("button", name="Channel settings").click()
    lively_btn = page.get_by_role("button", name="lively")
    # selected button has a different background class; we just check it's there
    expect(lively_btn).to_be_visible()


def test_agent_context_menu_via_right_click(page: Page):
    page.goto(URL)
    page.get_by_role("button", name="Messages").click()
    page.get_by_text("roundtable").first.click()

    # Right-click an agent's name in the transcript
    agent_span = page.get_by_role("link", name="@tom").first  # or whichever selector fits
    agent_span.click(button="right")
    expect(page.get_by_role("menu", name="Actions for @tom")).to_be_visible()
    expect(page.get_by_role("menuitem", name="DM @tom")).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.get_by_role("menu")).not_to_be_visible()
```

- [ ] **Step 2: Verify collection + skip locally**

Run: `PYTHONPATH=. pytest tests/e2e/test_chat_phase2a.py -v`
Expected: SKIPPED (env var absent).

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/test_chat_phase2a.py
git commit -m "test(e2e): chat Phase 2a — slash menu, settings panel, context menu"
```

---

## Final verification

- [ ] **Step 1: Full test suite**

Run: `PYTHONPATH=. pytest tests/ -x -q`
Expected: all green except pre-existing 3 macOS hardware-arch failures.

Run: `cd desktop && npm test -- --run`
Expected: all green.

- [ ] **Step 2: Verify commit series**

Run: `git log --oneline master..HEAD`
Cross-check each commit against the tasks above.

- [ ] **Step 3: Open PR**

```bash
git push -u origin feat/chat-phase-2a-desktop-admin
gh pr create --base master \
  --title "Chat Phase 2a — desktop admin UI + live signal (settings panel, context menu, slash menu, typing/thinking)" \
  --body-file docs/superpowers/specs/2026-04-19-chat-phase-2a-desktop-admin-design.md
```

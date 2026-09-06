# Agent Browsers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Agent Browsers app — Docker-based persistent browser profiles with CDP screenshots, noVNC in taOS windows, cookie export, and cluster node placement.

**Architecture:** `AgentBrowsersManager` backend (SQLite state + Docker subprocess), FastAPI routes at `/api/agent-browsers/*`, `AgentBrowsersApp.tsx` frontend (card grid + detail panel), `lib/agent-browsers.ts` API helpers.

**Tech Stack:** Python, Docker (subprocess), SQLite, FastAPI, React, TypeScript, Tailwind, Vitest, pytest-asyncio

**Spec:** `docs/superpowers/specs/2026-04-12-agent-browsers-design.md`

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `tinyagentos/agent_browsers.py` | AgentBrowsersManager — profile CRUD, Docker lifecycle, cookie export, screenshots |
| Create | `tests/test_agent_browsers.py` | Backend tests with MOCK Docker backend |
| Create | `tinyagentos/routes/agent_browsers.py` | `/api/agent-browsers/*` REST endpoints |
| Create | `tests/test_routes_agent_browsers.py` | Route-level tests |
| Modify | `tinyagentos/app.py` | Wire AgentBrowsersManager + routes into app |
| Create | `desktop/src/lib/agent-browsers.ts` | TypeScript types + fetch wrappers |
| Create | `desktop/tests/agent-browsers.test.ts` | Frontend API helper tests |
| Create | `desktop/src/apps/AgentBrowsersApp.tsx` | Main app component |
| Modify | `desktop/src/registry/app-registry.ts` | Register agent-browsers entry |

---

## Task 1: AgentBrowsersManager Backend + Tests

**Files:**
- Create: `tinyagentos/agent_browsers.py`
- Create: `tests/test_agent_browsers.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_agent_browsers.py`:

```python
import pytest
import pytest_asyncio
from tinyagentos.agent_browsers import AgentBrowsersManager


@pytest_asyncio.fixture
async def manager(tmp_path):
    mgr = AgentBrowsersManager(db_path=tmp_path / "browsers.db", mock=True)
    await mgr.init()
    yield mgr
    await mgr.close()


@pytest.mark.asyncio
async def test_create_profile(manager):
    profile = await manager.create_profile("default", agent_name="research-agent")
    assert profile["profile_name"] == "default"
    assert profile["agent_name"] == "research-agent"
    assert profile["status"] == "stopped"
    assert profile["node"] == "local"


@pytest.mark.asyncio
async def test_list_profiles(manager):
    await manager.create_profile("work", agent_name="agent-a")
    await manager.create_profile("personal", agent_name="agent-a")
    await manager.create_profile("browse", agent_name="agent-b")
    all_profiles = await manager.list_profiles()
    assert len(all_profiles) == 3
    agent_a = await manager.list_profiles(agent_name="agent-a")
    assert len(agent_a) == 2


@pytest.mark.asyncio
async def test_start_stop_browser(manager):
    profile = await manager.create_profile("test")
    started = await manager.start_browser(profile["id"])
    assert started is True
    updated = await manager.get_profile(profile["id"])
    assert updated["status"] == "running"
    stopped = await manager.stop_browser(profile["id"])
    assert stopped is True
    updated = await manager.get_profile(profile["id"])
    assert updated["status"] == "stopped"


@pytest.mark.asyncio
async def test_one_active_per_agent(manager):
    p1 = await manager.create_profile("first", agent_name="agent-a")
    p2 = await manager.create_profile("second", agent_name="agent-a")
    await manager.start_browser(p1["id"])
    await manager.start_browser(p2["id"])
    p1_updated = await manager.get_profile(p1["id"])
    p2_updated = await manager.get_profile(p2["id"])
    assert p1_updated["status"] == "stopped"
    assert p2_updated["status"] == "running"


@pytest.mark.asyncio
async def test_delete_profile(manager):
    profile = await manager.create_profile("test")
    deleted = await manager.delete_profile(profile["id"])
    assert deleted is True
    assert await manager.get_profile(profile["id"]) is None


@pytest.mark.asyncio
async def test_assign_agent(manager):
    profile = await manager.create_profile("test")
    await manager.assign_agent(profile["id"], "new-agent")
    updated = await manager.get_profile(profile["id"])
    assert updated["agent_name"] == "new-agent"


@pytest.mark.asyncio
async def test_move_to_node(manager):
    profile = await manager.create_profile("test")
    await manager.move_to_node(profile["id"], "worker-1")
    updated = await manager.get_profile(profile["id"])
    assert updated["node"] == "worker-1"


@pytest.mark.asyncio
async def test_get_login_status_mock(manager):
    profile = await manager.create_profile("test")
    status = await manager.get_login_status(profile["id"])
    assert isinstance(status, dict)
    for site in ("x", "github", "youtube", "reddit"):
        assert site in status


@pytest.mark.asyncio
async def test_get_cookies_mock(manager):
    profile = await manager.create_profile("test")
    cookies = await manager.get_cookies(profile["id"], "x.com")
    assert isinstance(cookies, list)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/jay/tinyagentos && python -m pytest tests/test_agent_browsers.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement AgentBrowsersManager**

Create `tinyagentos/agent_browsers.py` with the full implementation. Key points:
- SQLite table `agent_browsers` with columns: id, agent_name, profile_name, node, status, container_id, created_at, updated_at
- `mock=True` mode for tests — no Docker calls, uses mock container IDs
- `start_browser` stops other running containers for the same agent before starting
- `get_cookies` reads Chromium SQLite cookie DB from Docker volume (returns empty list in mock mode)
- `get_login_status` checks for known auth cookies per site (auth_token+ct0 for X, user_session for GitHub, SID for YouTube, reddit_session for Reddit)
- `get_screenshot` returns cached CDP screenshot with 30s TTL (returns minimal PNG stub in mock mode)

- [ ] **Step 4: Run tests**

Run: `cd /home/jay/tinyagentos && python -m pytest tests/test_agent_browsers.py -v`
Expected: all 9 tests PASS

- [ ] **Step 5: Commit**

```bash
git add tinyagentos/agent_browsers.py tests/test_agent_browsers.py
git commit -m "feat(agent-browsers): add AgentBrowsersManager with mock backend and tests"
```

---

## Task 2: Agent Browsers API Routes

**Files:**
- Create: `tinyagentos/routes/agent_browsers.py`
- Create: `tests/test_routes_agent_browsers.py`
- Modify: `tinyagentos/app.py`

- [ ] **Step 1: Write failing route tests**

Create `tests/test_routes_agent_browsers.py` following the `test_knowledge_routes.py` pattern. Test: POST create, GET list, POST start/stop, GET login-status, GET cookies, PUT assign, DELETE profile.

- [ ] **Step 2: Implement routes**

Create `tinyagentos/routes/agent_browsers.py` with FastAPI router — all 12 endpoints from the spec. Use Pydantic models for request bodies.

- [ ] **Step 3: Wire into app.py**

Add to `tinyagentos/app.py`:
- Import and include the agent_browsers router
- Create `AgentBrowsersManager` instance in app lifespan (mock mode auto-detected when Docker is unavailable)
- Store on `app.state.agent_browsers_manager`

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_routes_agent_browsers.py tests/test_agent_browsers.py -v`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add tinyagentos/routes/agent_browsers.py tests/test_routes_agent_browsers.py tinyagentos/app.py
git commit -m "feat(agent-browsers): add API routes and wire into app"
```

---

## Task 3: Frontend API Helpers + Tests

**Files:**
- Create: `desktop/src/lib/agent-browsers.ts`
- Create: `desktop/tests/agent-browsers.test.ts`

- [ ] **Step 1: Write failing tests**

Create `desktop/tests/agent-browsers.test.ts` — mocked fetch tests for: `listProfiles`, `createProfile`, `deleteProfile`, `startBrowser`, `stopBrowser`, `getCookies`, `getLoginStatus`, `assignAgent`, `moveToNode`. Same pattern as `knowledge.test.ts`.

- [ ] **Step 2: Implement types and helpers**

Create `desktop/src/lib/agent-browsers.ts` with:
- Types: `BrowserProfile`, `LoginStatus`, `CookieEntry`
- Functions: all 11 fetch wrappers using the `fetchJson`/`postJson` pattern from `knowledge.ts`

- [ ] **Step 3: Run tests**

Run: `cd /home/jay/tinyagentos/desktop && npx vitest run tests/agent-browsers.test.ts`
Expected: all pass

- [ ] **Step 4: Commit**

```bash
git add desktop/src/lib/agent-browsers.ts desktop/tests/agent-browsers.test.ts
git commit -m "feat(agent-browsers): add frontend API types and helpers with tests"
```

---

## Task 4: AgentBrowsersApp Component + Registration

**Files:**
- Create: `desktop/src/apps/AgentBrowsersApp.tsx`
- Modify: `desktop/src/registry/app-registry.ts`

- [ ] **Step 1: Create the component**

Two-panel layout (card grid + detail panel), following existing app patterns.

**Card grid:** Profile cards with status badge, agent name, node badge, login status dots, start/stop toggle. "+ New Profile" button.

**Detail panel:** Profile header, screenshot preview area, login status list, action buttons (Start/Stop, Connect via noVNC in taOS window, Assign agent, Move to node, Delete container, Delete data with confirmation).

**Create form:** Inline form with profile name input, agent dropdown (from /api/agents), node dropdown.

noVNC "Connect" opens a new taOS window (not external popup) containing the browser stream.

- [ ] **Step 2: Register the app**

Add to `app-registry.ts`:
```ts
{ id: "agent-browsers", name: "Browsers", icon: "globe", category: "platform", component: () => import("@/apps/AgentBrowsersApp").then((m) => ({ default: m.AgentBrowsersApp })), defaultSize: { w: 1000, h: 650 }, minSize: { w: 550, h: 400 }, singleton: true, pinned: false, launchpadOrder: 16 },
```

- [ ] **Step 3: Build + test**

Run: `cd /home/jay/tinyagentos/desktop && npx tsc --noEmit && npm test`

- [ ] **Step 4: Commit**

```bash
git add desktop/src/apps/AgentBrowsersApp.tsx desktop/src/registry/app-registry.ts
git commit -m "feat(agent-browsers): add Browsers app with full UI"
```

---

## Task 5: Manual Testing

- [ ] Open Browsers app from Launchpad
- [ ] Create a profile, verify card appears
- [ ] Start/stop (mock mode), verify state transitions
- [ ] Assign agent, verify update
- [ ] Delete profile, verify removal
- [ ] Test mobile layout
- [ ] Verify ARIA and keyboard nav

---

## TDD Summary

| Task | Tests | What it delivers |
|------|-------|------------------|
| 1 | 9 pytest | Backend manager with mock Docker |
| 2 | Route tests | REST API |
| 3 | Vitest | Frontend API layer |
| 4 | Build check | Full app UI |
| 5 | Manual | Polish |

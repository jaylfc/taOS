# App Streaming Foundation Implementation Plan

**Status:** In progress — tracked by #22. Session store, streaming pages, user workspace, agent-bridge, expert agents, and 12 app manifests have landed; remaining work is the containerised streaming runtime itself.


**Goal:** Build the foundation layer for containerised app streaming: session manager, streaming page with sidebar chat, user workspace file browser, WebSocket proxy, and the agent-bridge daemon spec.

**Architecture:** New `StreamingSessionStore` (BaseStore pattern) manages sessions. New routes module `routes/streaming.py` handles launch/stop/status/proxy. The streaming page uses a split layout: KasmVNC canvas on the left, agent chat on the right. User workspace at `/workspace` provides NAS-like file management. The agent-bridge is a Python daemon that runs inside app containers (spec + code in this plan, container integration in Plan 2).

**Tech Stack:** Python 3.10+, FastAPI, htmx, Pico CSS, aiosqlite, websockets, httpx

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `tinyagentos/streaming.py` | StreamingSessionStore (SQLite) |
| Create | `tinyagentos/routes/streaming.py` | Launch, stop, list, status endpoints + pages |
| Create | `tinyagentos/routes/user_workspace.py` | User workspace file browser routes |
| Create | `tinyagentos/agent_bridge.py` | Agent-bridge daemon (runs inside containers) |
| Create | `tinyagentos/templates/streaming_apps.html` | Available apps + active sessions page |
| Create | `tinyagentos/templates/streaming_app.html` | Split layout: app stream + chat sidebar |
| Create | `tinyagentos/templates/user_workspace.html` | NAS-like file browser page |
| Modify | `tinyagentos/app.py` | Register new stores + routes |
| Modify | `tinyagentos/templates/base.html` | Add Workspace + Apps nav links |
| Modify | `tests/conftest.py` | Init/close streaming store in fixtures |
| Create | `tests/test_streaming.py` | Session store tests |
| Create | `tests/test_routes_streaming.py` | Streaming route tests |
| Create | `tests/test_routes_user_workspace.py` | Workspace route tests |
| Create | `tests/test_agent_bridge.py` | Agent-bridge unit tests |

---

### Task 1: StreamingSessionStore

**Files:**
- Create: `tinyagentos/streaming.py`
- Test: `tests/test_streaming.py`

- [ ] **Step 1: Write tests**

Create `tests/test_streaming.py` with tests for: create_session, get_session, update_status, list_sessions, list_active_only, delete_session, swap_agent, touch_activity. Each test creates a fresh store in tmp_path, inits it, runs assertions, and closes. 8 tests total.

- [ ] **Step 2: Implement StreamingSessionStore**

Create `tinyagentos/streaming.py` with a `StreamingSessionStore(BaseStore)` class. Schema: `streaming_sessions` table with columns: session_id (TEXT PK), app_id, agent_name, agent_type, worker_name, container_id, status, started_at, last_activity. Methods: create_session (generates UUID hex[:12]), get_session, list_sessions (with active_only filter), update_status, swap_agent, touch_activity, delete_session.

- [ ] **Step 3: Run tests and verify**

Run: `.venv/bin/python -m pytest tests/test_streaming.py -v`
Expected: 8 PASS

- [ ] **Step 4: Commit**

```bash
git add tinyagentos/streaming.py tests/test_streaming.py
git commit -m "feat: add StreamingSessionStore for app streaming session management"
```

---

### Task 2: Streaming Routes + Pages

**Files:**
- Create: `tinyagentos/routes/streaming.py`
- Create: `tinyagentos/templates/streaming_apps.html`
- Create: `tinyagentos/templates/streaming_app.html`
- Modify: `tinyagentos/app.py`
- Modify: `tests/conftest.py`
- Test: `tests/test_routes_streaming.py`

- [ ] **Step 1: Write route tests**

Create `tests/test_routes_streaming.py` with tests for: GET /streaming (page), GET /api/streaming-apps (list), GET /api/streaming-apps/sessions (empty list), GET /api/streaming-apps/sessions/{id} (404), POST /api/streaming-apps/sessions/{id}/stop (404), POST /api/streaming-apps/sessions/{id}/swap-agent (404). Uses the `client` fixture from conftest.

- [ ] **Step 2: Create streaming routes**

Create `tinyagentos/routes/streaming.py` with endpoints:
- `GET /streaming` — HTML page listing available apps and active sessions
- `GET /api/streaming-apps` — list streaming-type apps from registry
- `GET /api/streaming-apps/sessions` — list sessions (with ?active_only param)
- `GET /api/streaming-apps/sessions/{session_id}` — get session
- `POST /api/streaming-apps/launch` — create session (body: app_id, agent_name, agent_type)
- `POST /api/streaming-apps/sessions/{session_id}/stop` — stop session
- `POST /api/streaming-apps/sessions/{session_id}/swap-agent` — hot-swap agent
- `GET /app/{session_id}` — streaming app page (split view)

- [ ] **Step 3: Create streaming_apps.html template**

Extends base.html. Shows active sessions (htmx polled) and available apps list.

- [ ] **Step 4: Create streaming_app.html template**

Extends base.html. Split layout: left panel is the KasmVNC canvas area (placeholder for Plan 2, shows "Connecting..." with session info), right panel is the agent chat sidebar with: agent selector dropdown, chat messages area, control buttons (Screenshot, Undo, Computer Use toggle), and chat input form. All HTML escaping uses a JS `escapeHtml()` function. Chat input is local-only for now (WebSocket integration in Plan 2).

- [ ] **Step 5: Register in app.py**

Add `StreamingSessionStore` import, create instance at `data_dir / "streaming.db"`, init in lifespan, set on app.state, close in lifespan cleanup. Also add to eager state section. Register the streaming router. Update conftest.py with streaming store init/close in both client fixtures.

- [ ] **Step 6: Run tests**

Run: `.venv/bin/python -m pytest tests/test_routes_streaming.py tests/test_streaming.py -v`
Expected: 14 PASS (8 store + 6 route)

- [ ] **Step 7: Commit**

```bash
git add tinyagentos/streaming.py tinyagentos/routes/streaming.py tinyagentos/templates/streaming_apps.html tinyagentos/templates/streaming_app.html tinyagentos/app.py tests/conftest.py tests/test_streaming.py tests/test_routes_streaming.py
git commit -m "feat: add app streaming foundation — session store, routes, and split-view streaming page"
```

---

### Task 3: User Workspace File Browser

**Files:**
- Create: `tinyagentos/routes/user_workspace.py`
- Create: `tinyagentos/templates/user_workspace.html`
- Modify: `tinyagentos/templates/base.html`
- Modify: `tinyagentos/app.py`
- Test: `tests/test_routes_user_workspace.py`

- [ ] **Step 1: Write tests**

Create `tests/test_routes_user_workspace.py` with tests for: GET /workspace (page), GET /api/workspace/files (empty), POST /api/workspace/files/upload, upload then list, POST /api/workspace/mkdir, list subdirectory, DELETE /api/workspace/files/{path}, delete nonexistent (404), path traversal blocked (400), GET /api/workspace/stats. 10 tests total.

- [ ] **Step 2: Create user workspace routes**

Create `tinyagentos/routes/user_workspace.py` with:
- `_get_workspace(request)` helper returning `data_dir / "workspace"` (creates if needed)
- `_resolve_safe(workspace, subpath)` helper rejecting path traversal
- `GET /workspace` — HTML page
- `GET /api/workspace/files` — list files/dirs (with ?path= subdirectory support)
- `POST /api/workspace/files/upload` — upload file (with ?path= subdirectory)
- `POST /api/workspace/mkdir` — create directory
- `DELETE /api/workspace/files/{file_path:path}` — delete file or directory
- `GET /api/workspace/stats` — total files + total size

- [ ] **Step 3: Create user_workspace.html template**

Extends base.html. Upload button, New Folder button, stats display, file list (htmx loaded). Uses JS for upload (FormData fetch) and mkdir (prompt + fetch).

- [ ] **Step 4: Add nav links**

In base.html, add Workspace and Apps links to BOTH nav lists, after the Store link:
```html
<li><a href="/workspace" ...>Workspace</a></li>
<li><a href="/streaming" ...>Apps</a></li>
```

- [ ] **Step 5: Register routes in app.py**

```python
from tinyagentos.routes.user_workspace import router as user_workspace_router
app.include_router(user_workspace_router)
```

- [ ] **Step 6: Run tests**

Run: `.venv/bin/python -m pytest tests/test_routes_user_workspace.py -v`
Expected: 10 PASS

- [ ] **Step 7: Commit**

```bash
git add tinyagentos/routes/user_workspace.py tinyagentos/templates/user_workspace.html tinyagentos/templates/base.html tinyagentos/app.py tests/test_routes_user_workspace.py
git commit -m "feat: add user workspace file browser with upload, mkdir, delete, and stats"
```

---

### Task 4: Agent Bridge Daemon

**Files:**
- Create: `tinyagentos/agent_bridge.py`
- Test: `tests/test_agent_bridge.py`

- [ ] **Step 1: Write tests**

Create `tests/test_agent_bridge.py` with tests using `create_bridge_app()` factory + httpx ASGITransport: health endpoint, screenshot (expects error without display), exec command ("echo hello"), exec with timeout, keyboard (expects error without xdotool), computer-use toggle (get/set/get), agent/current, files/list. 8 tests total.

- [ ] **Step 2: Implement agent bridge**

Create `tinyagentos/agent_bridge.py` with `create_bridge_app(app_id, mcp_server)` factory that returns a FastAPI app. Internal state dict tracks app_id, mcp_server, agent_name, agent_type, computer_use toggle. Endpoints:
- `GET /health` — status + app info
- `GET /mcp/capabilities` — placeholder (Plan 2 connects real MCP)
- `POST /mcp/tool` — placeholder
- `POST /exec` — asyncio subprocess with timeout
- `POST /files/list`, `/files/read`, `/files/write`, `/files/batch`
- `GET /screenshot` — scrot to /tmp/screenshot.png, return base64
- `POST /keyboard` — xdotool key injection
- `POST /mouse` — xdotool mousemove + click
- `POST /type` — xdotool type
- `GET /computer-use`, `POST /computer-use` — toggle state
- `GET /agent/current`, `POST /agent/swap` — agent identity

Note: exec uses `asyncio.create_subprocess_shell` intentionally since this daemon runs inside isolated containers and the purpose is to execute arbitrary commands on behalf of the agent.

- [ ] **Step 3: Run tests**

Run: `.venv/bin/python -m pytest tests/test_agent_bridge.py -v`
Expected: 8 PASS

- [ ] **Step 4: Commit**

```bash
git add tinyagentos/agent_bridge.py tests/test_agent_bridge.py
git commit -m "feat: add agent-bridge daemon for container-side app control"
```

---

### Task 5: Full Test Suite + README Update

- [ ] **Step 1: Run full test suite**

Run: `.venv/bin/python -m pytest tests/ --ignore=tests/e2e --tb=short -q`
Expected: ~830+ tests pass

- [ ] **Step 2: Fix any failures from app.py changes**

- [ ] **Step 3: Update README**

Update test count. Add to In Progress:
```
- [ ] Containerised app streaming (#22) — Phase 1 foundation complete
```

- [ ] **Step 4: Commit and push**

```bash
git add README.md
git commit -m "docs: update README with app streaming progress and test count"
git push
```

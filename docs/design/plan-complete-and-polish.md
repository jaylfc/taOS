# TinyAgentOS Complete & Polish Implementation Plan

**Status:** Implemented — this plan has landed; see the feature on `master` for the current state.


**Goal:** Fill all gaps identified in the codebase audit — background deploy, 3 missing UI pages, event system, store CRUD gaps, LLM proxy status card, and tests for 10 untested route modules.

**Architecture:** All changes follow existing patterns (FastAPI routes, htmx partials, Pico CSS, SQLite via BaseStore, pytest with conftest fixtures). No new dependencies, no architectural changes.

**Tech Stack:** Python 3.10+, FastAPI, htmx, Pico CSS, aiosqlite, pytest, httpx AsyncClient

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Modify | `tinyagentos/notifications.py` | Add `emit_event()` + notification_prefs table |
| Modify | `tinyagentos/routes/agents.py:120-162` | Background deploy with asyncio.create_task |
| Modify | `tinyagentos/routes/dashboard.py` | Add cluster summary API endpoint |
| Modify | `tinyagentos/routes/settings.py` | Add LLM proxy status endpoint |
| Modify | `tinyagentos/routes/shared_folders.py` | Add HTML page route |
| Modify | `tinyagentos/routes/channel_hub.py` | Add HTML page route |
| Modify | `tinyagentos/routes/conversion.py` | Add HTML page route |
| Modify | `tinyagentos/channels.py` | Add `get()` and `update()` methods |
| Modify | `tinyagentos/agent_messages.py` | Add `delete()` and `search()` methods |
| Modify | `tinyagentos/app.py:120-125` | Add deploy_tasks dict to state |
| Create | `tinyagentos/templates/shared_folders.html` | Shared folders management page |
| Create | `tinyagentos/templates/channel_hub.html` | Channel Hub management page |
| Create | `tinyagentos/templates/conversions.html` | Model conversion status page |
| Create | `tests/test_routes_settings.py` | Settings route tests |
| Create | `tests/test_routes_auth.py` | Auth route tests |
| Create | `tests/test_routes_channels.py` | Channel route tests |
| Create | `tests/test_routes_channel_hub.py` | Channel Hub route tests |
| Create | `tests/test_routes_conversion.py` | Conversion route tests |
| Create | `tests/test_routes_notifications.py` | Notification route tests |
| Create | `tests/test_routes_shared_folders.py` | Shared folders route tests |
| Create | `tests/test_routes_training.py` | Training route tests |
| Create | `tests/test_routes_workspace.py` | Workspace route tests |
| Create | `tests/test_routes_import_data.py` | Import data route tests |

---

### Task 1: Event System — NotificationStore.emit_event()

**Files:**
- Modify: `tinyagentos/notifications.py`
- Test: `tests/test_routes_notifications.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_routes_notifications.py
import pytest
from tinyagentos.notifications import NotificationStore


class TestNotificationStore:
    @pytest.mark.asyncio
    async def test_emit_event_stores_notification(self, tmp_path):
        store = NotificationStore(tmp_path / "notif.db")
        await store.init()
        try:
            await store.emit_event("worker.join", "Worker joined", "worker-1 connected", level="info")
            items = await store.list(limit=10)
            assert len(items) == 1
            assert items[0]["title"] == "Worker joined"
            assert items[0]["source"] == "worker.join"
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_emit_event_respects_muted_prefs(self, tmp_path):
        store = NotificationStore(tmp_path / "notif.db")
        await store.init()
        try:
            await store.set_event_muted("worker.join", True)
            await store.emit_event("worker.join", "Worker joined", "worker-1 connected")
            items = await store.list(limit=10)
            assert len(items) == 0
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_get_event_prefs(self, tmp_path):
        store = NotificationStore(tmp_path / "notif.db")
        await store.init()
        try:
            prefs = await store.get_event_prefs()
            assert isinstance(prefs, list)
            await store.set_event_muted("backend.down", True)
            prefs = await store.get_event_prefs()
            muted = [p for p in prefs if p["event_type"] == "backend.down"]
            assert len(muted) == 1
            assert muted[0]["muted"] is True
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_emit_unmuted_event_passes_through(self, tmp_path):
        store = NotificationStore(tmp_path / "notif.db")
        await store.init()
        try:
            await store.set_event_muted("worker.join", True)
            await store.emit_event("backend.up", "Backend online", "test-backend connected")
            items = await store.list(limit=10)
            assert len(items) == 1
            assert items[0]["title"] == "Backend online"
        finally:
            await store.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_routes_notifications.py -v`
Expected: FAIL — `emit_event` not defined

- [ ] **Step 3: Implement emit_event and notification prefs**

In `tinyagentos/notifications.py`, add to `NOTIF_SCHEMA` (append after the index):

```python
NOTIF_SCHEMA = """
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER NOT NULL,
    level TEXT NOT NULL,
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    read INTEGER NOT NULL DEFAULT 0,
    source TEXT
);
CREATE INDEX IF NOT EXISTS idx_notif_ts ON notifications(timestamp DESC);
CREATE TABLE IF NOT EXISTS notification_prefs (
    event_type TEXT PRIMARY KEY,
    muted INTEGER NOT NULL DEFAULT 0
);
"""
```

Add these methods to `NotificationStore`:

```python
EVENT_TYPES = [
    "worker.join", "worker.leave", "backend.up", "backend.down",
    "training.complete", "training.failed", "app.installed", "app.failed",
]

async def emit_event(self, event_type: str, title: str, message: str,
                     level: str = "info") -> None:
    """Emit a typed event — stores notification + fires webhooks unless muted."""
    if await self._is_event_muted(event_type):
        return
    await self.add(title, message, level=level, source=event_type)

async def _is_event_muted(self, event_type: str) -> bool:
    async with self._db.execute(
        "SELECT muted FROM notification_prefs WHERE event_type = ?", (event_type,)
    ) as cursor:
        row = await cursor.fetchone()
    return bool(row and row[0])

async def set_event_muted(self, event_type: str, muted: bool) -> None:
    await self._db.execute(
        "INSERT OR REPLACE INTO notification_prefs (event_type, muted) VALUES (?, ?)",
        (event_type, int(muted)),
    )
    await self._db.commit()

async def get_event_prefs(self) -> list[dict]:
    prefs = {}
    async with self._db.execute("SELECT event_type, muted FROM notification_prefs") as cursor:
        for row in await cursor.fetchall():
            prefs[row[0]] = bool(row[1])
    return [
        {"event_type": et, "muted": prefs.get(et, False)}
        for et in self.EVENT_TYPES
    ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_routes_notifications.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Wire notification cleanup in app lifespan**

In `tinyagentos/app.py`, after `await monitor.start()` (line 132), add:

```python
        # Schedule daily notification cleanup if not already scheduled
        existing = await scheduler.list_tasks()
        if not any(t.get("name") == "notif-cleanup" for t in existing):
            await scheduler.add_task(
                name="notif-cleanup",
                schedule="0 3 * * *",
                command="cleanup_notifications",
                agent_name=None,
            )
```

- [ ] **Step 6: Commit**

```bash
git add tinyagentos/notifications.py tinyagentos/app.py tests/test_routes_notifications.py
git commit -m "feat: add event system with mutable notification preferences"
```

---

### Task 2: Background Deploy

**Files:**
- Modify: `tinyagentos/routes/agents.py:120-162`
- Modify: `tinyagentos/app.py` (add `deploy_tasks` to state)
- Test: `tests/test_deployer.py` (add background deploy test)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_deployer.py`:

```python
class TestBackgroundDeploy:
    @pytest.mark.asyncio
    async def test_deploy_endpoint_returns_immediately(self, client):
        """POST /api/agents/deploy should return immediately with status=deploying."""
        resp = await client.post("/api/agents/deploy", json={
            "name": "bg-test",
            "framework": "none",
            "color": "#aabbcc",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "deploying"
        assert data["name"] == "bg-test"

    @pytest.mark.asyncio
    async def test_deploy_status_endpoint(self, client):
        """GET /api/agents/{name}/deploy-status returns task state."""
        # First trigger a deploy
        await client.post("/api/agents/deploy", json={
            "name": "status-test",
            "framework": "none",
        })
        resp = await client.get("/api/agents/status-test/deploy-status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] in ("deploying", "success", "failed")

    @pytest.mark.asyncio
    async def test_deploy_status_not_found(self, client):
        resp = await client.get("/api/agents/nonexistent/deploy-status")
        assert resp.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_deployer.py::TestBackgroundDeploy -v`
Expected: FAIL — endpoint returns old synchronous response

- [ ] **Step 3: Add deploy_tasks to app.state**

In `tinyagentos/app.py`, after line 120 (`app.state.channel_hub_connectors = {}`), add:

```python
        app.state.deploy_tasks = {}
```

Also add in the eager state section (after line 190):

```python
    app.state.deploy_tasks = {}
```

- [ ] **Step 4: Rewrite deploy endpoint for background execution**

Replace the `deploy_agent_endpoint` function in `tinyagentos/routes/agents.py`:

```python
@router.post("/api/agents/deploy")
async def deploy_agent_endpoint(request: Request, body: DeployAgentRequest):
    """Deploy a new agent — kicks off background LXC container creation."""
    import asyncio
    config = request.app.state.config
    name_error = validate_agent_name(body.name)
    if name_error:
        return JSONResponse({"error": name_error}, status_code=400)
    if body.framework != "none":
        registry = request.app.state.registry
        known = {a.id for a in registry.list_available(type_filter="agent-framework")}
        if body.framework not in known:
            return JSONResponse({"error": f"Unknown framework '{body.framework}'. Available: {sorted(known)}"}, status_code=400)
    if find_agent(config, body.name):
        return JSONResponse({"error": f"Agent '{body.name}' already exists"}, status_code=409)

    # Add agent to config with deploying status
    agent_entry = {
        "name": body.name,
        "host": "",
        "qmd_url": "",
        "color": body.color,
        "status": "deploying",
    }
    config.agents.append(agent_entry)
    await save_config_locked(config, config.config_path)

    # Track deploy task
    deploy_tasks = request.app.state.deploy_tasks
    deploy_tasks[body.name] = {"status": "deploying", "step": "starting", "error": None, "result": None}

    # Find rkllama URL
    rkllama_url = "http://localhost:7833"
    for b in config.backends:
        if b.get("type") == "rkllama":
            rkllama_url = b["url"]
            break

    async def _background_deploy():
        from tinyagentos.deployer import deploy_agent, DeployRequest
        try:
            result = await deploy_agent(DeployRequest(
                name=body.name,
                framework=body.framework,
                model=body.model,
                color=body.color,
                memory_limit=body.memory_limit,
                cpu_limit=body.cpu_limit,
                rkllama_url=rkllama_url,
            ))
            if result["success"]:
                agent_entry["host"] = result.get("ip", "")
                agent_entry["qmd_url"] = result.get("qmd_url", "")
                agent_entry["status"] = "running"
                deploy_tasks[body.name] = {"status": "success", "step": "done", "error": None, "result": result}
            else:
                agent_entry["status"] = "failed"
                deploy_tasks[body.name] = {"status": "failed", "step": "error", "error": result.get("error"), "result": result}
            await save_config_locked(config, config.config_path)
        except Exception as exc:
            agent_entry["status"] = "failed"
            deploy_tasks[body.name] = {"status": "failed", "step": "error", "error": str(exc), "result": None}
            await save_config_locked(config, config.config_path)

    asyncio.create_task(_background_deploy())
    return {"status": "deploying", "name": body.name}


@router.get("/api/agents/{name}/deploy-status")
async def deploy_status(request: Request, name: str):
    """Check the status of a background deployment."""
    deploy_tasks = request.app.state.deploy_tasks
    task = deploy_tasks.get(name)
    if not task:
        return JSONResponse({"error": "No deploy task found"}, status_code=404)
    return task
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_deployer.py -v`
Expected: PASS (all tests including new ones)

- [ ] **Step 6: Commit**

```bash
git add tinyagentos/routes/agents.py tinyagentos/app.py tests/test_deployer.py
git commit -m "feat: background agent deployment with status polling"
```

---

### Task 3: Cluster Summary Dashboard Endpoint

**Files:**
- Modify: `tinyagentos/routes/dashboard.py`
- Test: `tests/test_routes_dashboard.py` (existing file — add test)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_routes_dashboard.py`:

```python
class TestClusterSummary:
    @pytest.mark.asyncio
    async def test_cluster_summary_endpoint(self, client):
        resp = await client.get("/api/dashboard/cluster-summary")
        assert resp.status_code == 200
        data = resp.json()
        assert "workers" in data
        assert "online" in data
        assert "total_ram_gb" in data
        assert "total_vram_gb" in data
        assert data["workers"] == 0  # no workers in test
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_routes_dashboard.py::TestClusterSummary -v`
Expected: FAIL — 404 Not Found

- [ ] **Step 3: Add cluster summary endpoint**

In `tinyagentos/routes/dashboard.py`, after the `_get_cluster_stats` function:

```python
@router.get("/api/dashboard/cluster-summary")
async def cluster_summary(request: Request):
    """Cluster KPIs for the dashboard."""
    stats = _get_cluster_stats(request)
    return {
        "workers": stats["workers"],
        "online": stats["online"],
        "total_ram_gb": round(stats["total_ram_mb"] / 1024, 1),
        "total_vram_gb": round(stats["total_vram_mb"] / 1024, 1),
        "capabilities": stats["capabilities"],
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_routes_dashboard.py::TestClusterSummary -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tinyagentos/routes/dashboard.py tests/test_routes_dashboard.py
git commit -m "feat: add cluster summary API endpoint for dashboard KPIs"
```

---

### Task 4: Store CRUD Gaps — ChannelStore + AgentMessageStore

**Files:**
- Modify: `tinyagentos/channels.py`
- Modify: `tinyagentos/agent_messages.py`
- Test: `tests/test_routes_channels.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_routes_channels.py
import pytest
from tinyagentos.channels import ChannelStore
from tinyagentos.agent_messages import AgentMessageStore


class TestChannelStore:
    @pytest.mark.asyncio
    async def test_add_and_list(self, tmp_path):
        store = ChannelStore(tmp_path / "ch.db")
        await store.init()
        try:
            row_id = await store.add("agent-1", "telegram", {"bot_token_secret": "tok"})
            assert row_id > 0
            channels = await store.list_for_agent("agent-1")
            assert len(channels) == 1
            assert channels[0]["type"] == "telegram"
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_get_by_id(self, tmp_path):
        store = ChannelStore(tmp_path / "ch.db")
        await store.init()
        try:
            row_id = await store.add("agent-1", "discord", {"guild_id": "123"})
            ch = await store.get(row_id)
            assert ch is not None
            assert ch["type"] == "discord"
            assert ch["config"]["guild_id"] == "123"
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_get_nonexistent(self, tmp_path):
        store = ChannelStore(tmp_path / "ch.db")
        await store.init()
        try:
            ch = await store.get(999)
            assert ch is None
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_update_config(self, tmp_path):
        store = ChannelStore(tmp_path / "ch.db")
        await store.init()
        try:
            row_id = await store.add("agent-1", "slack", {"channel": "general"})
            await store.update(row_id, {"channel": "random", "extra": "val"})
            ch = await store.get(row_id)
            assert ch["config"]["channel"] == "random"
            assert ch["config"]["extra"] == "val"
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_toggle(self, tmp_path):
        store = ChannelStore(tmp_path / "ch.db")
        await store.init()
        try:
            row_id = await store.add("agent-1", "email", {})
            await store.toggle(row_id, False)
            ch = await store.get(row_id)
            assert ch["enabled"] is False
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_remove(self, tmp_path):
        store = ChannelStore(tmp_path / "ch.db")
        await store.init()
        try:
            await store.add("agent-1", "telegram", {})
            removed = await store.remove("agent-1", "telegram")
            assert removed is True
            channels = await store.list_for_agent("agent-1")
            assert len(channels) == 0
        finally:
            await store.close()


class TestAgentMessageStoreExtras:
    @pytest.mark.asyncio
    async def test_delete_message(self, tmp_path):
        store = AgentMessageStore(tmp_path / "msg.db")
        await store.init()
        try:
            msg_id = await store.send("a", "b", "hello")
            deleted = await store.delete(msg_id)
            assert deleted is True
            msgs = await store.get_messages("a")
            assert len(msgs) == 0
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self, tmp_path):
        store = AgentMessageStore(tmp_path / "msg.db")
        await store.init()
        try:
            deleted = await store.delete(999)
            assert deleted is False
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_search_messages(self, tmp_path):
        store = AgentMessageStore(tmp_path / "msg.db")
        await store.init()
        try:
            await store.send("a", "b", "hello world")
            await store.send("a", "b", "goodbye world")
            await store.send("c", "d", "something else")
            results = await store.search("hello", agent_name="a")
            assert len(results) == 1
            assert "hello" in results[0]["message"]
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_search_all_agents(self, tmp_path):
        store = AgentMessageStore(tmp_path / "msg.db")
        await store.init()
        try:
            await store.send("a", "b", "world peace")
            await store.send("c", "d", "world war")
            results = await store.search("world")
            assert len(results) == 2
        finally:
            await store.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_routes_channels.py -v`
Expected: FAIL — `get`, `update`, `delete`, `search` not defined

- [ ] **Step 3: Add get() and update() to ChannelStore**

In `tinyagentos/channels.py`, add after the `list_all` method:

```python
async def get(self, channel_id: int) -> dict | None:
    """Get a single channel by ID."""
    async with self._db.execute(
        "SELECT id, agent_name, type, config, enabled FROM channels WHERE id = ?",
        (channel_id,),
    ) as cursor:
        row = await cursor.fetchone()
    if not row:
        return None
    return {
        "id": row[0],
        "agent_name": row[1],
        "type": row[2],
        "config": json.loads(row[3]),
        "enabled": bool(row[4]),
        **CHANNEL_TYPES.get(row[2], {}),
    }

async def update(self, channel_id: int, config: dict) -> None:
    """Update a channel's config JSON."""
    await self._db.execute(
        "UPDATE channels SET config = ? WHERE id = ?",
        (json.dumps(config), channel_id),
    )
    await self._db.commit()
```

- [ ] **Step 4: Add delete() and search() to AgentMessageStore**

In `tinyagentos/agent_messages.py`, add after the `unread_count` method:

```python
async def delete(self, message_id: int) -> bool:
    """Delete a single message by ID. Returns True if deleted."""
    cursor = await self._db.execute(
        "DELETE FROM agent_messages WHERE id = ?", (message_id,)
    )
    await self._db.commit()
    return cursor.rowcount > 0

async def search(self, query: str, agent_name: str | None = None,
                 limit: int = 50) -> list[dict]:
    """Search messages by content. Optionally filter by agent."""
    pattern = f"%{query}%"
    if agent_name:
        sql = """SELECT id, from_agent, to_agent, message, tool_calls, tool_results,
                        reasoning, depth, metadata, timestamp, read
                 FROM agent_messages
                 WHERE (from_agent = ? OR to_agent = ?) AND message LIKE ?
                 ORDER BY timestamp DESC LIMIT ?"""
        params = (agent_name, agent_name, pattern, limit)
    else:
        sql = """SELECT id, from_agent, to_agent, message, tool_calls, tool_results,
                        reasoning, depth, metadata, timestamp, read
                 FROM agent_messages
                 WHERE message LIKE ?
                 ORDER BY timestamp DESC LIMIT ?"""
        params = (pattern, limit)
    async with self._db.execute(sql, params) as cursor:
        rows = await cursor.fetchall()
    return [self._format_message(r) for r in rows]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_routes_channels.py -v`
Expected: PASS (10 tests)

- [ ] **Step 6: Commit**

```bash
git add tinyagentos/channels.py tinyagentos/agent_messages.py tests/test_routes_channels.py
git commit -m "feat: add get/update to ChannelStore, delete/search to AgentMessageStore"
```

---

### Task 5: LLM Proxy Status Card

**Files:**
- Modify: `tinyagentos/routes/settings.py`
- Test: `tests/test_routes_settings.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_routes_settings.py
import pytest


class TestSettingsRoutes:
    @pytest.mark.asyncio
    async def test_settings_page(self, client):
        resp = await client.get("/settings")
        assert resp.status_code == 200
        assert b"Settings" in resp.content

    @pytest.mark.asyncio
    async def test_get_config(self, client):
        resp = await client.get("/api/config")
        assert resp.status_code == 200
        data = resp.json()
        assert "yaml" in data

    @pytest.mark.asyncio
    async def test_get_storage(self, client):
        resp = await client.get("/api/settings/storage")
        assert resp.status_code == 200
        data = resp.json()
        assert "storage" in data

    @pytest.mark.asyncio
    async def test_save_platform_settings(self, client):
        resp = await client.put("/api/settings/platform", json={
            "poll_interval": 60,
            "retention_days": 14,
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "saved"

    @pytest.mark.asyncio
    async def test_llm_proxy_status(self, client):
        resp = await client.get("/api/settings/llm-proxy")
        assert resp.status_code == 200
        data = resp.json()
        assert "running" in data
        assert "port" in data

    @pytest.mark.asyncio
    async def test_webhooks_crud(self, client):
        # Add
        resp = await client.post("/api/settings/webhooks", json={
            "url": "https://example.com/hook",
            "type": "generic",
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "added"
        # List
        resp = await client.get("/api/settings/webhooks")
        assert resp.status_code == 200
        assert len(resp.json()["webhooks"]) == 1
        # Remove
        resp = await client.delete("/api/settings/webhooks/0")
        assert resp.status_code == 200
        assert resp.json()["status"] == "removed"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_routes_settings.py -v`
Expected: FAIL on `test_llm_proxy_status` — 404

- [ ] **Step 3: Add LLM proxy status endpoint**

In `tinyagentos/routes/settings.py`, add:

```python
@router.get("/api/settings/llm-proxy")
async def llm_proxy_status(request: Request):
    """Return LLM proxy status for the settings page."""
    proxy = request.app.state.llm_proxy
    return {
        "running": proxy.is_running() if hasattr(proxy, "is_running") else False,
        "port": proxy.port if hasattr(proxy, "port") else 4000,
        "backends": len(request.app.state.config.backends),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_routes_settings.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add tinyagentos/routes/settings.py tests/test_routes_settings.py
git commit -m "feat: add LLM proxy status endpoint + settings route tests"
```

---

### Task 6: Shared Folders UI Page

**Files:**
- Create: `tinyagentos/templates/shared_folders.html`
- Modify: `tinyagentos/routes/shared_folders.py`
- Test: `tests/test_routes_shared_folders.py`

- [ ] **Step 1: Write the tests**

```python
# tests/test_routes_shared_folders.py
import pytest


class TestSharedFoldersRoutes:
    @pytest.mark.asyncio
    async def test_shared_folders_page(self, client):
        resp = await client.get("/shared-folders")
        assert resp.status_code == 200
        assert b"Shared Folders" in resp.content

    @pytest.mark.asyncio
    async def test_create_folder(self, client):
        resp = await client.post("/api/shared-folders", json={
            "name": "team-docs",
            "description": "Shared documentation",
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "created"

    @pytest.mark.asyncio
    async def test_list_folders(self, client):
        await client.post("/api/shared-folders", json={"name": "list-test"})
        resp = await client.get("/api/shared-folders")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    @pytest.mark.asyncio
    async def test_delete_folder(self, client):
        resp = await client.post("/api/shared-folders", json={"name": "del-test"})
        folder_id = resp.json()["id"]
        resp = await client.delete(f"/api/shared-folders/{folder_id}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"

    @pytest.mark.asyncio
    async def test_grant_access(self, client):
        resp = await client.post("/api/shared-folders", json={"name": "access-test"})
        folder_id = resp.json()["id"]
        resp = await client.post(f"/api/shared-folders/{folder_id}/access", json={
            "agent_name": "test-agent",
            "permission": "readwrite",
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "granted"
```

- [ ] **Step 2: Create the template**

```html
<!-- tinyagentos/templates/shared_folders.html -->
{% extends "base.html" %}
{% block title %}Shared Folders — TinyAgentOS{% endblock %}
{% block content %}
<main class="container">
  <hgroup>
    <h2>Shared Folders</h2>
    <p>Create shared file spaces for agents, groups, and departments</p>
  </hgroup>

  <div id="folder-list" hx-get="/api/partials/shared-folders" hx-trigger="load" hx-swap="innerHTML">
    <p aria-busy="true">Loading folders...</p>
  </div>

  <details>
    <summary role="button" class="secondary">New Folder</summary>
    <form hx-post="/api/shared-folders" hx-target="#folder-list" hx-swap="innerHTML"
          hx-on::after-request="this.reset(); htmx.trigger('#folder-list', 'load')">
      <label for="folder-name">Folder Name
        <input type="text" id="folder-name" name="name" required placeholder="e.g. team-docs">
      </label>
      <label for="folder-desc">Description
        <input type="text" id="folder-desc" name="description" placeholder="Optional description">
      </label>
      <button type="submit">Create Folder</button>
    </form>
  </details>
</main>
{% endblock %}
```

- [ ] **Step 3: Add the page route**

In `tinyagentos/routes/shared_folders.py`, add at the top of the routes (after the imports):

```python
from fastapi.responses import HTMLResponse

@router.get("/shared-folders", response_class=HTMLResponse)
async def shared_folders_page(request: Request):
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "shared_folders.html", {
        "active_page": "shared-folders",
    })
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_routes_shared_folders.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add tinyagentos/templates/shared_folders.html tinyagentos/routes/shared_folders.py tests/test_routes_shared_folders.py
git commit -m "feat: add Shared Folders UI page with create/delete/access management"
```

---

### Task 7: Channel Hub UI Page

**Files:**
- Create: `tinyagentos/templates/channel_hub.html`
- Modify: `tinyagentos/routes/channel_hub.py`
- Test: `tests/test_routes_channel_hub.py`

- [ ] **Step 1: Write the tests**

```python
# tests/test_routes_channel_hub.py
import pytest


class TestChannelHubRoutes:
    @pytest.mark.asyncio
    async def test_channel_hub_page(self, client):
        resp = await client.get("/channel-hub")
        assert resp.status_code == 200
        assert b"Channel Hub" in resp.content

    @pytest.mark.asyncio
    async def test_hub_status(self, client):
        resp = await client.get("/api/channel-hub/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "connectors" in data
        assert "adapters" in data

    @pytest.mark.asyncio
    async def test_list_adapters(self, client):
        resp = await client.get("/api/channel-hub/adapters")
        assert resp.status_code == 200
        data = resp.json()
        assert "adapters" in data

    @pytest.mark.asyncio
    async def test_connect_webchat(self, client):
        resp = await client.post("/api/channel-hub/connect", content='{"platform": "webchat", "agent_name": "test-agent"}', headers={"content-type": "application/json"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "connected"

    @pytest.mark.asyncio
    async def test_disconnect(self, client):
        # Connect first
        await client.post("/api/channel-hub/connect", content='{"platform": "webchat", "agent_name": "disc-test"}', headers={"content-type": "application/json"})
        resp = await client.post("/api/channel-hub/disconnect", content='{"platform": "webchat", "agent_name": "disc-test"}', headers={"content-type": "application/json"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "disconnected"
```

- [ ] **Step 2: Create the template**

```html
<!-- tinyagentos/templates/channel_hub.html -->
{% extends "base.html" %}
{% block title %}Channel Hub — TinyAgentOS{% endblock %}
{% block content %}
<main class="container">
  <hgroup>
    <h2>Channel Hub</h2>
    <p>Manage messaging connections across all agents</p>
  </hgroup>

  <section id="hub-status" hx-get="/api/channel-hub/status" hx-trigger="load, every 10s" hx-swap="innerHTML">
    <p aria-busy="true">Loading status...</p>
  </section>

  <h3>Connected Channels</h3>
  <div id="connectors" hx-get="/api/partials/channel-hub-connectors" hx-trigger="load, every 10s" hx-swap="innerHTML">
    <p aria-busy="true">Loading connectors...</p>
  </div>

  <h3>Framework Adapters</h3>
  <div id="adapters" hx-get="/api/channel-hub/adapters" hx-trigger="load, every 10s" hx-swap="innerHTML">
    <p aria-busy="true">Loading adapters...</p>
  </div>
</main>
{% endblock %}
```

- [ ] **Step 3: Add the page route**

In `tinyagentos/routes/channel_hub.py`, add after the imports:

```python
@router.get("/channel-hub", response_class=HTMLResponse)
async def channel_hub_page(request: Request):
    """Channel Hub management page."""
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "channel_hub.html", {
        "active_page": "channel-hub",
    })
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_routes_channel_hub.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add tinyagentos/templates/channel_hub.html tinyagentos/routes/channel_hub.py tests/test_routes_channel_hub.py
git commit -m "feat: add Channel Hub management UI page"
```

---

### Task 8: Model Conversion UI Page

**Files:**
- Create: `tinyagentos/templates/conversions.html`
- Modify: `tinyagentos/routes/conversion.py`
- Test: `tests/test_routes_conversion.py`

- [ ] **Step 1: Write the tests**

```python
# tests/test_routes_conversion.py
import pytest


class TestConversionRoutes:
    @pytest.mark.asyncio
    async def test_conversions_page(self, client):
        resp = await client.get("/conversions")
        assert resp.status_code == 200
        assert b"Model Conversion" in resp.content

    @pytest.mark.asyncio
    async def test_list_conversion_jobs(self, client):
        resp = await client.get("/api/conversion/jobs")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    @pytest.mark.asyncio
    async def test_list_formats(self, client):
        resp = await client.get("/api/conversion/formats")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    @pytest.mark.asyncio
    async def test_create_job_invalid_path(self, client):
        resp = await client.post("/api/conversion/jobs", json={
            "source_model": "test-model",
            "source_format": "invalid",
            "target_format": "also-invalid",
        })
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_get_job_not_found(self, client):
        resp = await client.get("/api/conversion/jobs/nonexistent")
        assert resp.status_code == 404
```

- [ ] **Step 2: Create the template**

```html
<!-- tinyagentos/templates/conversions.html -->
{% extends "base.html" %}
{% block title %}Model Conversion — TinyAgentOS{% endblock %}
{% block content %}
<main class="container">
  <hgroup>
    <h2>Model Conversion</h2>
    <p>Convert models between formats (GGUF, RKLLM, MLX, HuggingFace)</p>
  </hgroup>

  <div id="conversion-jobs" hx-get="/api/conversion/jobs" hx-trigger="load, every 10s" hx-swap="innerHTML">
    <p aria-busy="true">Loading conversion jobs...</p>
  </div>

  <h3>Available Conversion Paths</h3>
  <div id="formats" hx-get="/api/conversion/formats" hx-trigger="load" hx-swap="innerHTML">
    <p aria-busy="true">Loading formats...</p>
  </div>
</main>
{% endblock %}
```

- [ ] **Step 3: Add the page route**

In `tinyagentos/routes/conversion.py`, add after the imports:

```python
from fastapi.responses import HTMLResponse

@router.get("/conversions", response_class=HTMLResponse)
async def conversions_page(request: Request):
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "conversions.html", {
        "active_page": "conversions",
    })
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_routes_conversion.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add tinyagentos/templates/conversions.html tinyagentos/routes/conversion.py tests/test_routes_conversion.py
git commit -m "feat: add Model Conversion UI page with job list and format browser"
```

---

### Task 9: Remaining Route Tests (auth, training, workspace, import_data)

**Files:**
- Create: `tests/test_routes_auth.py`
- Create: `tests/test_routes_training.py`
- Create: `tests/test_routes_workspace.py`
- Create: `tests/test_routes_import_data.py`

- [ ] **Step 1: Write auth tests**

```python
# tests/test_routes_auth.py
import pytest


class TestAuthRoutes:
    @pytest.mark.asyncio
    async def test_login_page(self, client):
        resp = await client.get("/login")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_login_no_password_configured(self, client):
        resp = await client.post("/login", data={"password": "anything"})
        # No password set in test config, should redirect
        assert resp.status_code in (200, 303)

    @pytest.mark.asyncio
    async def test_health_exempt_from_auth(self, client):
        resp = await client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    @pytest.mark.asyncio
    async def test_cluster_register_exempt_from_auth(self, client):
        resp = await client.post("/api/cluster/register", json={
            "name": "test-worker",
            "platform": "linux",
            "capabilities": [],
            "hardware": {},
        })
        assert resp.status_code == 200
```

- [ ] **Step 2: Write training tests**

```python
# tests/test_routes_training.py
import pytest


class TestTrainingRoutes:
    @pytest.mark.asyncio
    async def test_training_page(self, client):
        resp = await client.get("/training")
        assert resp.status_code == 200
        assert b"training" in resp.content.lower()

    @pytest.mark.asyncio
    async def test_list_jobs_empty(self, client):
        resp = await client.get("/api/training/jobs")
        assert resp.status_code == 200
        assert resp.json()["jobs"] == []

    @pytest.mark.asyncio
    async def test_create_and_get_job(self, client):
        resp = await client.post("/api/training/jobs", json={
            "base_model": "qwen3-0.6b",
            "agent_name": "test-agent",
        })
        assert resp.status_code == 200
        job_id = resp.json()["id"]
        resp = await client.get(f"/api/training/jobs/{job_id}")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_delete_job(self, client):
        resp = await client.post("/api/training/jobs", json={"base_model": "qwen3-0.6b"})
        job_id = resp.json()["id"]
        resp = await client.delete(f"/api/training/jobs/{job_id}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"

    @pytest.mark.asyncio
    async def test_list_presets(self, client):
        resp = await client.get("/api/training/presets")
        assert resp.status_code == 200
        assert "presets" in resp.json()
```

- [ ] **Step 3: Write workspace tests**

```python
# tests/test_routes_workspace.py
import pytest


class TestWorkspaceRoutes:
    @pytest.mark.asyncio
    async def test_workspace_page(self, client):
        resp = await client.get("/workspace/test-agent")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_workspace_messages(self, client):
        resp = await client.get("/workspace/test-agent/messages")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_workspace_files(self, client):
        resp = await client.get("/workspace/test-agent/files")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_send_inter_agent_message(self, client):
        resp = await client.post("/api/agents/test-agent/messages", json={
            "to": "other-agent",
            "message": "Hello from test",
        })
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_get_contacts(self, client):
        resp = await client.get("/api/agents/test-agent/contacts")
        assert resp.status_code == 200
```

- [ ] **Step 4: Write import data tests**

```python
# tests/test_routes_import_data.py
import io
import pytest


class TestImportDataRoutes:
    @pytest.mark.asyncio
    async def test_import_page(self, client):
        resp = await client.get("/import")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_upload_text_file(self, client):
        file_content = b"This is a test document for import."
        resp = await client.post(
            "/api/import/test-agent",
            files={"file": ("test.txt", io.BytesIO(file_content), "text/plain")},
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_list_imports(self, client):
        resp = await client.get("/api/import/test-agent")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_upload_no_file(self, client):
        resp = await client.post("/api/import/test-agent")
        assert resp.status_code in (400, 422)
```

- [ ] **Step 5: Run all new test files**

Run: `.venv/bin/python -m pytest tests/test_routes_auth.py tests/test_routes_training.py tests/test_routes_workspace.py tests/test_routes_import_data.py -v`
Expected: PASS (18 tests)

- [ ] **Step 6: Commit**

```bash
git add tests/test_routes_auth.py tests/test_routes_training.py tests/test_routes_workspace.py tests/test_routes_import_data.py
git commit -m "test: add tests for auth, training, workspace, and import routes"
```

---

### Task 10: Full Test Suite Verification

- [ ] **Step 1: Run entire test suite**

Run: `.venv/bin/python -m pytest tests/ --tb=short -q`
Expected: ~750+ tests PASS

- [ ] **Step 2: Fix any failures**

Address any test conflicts or regressions from the changes.

- [ ] **Step 3: Update README test count**

In `README.md`, update the test count:
```
pytest tests/ -v          # 750+ tests
```

- [ ] **Step 4: Final commit**

```bash
git add README.md
git commit -m "docs: update test count to 750+"
```

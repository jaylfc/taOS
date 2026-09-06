# Provider Lifecycle Management — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Per-provider lifecycle policy (enabled, auto_manage, keep_alive) with on-demand start/stop, graceful drain, kill-now escape hatch, and auto-registration from app catalog manifests.

**Architecture:** A new `LifecycleManager` sits between the scheduler and backend services, starting services on demand and stopping them after the keep-alive window. `BackendEntry` gains lifecycle state (`stopped → starting → running → draining → stopping`). App catalog service manifests declare defaults; user overrides live in `config.yaml`. taOS-managed services auto-register at install time — no manual provider setup required.

**Tech Stack:** Python asyncio, FastAPI, YAML manifests, React/TypeScript (desktop/src), pytest-asyncio

---

## File Map

**Create:**
- `tinyagentos/lifecycle_manager.py` — demand-triggered start/stop, keep-alive timer
- `app-catalog/services/rknn-sd.yaml` — service manifest for RKNN SD
- `app-catalog/services/rkllama.yaml` — service manifest for rkllama
- `tests/test_lifecycle_manager.py` — unit tests for LifecycleManager

**Modify:**
- `tinyagentos/scheduler/backend_catalog.py` — add lifecycle fields to `BackendEntry`; skip disabled backends in polling; expose `backends_startable_for_capability`
- `tinyagentos/config.py` — accept `enabled`, `auto_manage`, `keep_alive_minutes` in backend config; add `auto_register_from_manifest()`
- `tinyagentos/routes/providers.py` — add `PATCH /{name}`, `POST /{name}/start`, `POST /{name}/stop`; update `GET` response; fix `POST /test` to auto-start
- `tinyagentos/main.py` (or wherever app startup lives) — wire `LifecycleManager` into app state
- `desktop/src/apps/ProvidersApp.tsx` — add lifecycle controls, state-aware buttons, improved error messages

---

### Task 1: Extend BackendEntry with lifecycle fields

**Files:**
- Modify: `tinyagentos/scheduler/backend_catalog.py`
- Test: `tests/test_backend_catalog_lifecycle.py` (create)

- [ ] **Step 1: Write failing tests**

```python
# tests/test_backend_catalog_lifecycle.py
from __future__ import annotations
import asyncio
import pytest
from tinyagentos.scheduler.backend_catalog import BackendCatalog, BackendEntry


@pytest.mark.asyncio
async def test_disabled_backend_excluded_from_routing():
    """A backend with enabled=False must not appear in backends_with_capability."""
    async def probe(backend: dict) -> dict:
        return {"status": "ok", "response_ms": 1, "models": []}

    backends = [
        {"name": "b1", "type": "rkllama", "url": "http://b1", "priority": 1, "enabled": False},
        {"name": "b2", "type": "rkllama", "url": "http://b2", "priority": 2, "enabled": True},
    ]
    catalog = BackendCatalog(backends=backends, probe_fn=probe, interval_seconds=3600)
    await catalog.start()
    try:
        results = catalog.backends_with_capability("llm-chat")
        assert len(results) == 1
        assert results[0].name == "b2"
    finally:
        await catalog.stop()


@pytest.mark.asyncio
async def test_lifecycle_state_in_to_dict():
    """BackendEntry.to_dict() must include lifecycle fields."""
    async def probe(backend: dict) -> dict:
        return {"status": "ok", "response_ms": 5, "models": []}

    backends = [
        {
            "name": "b1", "type": "rkllama", "url": "http://b1", "priority": 1,
            "enabled": True, "auto_manage": True, "keep_alive_minutes": 10,
        }
    ]
    catalog = BackendCatalog(backends=backends, probe_fn=probe, interval_seconds=3600)
    await catalog.start()
    try:
        entries = catalog.backends()
        assert len(entries) == 1
        d = entries[0].to_dict()
        assert d["lifecycle_state"] == "running"
        assert d["auto_manage"] is True
        assert d["keep_alive_minutes"] == 10
        assert d["enabled"] is True
    finally:
        await catalog.stop()


@pytest.mark.asyncio
async def test_stopped_backend_in_backends_startable():
    """A stopped+auto_manage backend appears in backends_startable_for_capability."""
    async def probe(backend: dict) -> dict:
        return {"status": "error", "response_ms": 0, "models": []}

    backends = [
        {
            "name": "b1", "type": "rknn-sd", "url": "http://b1", "priority": 1,
            "enabled": True, "auto_manage": True, "keep_alive_minutes": 10,
            "_lifecycle_state": "stopped",
        }
    ]
    catalog = BackendCatalog(backends=backends, probe_fn=probe, interval_seconds=3600)
    # Manually seed the stopped state
    catalog._lifecycle_states["b1"] = "stopped"
    await catalog.start()
    try:
        startable = catalog.backends_startable_for_capability("image-generation")
        assert len(startable) == 1
        assert startable[0].name == "b1"
    finally:
        await catalog.stop()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Volumes/NVMe/Users/jay/Development/tinyagentos
python -m pytest tests/test_backend_catalog_lifecycle.py -v
```
Expected: `FAILED` — `lifecycle_state`, `auto_manage`, `keep_alive_minutes` not yet on `BackendEntry`

- [ ] **Step 3: Extend BackendEntry**

In `tinyagentos/scheduler/backend_catalog.py`, update `BackendEntry`:

```python
@dataclass
class BackendEntry:
    """One backend as seen by the catalog right now."""
    name: str
    type: str
    url: str
    status: str                         # "ok" | "error" | "stale"
    capabilities: set[str]
    models: list[dict]
    priority: int
    last_healthy: Optional[float] = None
    last_probed: float = field(default_factory=time.time)
    error: Optional[str] = None
    # Lifecycle fields
    lifecycle_state: str = "running"    # "stopped"|"starting"|"running"|"draining"|"stopping"
    auto_manage: bool = False
    keep_alive_minutes: int = 10
    enabled: bool = True

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "type": self.type,
            "url": self.url,
            "status": self.status,
            "capabilities": sorted(self.capabilities),
            "models": self.models,
            "priority": self.priority,
            "last_healthy": self.last_healthy,
            "last_probed": self.last_probed,
            "error": self.error,
            "lifecycle_state": self.lifecycle_state,
            "auto_manage": self.auto_manage,
            "keep_alive_minutes": self.keep_alive_minutes,
            "enabled": self.enabled,
        }
```

- [ ] **Step 4: Add `_lifecycle_states` dict and `backends_startable_for_capability` to BackendCatalog**

In `BackendCatalog.__init__`, add:
```python
# Lifecycle states managed externally by LifecycleManager.
# Keyed by backend name. "running" is the default for non-managed backends.
self._lifecycle_states: dict[str, str] = {}
```

Add method after `backends_with_capability`:
```python
def set_lifecycle_state(self, name: str, state: str) -> None:
    """Called by LifecycleManager to update a backend's lifecycle state."""
    self._lifecycle_states[name] = state

def get_lifecycle_state(self, name: str) -> str:
    return self._lifecycle_states.get(name, "running")

def backends_startable_for_capability(self, capability: str) -> list[BackendEntry]:
    """Backends that are stopped+auto_manage=true and could serve this capability.
    Used by LifecycleManager to decide what to start on demand."""
    out = []
    for b in self._backends_config:
        if not b.get("enabled", True):
            continue
        if not b.get("auto_manage", False):
            continue
        state = self._lifecycle_states.get(b["name"], "running")
        if state != "stopped":
            continue
        caps = self._capabilities_for_type(b["type"])
        if capability in caps:
            entry = self._entries.get(b["name"])
            if entry:
                out.append(entry)
    return out
```

- [ ] **Step 5: Skip disabled backends in `_probe_all` and populate lifecycle fields on entries**

Update `_probe_all` in `BackendCatalog`:

```python
async def _probe_all(self) -> None:
    now = time.time()
    active_backends = [b for b in self._backends_config if b.get("enabled", True)]
    results = await asyncio.gather(
        *[self._probe_one(b) for b in active_backends],
        return_exceptions=True,
    )
    for backend, result in zip(active_backends, results):
        name = backend["name"]
        auto_manage = backend.get("auto_manage", False)
        keep_alive_minutes = backend.get("keep_alive_minutes", 10)
        lifecycle_state = self._lifecycle_states.get(name, "running")
        if isinstance(result, Exception):
            self._mark_error(name, backend, str(result), now)
            continue
        if result.get("status") == "ok":
            self._entries[name] = BackendEntry(
                name=name,
                type=backend["type"],
                url=backend["url"],
                status="ok",
                capabilities=self._capabilities_for_type(backend["type"]),
                models=result.get("models", []),
                priority=backend.get("priority", 99),
                last_healthy=now,
                last_probed=now,
                error=None,
                lifecycle_state=lifecycle_state,
                auto_manage=auto_manage,
                keep_alive_minutes=keep_alive_minutes,
                enabled=True,
            )
        else:
            self._mark_error(name, backend, result.get("error"), now)
            # Preserve lifecycle fields on error entries
            entry = self._entries.get(name)
            if entry:
                entry.auto_manage = auto_manage
                entry.keep_alive_minutes = keep_alive_minutes
                entry.lifecycle_state = lifecycle_state
```

Also update `_mark_error` to carry lifecycle fields:
```python
def _mark_error(self, name: str, backend: dict, err: Optional[str], now: float) -> None:
    existing = self._entries.get(name)
    last_healthy = existing.last_healthy if existing else None
    status = "error"
    if last_healthy and (now - last_healthy) < self._stale_after:
        status = "stale"
    self._entries[name] = BackendEntry(
        name=name,
        type=backend["type"],
        url=backend["url"],
        status=status,
        capabilities=self._capabilities_for_type(backend["type"]),
        models=existing.models if existing else [],
        priority=backend.get("priority", 99),
        last_healthy=last_healthy,
        last_probed=now,
        error=err,
        lifecycle_state=self._lifecycle_states.get(name, "running"),
        auto_manage=backend.get("auto_manage", False),
        keep_alive_minutes=backend.get("keep_alive_minutes", 10),
        enabled=backend.get("enabled", True),
    )
```

Update `backends_with_capability` to exclude disabled and stopped backends from routing:
```python
def backends_with_capability(self, capability: str) -> list[BackendEntry]:
    """All healthy, enabled, running backends for this capability, ordered by priority."""
    matches = [
        e for e in self._entries.values()
        if e.status == "ok"
        and e.enabled
        and e.lifecycle_state == "running"
        and capability in e.capabilities
    ]
    matches.sort(key=lambda e: e.priority)
    return matches
```

- [ ] **Step 6: Run tests — expect pass**

```bash
python -m pytest tests/test_backend_catalog_lifecycle.py -v
```
Expected: all 3 tests `PASSED`

- [ ] **Step 7: Run full test suite to check for regressions**

```bash
python -m pytest tests/ -v --tb=short
```
Expected: all existing tests still pass

- [ ] **Step 8: Commit**

```bash
git add tinyagentos/scheduler/backend_catalog.py tests/test_backend_catalog_lifecycle.py
git commit -m "feat(catalog): add lifecycle_state, auto_manage, keep_alive_minutes to BackendEntry"
```

---

### Task 2: Service manifests + config auto-registration

**Files:**
- Create: `app-catalog/services/rknn-sd.yaml`
- Create: `app-catalog/services/rkllama.yaml`
- Modify: `tinyagentos/config.py`
- Test: `tests/test_config_auto_register.py` (create)

- [ ] **Step 1: Write failing test**

```python
# tests/test_config_auto_register.py
from __future__ import annotations
import pytest
from pathlib import Path
from tinyagentos.config import auto_register_from_manifest, AppConfig


def test_auto_register_adds_backend(tmp_path: Path):
    """auto_register_from_manifest writes a backend entry from a manifest file."""
    manifest = tmp_path / "rknn-sd.yaml"
    manifest.write_text("""
id: rknn-sd
name: RKNN Stable Diffusion
type: rknn-sd
default_url: http://localhost:7863
capabilities:
  - image-generation
lifecycle:
  auto_manage: true
  keep_alive_minutes: 10
  start_cmd: "systemctl start tinyagentos-rknn-sd"
  stop_cmd: "systemctl stop tinyagentos-rknn-sd"
  startup_timeout_seconds: 90
""")
    config = AppConfig()
    auto_register_from_manifest(manifest, config)
    assert len(config.backends) == 1
    b = config.backends[0]
    assert b["name"] == "local-rknn-sd"
    assert b["type"] == "rknn-sd"
    assert b["url"] == "http://localhost:7863"
    assert b["auto_manage"] is True
    assert b["keep_alive_minutes"] == 10


def test_auto_register_idempotent(tmp_path: Path):
    """Calling auto_register_from_manifest twice does not add duplicates."""
    manifest = tmp_path / "rknn-sd.yaml"
    manifest.write_text("""
id: rknn-sd
name: RKNN Stable Diffusion
type: rknn-sd
default_url: http://localhost:7863
lifecycle:
  auto_manage: true
  keep_alive_minutes: 10
  start_cmd: "systemctl start tinyagentos-rknn-sd"
  stop_cmd: "systemctl stop tinyagentos-rknn-sd"
  startup_timeout_seconds: 90
""")
    config = AppConfig()
    auto_register_from_manifest(manifest, config)
    auto_register_from_manifest(manifest, config)
    assert len(config.backends) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_config_auto_register.py -v
```
Expected: `FAILED` — `auto_register_from_manifest` not yet defined

- [ ] **Step 3: Add `auto_register_from_manifest` to config.py**

In `tinyagentos/config.py`, add after the imports:

```python
def auto_register_from_manifest(manifest_path: Path, config: "AppConfig") -> bool:
    """Read a service manifest and add a backend entry to config if not already present.

    Returns True if a new entry was added, False if already registered.
    """
    data = yaml.safe_load(manifest_path.read_text())
    backend_type = data.get("type", "")
    default_url = data.get("default_url", "")
    lifecycle = data.get("lifecycle", {})
    name = f"local-{data.get('id', backend_type)}"

    if any(b.get("name") == name for b in config.backends):
        return False

    entry: dict = {
        "name": name,
        "type": backend_type,
        "url": default_url,
        "priority": 99,
        "enabled": True,
        "auto_manage": lifecycle.get("auto_manage", False),
        "keep_alive_minutes": lifecycle.get("keep_alive_minutes", 10),
    }
    if lifecycle.get("start_cmd"):
        entry["start_cmd"] = lifecycle["start_cmd"]
    if lifecycle.get("stop_cmd"):
        entry["stop_cmd"] = lifecycle["stop_cmd"]
    if lifecycle.get("startup_timeout_seconds"):
        entry["startup_timeout_seconds"] = lifecycle["startup_timeout_seconds"]

    config.backends.append(entry)
    return True
```

- [ ] **Step 4: Run tests — expect pass**

```bash
python -m pytest tests/test_config_auto_register.py -v
```
Expected: both tests `PASSED`

- [ ] **Step 5: Create `app-catalog/services/rknn-sd.yaml`**

```yaml
id: rknn-sd
name: RKNN Stable Diffusion
type: rknn-sd
default_url: http://localhost:7863
capabilities:
  - image-generation
lifecycle:
  auto_manage: true
  keep_alive_minutes: 10
  start_cmd: "systemctl start tinyagentos-rknn-sd"
  stop_cmd: "systemctl stop tinyagentos-rknn-sd"
  startup_timeout_seconds: 90
```

- [ ] **Step 6: Create `app-catalog/services/rkllama.yaml`**

```yaml
id: rkllama
name: rkllama (RK3588 NPU LLM)
type: rkllama
default_url: http://localhost:8080
capabilities:
  - llm-chat
  - embedding
  - reranking
lifecycle:
  auto_manage: true
  keep_alive_minutes: 0
  start_cmd: "systemctl start rkllama"
  stop_cmd: "systemctl stop rkllama"
  startup_timeout_seconds: 60
```

Note: `keep_alive_minutes: 0` = always on for rkllama since it's the primary LLM backend.

- [ ] **Step 7: Commit**

```bash
git add tinyagentos/config.py tests/test_config_auto_register.py \
    app-catalog/services/rknn-sd.yaml app-catalog/services/rkllama.yaml
git commit -m "feat(config): service manifests + auto_register_from_manifest helper"
```

---

### Task 3: LifecycleManager

**Files:**
- Create: `tinyagentos/lifecycle_manager.py`
- Test: `tests/test_lifecycle_manager.py` (create)

- [ ] **Step 1: Write failing tests**

```python
# tests/test_lifecycle_manager.py
from __future__ import annotations
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from tinyagentos.lifecycle_manager import LifecycleManager


def _make_catalog(lifecycle_states: dict, backends_config: list[dict]):
    catalog = MagicMock()
    catalog.get_lifecycle_state = lambda name: lifecycle_states.get(name, "running")
    catalog.set_lifecycle_state = MagicMock(side_effect=lambda n, s: lifecycle_states.update({n: s}))
    catalog._backends_config = backends_config
    return catalog


@pytest.mark.asyncio
async def test_start_sets_state_to_running():
    """start() should transition stopped → starting → running on success."""
    states = {"b1": "stopped"}
    backends = [
        {
            "name": "b1", "type": "rknn-sd", "url": "http://b1",
            "start_cmd": "true",   # shell no-op that exits 0
            "startup_timeout_seconds": 5,
        }
    ]
    catalog = _make_catalog(states, backends)

    async def mock_probe(url: str) -> bool:
        return True

    mgr = LifecycleManager(catalog)
    mgr._probe_health = mock_probe
    await mgr.start("b1")
    assert states["b1"] == "running"


@pytest.mark.asyncio
async def test_start_sets_error_on_timeout():
    """start() should set state to stopped if health probe never succeeds."""
    states = {"b1": "stopped"}
    backends = [
        {
            "name": "b1", "type": "rknn-sd", "url": "http://b1",
            "start_cmd": "true",
            "startup_timeout_seconds": 1,
        }
    ]
    catalog = _make_catalog(states, backends)

    async def mock_probe(url: str) -> bool:
        return False  # never healthy

    mgr = LifecycleManager(catalog)
    mgr._probe_health = mock_probe
    with pytest.raises(TimeoutError):
        await mgr.start("b1")
    assert states["b1"] == "stopped"


@pytest.mark.asyncio
async def test_drain_and_stop_graceful():
    """drain_and_stop() should drain then stop the service."""
    states = {"b1": "running"}
    backends = [
        {
            "name": "b1", "type": "rknn-sd", "url": "http://b1",
            "stop_cmd": "true",
        }
    ]
    catalog = _make_catalog(states, backends)
    catalog.in_flight_count = MagicMock(return_value=0)

    mgr = LifecycleManager(catalog)
    await mgr.drain_and_stop("b1", force=False)
    assert states["b1"] == "stopped"


@pytest.mark.asyncio
async def test_kill_stops_immediately():
    """drain_and_stop(force=True) skips drain and stops immediately."""
    states = {"b1": "running"}
    backends = [
        {
            "name": "b1", "type": "rknn-sd", "url": "http://b1",
            "stop_cmd": "true",
        }
    ]
    catalog = _make_catalog(states, backends)

    mgr = LifecycleManager(catalog)
    await mgr.drain_and_stop("b1", force=True)
    assert states["b1"] == "stopped"


@pytest.mark.asyncio
async def test_keepalive_zero_never_stops():
    """keep_alive_minutes=0 means the keepalive timer is never started."""
    states = {"b1": "running"}
    backends = [
        {
            "name": "b1", "type": "rkllama", "url": "http://b1",
            "stop_cmd": "true", "keep_alive_minutes": 0,
        }
    ]
    catalog = _make_catalog(states, backends)

    mgr = LifecycleManager(catalog)
    mgr.notify_task_complete("b1")
    # Give the event loop a tick
    await asyncio.sleep(0)
    # Timer should NOT have been started — state stays running
    assert states["b1"] == "running"
    assert "b1" not in mgr._keepalive_tasks
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_lifecycle_manager.py -v
```
Expected: `FAILED` — `lifecycle_manager` module does not exist

- [ ] **Step 3: Implement LifecycleManager**

Create `tinyagentos/lifecycle_manager.py`:

```python
"""Provider lifecycle management.

Starts and stops backend services on demand, manages keep-alive timers,
and exposes graceful drain + kill-now stop paths.
"""
from __future__ import annotations

import asyncio
import logging
import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tinyagentos.scheduler.backend_catalog import BackendCatalog

logger = logging.getLogger(__name__)

_DRAIN_TIMEOUT_SECONDS = 60


class LifecycleManager:
    """Manages start/stop lifecycle for auto-managed backend services."""

    def __init__(self, catalog: "BackendCatalog") -> None:
        self._catalog = catalog
        self._keepalive_tasks: dict[str, asyncio.Task] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def start(self, name: str) -> None:
        """Start a stopped backend service.

        Runs start_cmd, polls /health until the service responds, then sets
        lifecycle_state to "running". Raises TimeoutError if the service
        does not respond within startup_timeout_seconds.
        """
        backend = self._backend_config(name)
        start_cmd = backend.get("start_cmd", "")
        timeout = backend.get("startup_timeout_seconds", 60)

        self._catalog.set_lifecycle_state(name, "starting")
        logger.info("lifecycle: starting %s via %r", name, start_cmd)

        if start_cmd:
            proc = await asyncio.create_subprocess_shell(
                start_cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            await proc.wait()

        # Poll health until ready
        url = backend.get("url", "")
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            if await self._probe_health(url):
                self._catalog.set_lifecycle_state(name, "running")
                logger.info("lifecycle: %s is running", name)
                return
            await asyncio.sleep(2)

        self._catalog.set_lifecycle_state(name, "stopped")
        raise TimeoutError(
            f"Service {name!r} did not respond within {timeout}s"
        )

    async def drain_and_stop(self, name: str, force: bool = False) -> None:
        """Stop a running backend service.

        If force=False: waits up to _DRAIN_TIMEOUT_SECONDS for in-flight
        tasks to finish (graceful drain), then runs stop_cmd.
        If force=True: runs stop_cmd immediately (kill now).
        """
        backend = self._backend_config(name)
        stop_cmd = backend.get("stop_cmd", "")

        self._catalog.set_lifecycle_state(name, "draining")
        self._cancel_keepalive(name)

        if not force:
            try:
                await asyncio.wait_for(
                    self._wait_for_drain(name),
                    timeout=_DRAIN_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                logger.warning("lifecycle: drain timeout for %s, forcing stop", name)

        self._catalog.set_lifecycle_state(name, "stopping")
        logger.info("lifecycle: stopping %s via %r", name, stop_cmd)

        if stop_cmd:
            proc = await asyncio.create_subprocess_shell(
                stop_cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            await proc.wait()

        self._catalog.set_lifecycle_state(name, "stopped")
        logger.info("lifecycle: %s stopped", name)

    def notify_task_complete(self, name: str) -> None:
        """Call this when a task on a backend completes.

        Starts or resets the keep-alive timer for the backend.
        If keep_alive_minutes=0, no timer is started (always-on).
        """
        backend = self._backend_config(name)
        keep_alive = backend.get("keep_alive_minutes", 10)
        if keep_alive == 0:
            return
        self._cancel_keepalive(name)
        delay = keep_alive * 60
        task = asyncio.create_task(
            self._keepalive_expire(name, delay),
            name=f"keepalive-{name}",
        )
        self._keepalive_tasks[name] = task

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _probe_health(self, url: str) -> bool:
        """Return True if the service at url responds to /health with status ok."""
        import httpx
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{url.rstrip('/')}/health", timeout=3)
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("status") in ("ok", "healthy", "running")
        except Exception:
            pass
        return False

    async def _wait_for_drain(self, name: str) -> None:
        """Wait until the catalog reports no in-flight tasks for this backend."""
        while True:
            count = getattr(self._catalog, "in_flight_count", lambda n: 0)(name)
            if count == 0:
                return
            await asyncio.sleep(1)

    async def _keepalive_expire(self, name: str, delay: float) -> None:
        await asyncio.sleep(delay)
        if self._catalog.get_lifecycle_state(name) != "running":
            return
        logger.info("lifecycle: keep-alive expired for %s, stopping", name)
        try:
            await self.drain_and_stop(name, force=False)
        except Exception:
            logger.exception("lifecycle: stop failed for %s after keep-alive", name)

    def _cancel_keepalive(self, name: str) -> None:
        task = self._keepalive_tasks.pop(name, None)
        if task and not task.done():
            task.cancel()

    def _backend_config(self, name: str) -> dict:
        for b in self._catalog._backends_config:
            if b.get("name") == name:
                return b
        return {}
```

- [ ] **Step 4: Run tests — expect pass**

```bash
python -m pytest tests/test_lifecycle_manager.py -v
```
Expected: all 5 tests `PASSED`

- [ ] **Step 5: Commit**

```bash
git add tinyagentos/lifecycle_manager.py tests/test_lifecycle_manager.py
git commit -m "feat(lifecycle): add LifecycleManager with start/stop/kill and keep-alive timer"
```

---

### Task 4: Wire LifecycleManager into app startup

**Files:**
- Modify: `tinyagentos/main.py` (find the FastAPI app startup — search for `lifespan` or `on_startup` or `app.state`)

- [ ] **Step 1: Find the app startup location**

```bash
grep -n "app.state\|lifespan\|on_startup\|BackendCatalog" /Volumes/NVMe/Users/jay/Development/tinyagentos/tinyagentos/main.py | head -20
```

- [ ] **Step 2: Instantiate LifecycleManager after BackendCatalog is created**

In `main.py`, after the line that creates the `BackendCatalog` (it will look like `catalog = BackendCatalog(...)`), add:

```python
from tinyagentos.lifecycle_manager import LifecycleManager
app.state.lifecycle_manager = LifecycleManager(catalog)
```

Set initial lifecycle states for backends with `auto_manage=true` that are not currently reachable:

```python
# After catalog.start(), set stopped state for unreachable auto-managed backends
for entry in catalog.backends():
    b_conf = next((b for b in config.backends if b["name"] == entry.name), {})
    if b_conf.get("auto_manage") and entry.status != "ok":
        catalog.set_lifecycle_state(entry.name, "stopped")
```

- [ ] **Step 3: Auto-register services from manifests on startup**

In `main.py` startup, before `BackendCatalog` is built, scan `app-catalog/services/` and auto-register any service not already in config:

```python
from tinyagentos.config import auto_register_from_manifest
import pathlib

services_dir = pathlib.Path(__file__).parent.parent / "app-catalog" / "services"
if services_dir.exists():
    for manifest_path in services_dir.glob("*.yaml"):
        added = auto_register_from_manifest(manifest_path, config)
        if added:
            await save_config_locked(config, config.config_path)
```

- [ ] **Step 4: Start the server and verify no errors**

```bash
cd /Volumes/NVMe/Users/jay/Development/tinyagentos
python -m tinyagentos.main &
sleep 3
curl -s http://localhost:6969/api/providers | python3 -m json.tool | grep -E "name|lifecycle_state|auto_manage"
kill %1
```

Expected: providers list includes `lifecycle_state`, `auto_manage`, `keep_alive_minutes` fields

- [ ] **Step 5: Commit**

```bash
git add tinyagentos/main.py
git commit -m "feat(startup): wire LifecycleManager; auto-register services from manifests"
```

---

### Task 5: Provider API endpoints

**Files:**
- Modify: `tinyagentos/routes/providers.py`
- Test: `tests/test_provider_lifecycle_api.py` (create)

- [ ] **Step 1: Write failing tests**

```python
# tests/test_provider_lifecycle_api.py
from __future__ import annotations
import pytest
from unittest.mock import AsyncMock, MagicMock
from fastapi.testclient import TestClient
from tinyagentos.routes.providers import router
from fastapi import FastAPI

app = FastAPI()
app.include_router(router)


def _make_app_state(backends: list[dict], lifecycle_states: dict):
    config = MagicMock()
    config.backends = backends
    config.config_path = "/tmp/test-config.yaml"

    catalog = MagicMock()
    catalog.backends = lambda: [
        MagicMock(
            name=b["name"], type=b["type"], url=b["url"],
            status="ok", response_ms=5, models=[],
            lifecycle_state=lifecycle_states.get(b["name"], "running"),
            auto_manage=b.get("auto_manage", False),
            keep_alive_minutes=b.get("keep_alive_minutes", 10),
            enabled=b.get("enabled", True),
            to_dict=lambda b=b, ls=lifecycle_states: {
                **b, "status": "ok", "response_ms": 5, "models": [],
                "lifecycle_state": ls.get(b["name"], "running"),
            },
        )
        for b in backends
    ]

    lifecycle = AsyncMock()
    return config, catalog, lifecycle


def test_patch_provider_updates_lifecycle():
    config, catalog, lifecycle = _make_app_state(
        [{"name": "b1", "type": "rknn-sd", "url": "http://b1", "priority": 99,
          "auto_manage": False, "keep_alive_minutes": 10, "enabled": True}],
        {},
    )
    with TestClient(app) as client:
        client.app.state.config = config
        client.app.state.backend_catalog = catalog
        client.app.state.lifecycle_manager = lifecycle
        resp = client.patch("/api/providers/b1", json={"auto_manage": True, "keep_alive_minutes": 5})
    assert resp.status_code == 200
    assert config.backends[0]["auto_manage"] is True
    assert config.backends[0]["keep_alive_minutes"] == 5


def test_start_provider_calls_lifecycle_manager():
    config, catalog, lifecycle = _make_app_state(
        [{"name": "b1", "type": "rknn-sd", "url": "http://b1", "priority": 99,
          "auto_manage": True, "keep_alive_minutes": 10, "enabled": True}],
        {"b1": "stopped"},
    )
    with TestClient(app) as client:
        client.app.state.config = config
        client.app.state.backend_catalog = catalog
        client.app.state.lifecycle_manager = lifecycle
        resp = client.post("/api/providers/b1/start")
    assert resp.status_code == 200
    lifecycle.start.assert_called_once_with("b1")


def test_stop_provider_graceful():
    config, catalog, lifecycle = _make_app_state(
        [{"name": "b1", "type": "rknn-sd", "url": "http://b1", "priority": 99,
          "auto_manage": True, "keep_alive_minutes": 10, "enabled": True}],
        {"b1": "running"},
    )
    with TestClient(app) as client:
        client.app.state.config = config
        client.app.state.backend_catalog = catalog
        client.app.state.lifecycle_manager = lifecycle
        resp = client.post("/api/providers/b1/stop", json={"force": False})
    assert resp.status_code == 200
    lifecycle.drain_and_stop.assert_called_once_with("b1", force=False)


def test_stop_provider_force():
    config, catalog, lifecycle = _make_app_state(
        [{"name": "b1", "type": "rknn-sd", "url": "http://b1", "priority": 99,
          "auto_manage": True, "keep_alive_minutes": 10, "enabled": True}],
        {"b1": "running"},
    )
    with TestClient(app) as client:
        client.app.state.config = config
        client.app.state.backend_catalog = catalog
        client.app.state.lifecycle_manager = lifecycle
        resp = client.post("/api/providers/b1/stop", json={"force": True})
    assert resp.status_code == 200
    lifecycle.drain_and_stop.assert_called_once_with("b1", force=True)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_provider_lifecycle_api.py -v
```
Expected: `FAILED` — PATCH and start/stop endpoints don't exist yet

- [ ] **Step 3: Add endpoints to `tinyagentos/routes/providers.py`**

Add these imports at the top of `providers.py`:
```python
from tinyagentos.lifecycle_manager import LifecycleManager
```

Add `ProviderPatch` model after `ProviderTest`:
```python
class ProviderPatch(BaseModel):
    enabled: bool | None = None
    auto_manage: bool | None = None
    keep_alive_minutes: int | None = None
```

Add `ProviderStop` model:
```python
class ProviderStop(BaseModel):
    force: bool = False
```

Add the three new endpoints after the existing `@router.post("/api/providers")`:

```python
@router.patch("/api/providers/{name}")
async def patch_provider(request: Request, name: str, body: ProviderPatch):
    """Update lifecycle settings for a local provider."""
    config = request.app.state.config
    backend = next((b for b in config.backends if b.get("name") == name), None)
    if backend is None:
        return JSONResponse({"error": f"Provider '{name}' not found"}, status_code=404)
    if body.enabled is not None:
        backend["enabled"] = body.enabled
    if body.auto_manage is not None:
        backend["auto_manage"] = body.auto_manage
    if body.keep_alive_minutes is not None:
        backend["keep_alive_minutes"] = body.keep_alive_minutes
    await save_config_locked(config, config.config_path)
    return {"status": "updated", "name": name}


@router.post("/api/providers/{name}/start")
async def start_provider(request: Request, name: str):
    """Manually start a stopped provider."""
    config = request.app.state.config
    if not any(b.get("name") == name for b in config.backends):
        return JSONResponse({"error": f"Provider '{name}' not found"}, status_code=404)
    lifecycle: LifecycleManager = getattr(request.app.state, "lifecycle_manager", None)
    if lifecycle is None:
        return JSONResponse({"error": "Lifecycle manager not available"}, status_code=503)
    try:
        await lifecycle.start(name)
        return {"status": "started", "name": name}
    except TimeoutError as e:
        return JSONResponse({"error": str(e)}, status_code=504)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/providers/{name}/stop")
async def stop_provider(request: Request, name: str, body: ProviderStop):
    """Gracefully stop (or force-kill) a running provider."""
    config = request.app.state.config
    if not any(b.get("name") == name for b in config.backends):
        return JSONResponse({"error": f"Provider '{name}' not found"}, status_code=404)
    lifecycle: LifecycleManager = getattr(request.app.state, "lifecycle_manager", None)
    if lifecycle is None:
        return JSONResponse({"error": "Lifecycle manager not available"}, status_code=503)
    await lifecycle.drain_and_stop(name, force=body.force)
    return {"status": "stopped", "name": name}
```

- [ ] **Step 4: Update `GET /api/providers` to include lifecycle fields**

In the existing `list_providers` function, update the entry construction for local providers:

```python
for backend in config.backends:
    status = "unknown"
    response_ms = 0
    models = []
    lifecycle_state = "running"
    try:
        adapter = get_adapter(backend["type"])
        result = await adapter.health(http_client, backend["url"])
        status = result.get("status", "error")
        response_ms = result.get("response_ms", 0)
        models = result.get("models", [])
    except Exception:
        status = "error"

    # Get lifecycle state from catalog if available
    catalog = getattr(request.app.state, "backend_catalog", None)
    if catalog:
        lifecycle_state = catalog.get_lifecycle_state(backend["name"])

    entry = {
        **backend,
        "status": status,
        "response_ms": response_ms,
        "models": models,
        "source": "local",
        "lifecycle_state": lifecycle_state,
        "auto_manage": backend.get("auto_manage", False),
        "keep_alive_minutes": backend.get("keep_alive_minutes", 10),
        "enabled": backend.get("enabled", True),
    }
    entry["category"] = _categorise(entry)
    providers.append(entry)
```

- [ ] **Step 5: Update `POST /api/providers/test` to auto-start if stopped**

Update the existing `test_provider` endpoint:

```python
@router.post("/api/providers/test")
async def test_provider(request: Request, body: ProviderTest):
    """Test connectivity to a provider. Auto-starts if stopped and auto_manage is on."""
    if not body.url:
        return JSONResponse({"error": "URL required"}, status_code=400)
    if body.type not in VALID_BACKEND_TYPES:
        return JSONResponse({"error": f"Invalid type. Must be one of: {sorted(VALID_BACKEND_TYPES)}"}, status_code=400)

    # Auto-start if the provider is stopped and auto_manage is enabled
    config = request.app.state.config
    backend = next((b for b in config.backends if b.get("url") == body.url and b.get("type") == body.type), None)
    if backend and backend.get("auto_manage") and backend.get("enabled", True):
        catalog = getattr(request.app.state, "backend_catalog", None)
        lifecycle = getattr(request.app.state, "lifecycle_manager", None)
        if catalog and lifecycle:
            state = catalog.get_lifecycle_state(backend["name"])
            if state == "stopped":
                try:
                    await lifecycle.start(backend["name"])
                except Exception as e:
                    return JSONResponse({"reachable": False, "error": f"Auto-start failed: {e}"})

    try:
        adapter = get_adapter(body.type)
        http_client = request.app.state.http_client
        result = await adapter.health(http_client, body.url)
        return {
            "reachable": result["status"] == "ok",
            "response_ms": result.get("response_ms", 0),
            "models": result.get("models", []),
        }
    except Exception as e:
        return {"reachable": False, "error": str(e)}
```

- [ ] **Step 6: Run tests — expect pass**

```bash
python -m pytest tests/test_provider_lifecycle_api.py -v
```
Expected: all 4 tests `PASSED`

- [ ] **Step 7: Run full test suite**

```bash
python -m pytest tests/ -v --tb=short
```
Expected: no regressions

- [ ] **Step 8: Commit**

```bash
git add tinyagentos/routes/providers.py tests/test_provider_lifecycle_api.py
git commit -m "feat(api): PATCH/start/stop provider endpoints; lifecycle fields in GET; test auto-start"
```

---

### Task 6: Frontend — lifecycle controls and state-aware UI

**Files:**
- Modify: `desktop/src/apps/ProvidersApp.tsx`

- [ ] **Step 1: Extend the `Provider` TypeScript interface**

Find the `interface Provider` block (around line 60) and add lifecycle fields:

```typescript
interface Provider {
  name: string;
  type: string;
  url: string;
  priority: number;
  api_key_secret?: string;
  model?: string;
  status: string;
  response_ms: number;
  models: ProviderModel[];
  source?: string;
  category?: ProviderCategory;
  worker_name?: string;
  worker_url?: string;
  worker_platform?: string;
  // Lifecycle
  lifecycle_state?: "stopped" | "starting" | "running" | "draining" | "stopping";
  auto_manage?: boolean;
  keep_alive_minutes?: number;
  enabled?: boolean;
}
```

- [ ] **Step 2: Add `LifecycleStatePill` component**

Add after the existing `StatusPill` component (around line 117):

```typescript
const LIFECYCLE_PILL: Record<string, string> = {
  running:  "bg-emerald-500/20 text-emerald-400",
  stopped:  "bg-zinc-500/20 text-zinc-400",
  starting: "bg-blue-500/20 text-blue-400",
  draining: "bg-amber-500/20 text-amber-400",
  stopping: "bg-amber-500/20 text-amber-400",
};

const LIFECYCLE_LABEL: Record<string, string> = {
  running:  "Running",
  stopped:  "Stopped",
  starting: "Starting…",
  draining: "Draining…",
  stopping: "Stopping…",
};

function LifecycleStatePill({ state }: { state: string }) {
  const cls = LIFECYCLE_PILL[state] ?? LIFECYCLE_PILL.stopped;
  const label = LIFECYCLE_LABEL[state] ?? state;
  const isTransitional = state === "starting" || state === "draining" || state === "stopping";
  return (
    <span className={`text-[10px] px-1.5 py-0.5 rounded-full font-medium inline-flex items-center gap-1 ${cls}`}>
      {isTransitional && (
        <svg className="animate-spin" width="8" height="8" viewBox="0 0 8 8" fill="none">
          <circle cx="4" cy="4" r="3" stroke="currentColor" strokeWidth="1.5" strokeDasharray="10" strokeDashoffset="5" />
        </svg>
      )}
      {label}
    </span>
  );
}
```

- [ ] **Step 3: Add lifecycle control helpers and improve error messages in `ProviderDetail`**

Find `function ProviderDetail` (around line 484). Add state variables and handlers after the existing ones:

```typescript
function ProviderDetail({
  provider,
  onEdit,
  onDelete,
  onTestDone,
  onRefresh,
}: {
  provider: Provider;
  onEdit: () => void;
  onDelete: () => void;
  onTestDone: (result: TestResult) => void;
  onRefresh: () => void;
}) {
  const isMobile = useIsMobile();
  const [copied, setCopied] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<TestResult>(null);
  const [lifecycleLoading, setLifecycleLoading] = useState(false);

  const openWindow = useProcessStore((s) => s.openWindow);
  const lifecycleState = provider.lifecycle_state ?? "running";
  const isLocal = !provider.source?.startsWith("worker:");
  const isTransitional = lifecycleState === "starting" || lifecycleState === "draining" || lifecycleState === "stopping";

  async function handleStart() {
    setLifecycleLoading(true);
    try {
      await fetch(`/api/providers/${encodeURIComponent(provider.name)}/start`, { method: "POST" });
      onRefresh();
    } finally {
      setLifecycleLoading(false);
    }
  }

  async function handleStop(force: boolean) {
    setLifecycleLoading(true);
    try {
      await fetch(`/api/providers/${encodeURIComponent(provider.name)}/stop`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ force }),
      });
      onRefresh();
    } finally {
      setLifecycleLoading(false);
    }
  }

  async function handlePatch(patch: { enabled?: boolean; auto_manage?: boolean; keep_alive_minutes?: number }) {
    await fetch(`/api/providers/${encodeURIComponent(provider.name)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    });
    onRefresh();
  }
```

- [ ] **Step 4: Add lifecycle controls section to the `ProviderDetail` render**

In the JSX returned by `ProviderDetail`, add the `LifecycleStatePill` to the header chips (next to `StatusPill`):

```typescript
{/* In the existing chips row — add after <StatusPill status={provider.status} /> */}
{isLocal && provider.lifecycle_state && (
  <LifecycleStatePill state={lifecycleState} />
)}
```

Replace the existing action buttons section with state-aware buttons:

```typescript
{/* Action buttons — lifecycle state aware */}
<div className="flex items-center gap-1 shrink-0">
  {isLocal && lifecycleState === "stopped" && (
    <Button size="sm" variant="outline" onClick={handleStart} disabled={lifecycleLoading}
      aria-label={`Start provider ${provider.name}`}>
      {lifecycleLoading ? "Starting…" : "Start"}
    </Button>
  )}
  {isLocal && lifecycleState === "running" && (
    <>
      <Button size="sm" variant="outline" onClick={handleTest} disabled={testing}
        aria-label={`Test connection for ${provider.name}`}>
        <RefreshCw size={13} className={testing ? "animate-spin" : ""} />
        {testing ? "Testing..." : "Test"}
      </Button>
      <Button size="sm" variant="outline" onClick={() => handleStop(false)} disabled={lifecycleLoading}
        aria-label={`Stop provider ${provider.name}`}>
        Stop
      </Button>
      <button
        onClick={() => handleStop(true)}
        disabled={lifecycleLoading}
        className="text-[11px] text-red-400 hover:text-red-300 px-1"
        aria-label={`Force kill provider ${provider.name}`}
      >
        Kill
      </button>
    </>
  )}
  {isLocal && isTransitional && (
    <span className="text-[11px] text-shell-text-tertiary px-2">
      {LIFECYCLE_LABEL[lifecycleState]}
    </span>
  )}
  {(!isLocal || lifecycleState === "running") && (
    <>
      <Button size="sm" variant="outline" onClick={onEdit} aria-label={`Edit provider ${provider.name}`}>
        <Edit size={13} />
        Edit
      </Button>
      <Button size="sm" variant="outline" onClick={onDelete}
        className="hover:bg-red-500/15 hover:text-red-300"
        aria-label={`Delete provider ${provider.name}`}>
        <Trash2 size={13} />
        Delete
      </Button>
    </>
  )}
</div>
```

- [ ] **Step 5: Add lifecycle controls panel (enabled, auto_manage, keep_alive)**

In the provider detail body, add a "Lifecycle" section after the existing BASE URL / API KEY / MODELS cards:

```typescript
{/* Lifecycle settings — only for local, auto-managed providers */}
{isLocal && provider.auto_manage !== undefined && (
  <div className="mx-4 mb-3 rounded-lg border border-white/8 bg-white/3 overflow-hidden">
    <div className="px-3 py-2 border-b border-white/5">
      <span className="text-[10px] font-medium text-shell-text-tertiary uppercase tracking-wider">
        Lifecycle
      </span>
    </div>
    <div className="px-3 py-2 space-y-3">
      {/* Enabled toggle */}
      <div className="flex items-center justify-between">
        <span className="text-[12px] text-shell-text-secondary">Enabled</span>
        <button
          role="switch"
          aria-checked={provider.enabled ?? true}
          onClick={() => handlePatch({ enabled: !(provider.enabled ?? true) })}
          className={`w-8 h-4 rounded-full transition-colors relative ${
            (provider.enabled ?? true) ? "bg-emerald-500" : "bg-zinc-600"
          }`}
        >
          <span className={`absolute top-0.5 w-3 h-3 rounded-full bg-white transition-transform ${
            (provider.enabled ?? true) ? "translate-x-4" : "translate-x-0.5"
          }`} />
        </button>
      </div>
      {/* Auto manage toggle */}
      <div className="flex items-center justify-between">
        <span className="text-[12px] text-shell-text-secondary">Auto manage</span>
        <button
          role="switch"
          aria-checked={provider.auto_manage ?? false}
          onClick={() => handlePatch({ auto_manage: !(provider.auto_manage ?? false) })}
          className={`w-8 h-4 rounded-full transition-colors relative ${
            (provider.auto_manage ?? false) ? "bg-emerald-500" : "bg-zinc-600"
          }`}
        >
          <span className={`absolute top-0.5 w-3 h-3 rounded-full bg-white transition-transform ${
            (provider.auto_manage ?? false) ? "translate-x-4" : "translate-x-0.5"
          }`} />
        </button>
      </div>
      {/* Keep alive — only shown when auto manage is on */}
      {(provider.auto_manage ?? false) && (
        <div className="flex items-center justify-between gap-3">
          <div>
            <span className="text-[12px] text-shell-text-secondary">Keep alive</span>
            <p className="text-[10px] text-shell-text-tertiary">
              {(provider.keep_alive_minutes ?? 10) === 0
                ? "Always on"
                : `Stop after ${provider.keep_alive_minutes ?? 10} min idle`}
            </p>
          </div>
          <input
            type="number"
            min={0}
            max={60}
            value={provider.keep_alive_minutes ?? 10}
            onChange={(e) => handlePatch({ keep_alive_minutes: Number(e.target.value) })}
            className="w-14 text-[12px] bg-white/5 border border-white/10 rounded px-2 py-1 text-right text-shell-text"
            aria-label="Keep alive minutes (0 = always on)"
          />
        </div>
      )}
    </div>
  </div>
)}
```

- [ ] **Step 6: Improve error messaging**

Find the section where error messages from test results are displayed (around line 620, search for `testResult`). Replace the raw error display:

```typescript
{testResult && !testResult.reachable && (
  <div className="mx-4 mb-3 rounded-lg bg-red-500/10 border border-red-500/20 px-3 py-2">
    <p className="text-[11px] text-red-300">
      {lifecycleState === "stopped" && (provider.auto_manage ?? false)
        ? "Starting service…"
        : lifecycleState === "stopped"
        ? `Service is stopped. Start it manually or enable Auto manage.`
        : testResult.error?.includes("Cannot connect") || testResult.error?.includes("Connection refused")
        ? `Cannot reach ${provider.url}. Check the service is running.`
        : testResult.error ?? "Connection failed"}
    </p>
  </div>
)}
```

- [ ] **Step 7: Pass `onRefresh` from the parent component**

Find where `ProviderDetail` is rendered (around line 838) and pass the `onRefresh` prop — it should call the same `loadProviders` or `fetchProviders` function used by the list. Look for `onTestDone` in the JSX and add `onRefresh={loadProviders}` (or whatever the refresh function is called) alongside it.

- [ ] **Step 8: Build the frontend**

```bash
cd /Volumes/NVMe/Users/jay/Development/tinyagentos/desktop
npm run build
```
Expected: build succeeds with no TypeScript errors

- [ ] **Step 9: Commit**

```bash
git add desktop/src/apps/ProvidersApp.tsx
git commit -m "feat(ui): provider lifecycle controls, state-aware buttons, improved error messages"
```

---

### Task 7: Build frontend assets and migrate existing providers

**Files:**
- Modify: `tinyagentos/config.py` (migration helper)
- Run on device

- [ ] **Step 1: Copy built assets**

```bash
cd /Volumes/NVMe/Users/jay/Development/tinyagentos/desktop
npm run build
cp -r dist/* ../static/desktop/
```

- [ ] **Step 2: Remove stale manually-added rknn-sd provider from config**

On the Orange Pi, the `config.yaml` will have a `local-sd-cpp` entry with wrong type. The auto-registration from Task 4 will add the correct `local-rknn-sd` entry. Remove the old entry:

```bash
# On the Pi — find and edit config.yaml
grep -n "local-sd-cpp\|sd-cpp" ~/.config/taos/config.yaml
# Remove the local-sd-cpp backend entry manually, or via:
curl -X DELETE http://localhost:6969/api/providers/local-sd-cpp
```

- [ ] **Step 3: Restart taOS and verify auto-registration**

```bash
# On the Pi
sudo systemctl restart tinyagentos
sleep 5
curl -s http://localhost:6969/api/providers | python3 -m json.tool | grep -E "name|type|url|lifecycle_state|auto_manage"
```

Expected output includes:
```json
{ "name": "local-rknn-sd", "type": "rknn-sd", "url": "http://localhost:7863",
  "lifecycle_state": "running", "auto_manage": true, "keep_alive_minutes": 10 }
```

- [ ] **Step 4: Commit built assets + push**

```bash
git add static/desktop/
git commit -m "build: rebuild frontend with provider lifecycle controls"
git push origin master
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Implemented in |
|---|---|
| `enabled`, `auto_manage`, `keep_alive_minutes` fields | Task 1 (BackendEntry), Task 2 (config) |
| `keep_alive_minutes: 0` = always on | Task 3 (LifecycleManager.notify_task_complete) |
| `stopped → starting → running → draining → stopping` state machine | Task 1 (BackendEntry.lifecycle_state) |
| Manifest lifecycle block | Task 2 (app-catalog/services/*.yaml) |
| Auto-registration from manifest at install | Task 4 (main.py startup scan) |
| LifecycleManager start/drain_and_stop/kill | Task 3 |
| Keep-alive timer + scheduler eviction | Task 3 (notify_task_complete + _keepalive_expire) |
| PATCH /api/providers/{name} | Task 5 |
| POST /api/providers/{name}/start | Task 5 |
| POST /api/providers/{name}/stop (force=False/True) | Task 5 |
| GET /api/providers lifecycle fields | Task 5 |
| POST /api/providers/test auto-start | Task 5 |
| UI: LifecycleStatePill | Task 6 |
| UI: enabled/auto_manage toggles, keep_alive input | Task 6 |
| UI: state-aware Start/Stop/Kill buttons | Task 6 |
| UI: human-readable error messages | Task 6 |
| Migrate existing providers (rknn-sd fix) | Task 7 |

**Type consistency check:** `LifecycleManager.start(name: str)` used consistently across Task 3, 4, 5. `drain_and_stop(name, force)` consistent across Task 3, 5. `set_lifecycle_state` / `get_lifecycle_state` defined Task 1, used Task 3. `auto_register_from_manifest(manifest_path, config)` defined Task 2, used Task 4.

**No placeholders:** All code blocks are complete. No TBDs.

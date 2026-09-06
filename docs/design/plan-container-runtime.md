# Dual Container Runtime Implementation Plan

**Status:** Implemented — this plan has landed; see the feature on `master` for the current state.

**Goal:** Refactor the container management layer to support both LXC (incus) and Docker behind a common interface, with auto-detection and user override in settings.

**Architecture:** Extract the current `containers.py` into a `containers/` package with an ABC and two backend implementations. A `__init__.py` re-exports the same function names for backward compatibility. Detection logic picks the best runtime on startup. Settings page shows detected runtimes with an override dropdown.

**Tech Stack:** Python 3.10+, asyncio subprocess, Docker CLI, incus CLI, pytest

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `tinyagentos/containers/__init__.py` | Re-exports for backward compat + get/set backend |
| Create | `tinyagentos/containers/backend.py` | ABC, ContainerInfo, detection logic |
| Create | `tinyagentos/containers/lxc.py` | LXCBackend (moved from containers.py) |
| Create | `tinyagentos/containers/docker.py` | DockerBackend (new) |
| Delete | `tinyagentos/containers.py` | Replaced by package |
| Modify | `tinyagentos/routes/settings.py` | Runtime status + override endpoint |
| Modify | `tinyagentos/app.py` | Init container backend on startup |
| Modify | `tests/test_containers.py` | Update import paths, add Docker tests |
| Create | `tests/test_container_docker.py` | DockerBackend-specific tests |
| Create | `tests/test_container_detection.py` | Detection logic tests |

---

### Task 1: Container Backend ABC + ContainerInfo

**Files:**
- Create: `tinyagentos/containers/backend.py`
- Test: `tests/test_container_detection.py`

- [ ] **Step 1: Write detection tests**

Create `tests/test_container_detection.py` testing the detection logic and ABC structure:
- `test_detect_lxc_when_incus_available` - mock `shutil.which("incus")` returns a path
- `test_detect_docker_when_docker_available` - mock incus missing, docker available
- `test_detect_podman_when_podman_available` - mock incus and docker missing, podman available
- `test_detect_none_when_nothing_available` - all missing, returns "none"
- `test_detect_prefers_lxc_over_docker` - when both available, returns "lxc"

- [ ] **Step 2: Implement backend.py**

Create `tinyagentos/containers/backend.py` with:
- `ContainerInfo` dataclass (moved from containers.py)
- `_parse_memory()` helper (moved from containers.py)
- `ContainerBackend` ABC with all 9 abstract methods matching current signatures
- `detect_runtime()` function that checks `shutil.which()` for incus, docker, podman in order
- Module-level `_active_backend` variable and `get_backend()`/`set_backend()` functions

- [ ] **Step 3: Run tests, verify pass**
- [ ] **Step 4: Commit**

```bash
git add tinyagentos/containers/backend.py tests/test_container_detection.py
git commit -m "feat: add container backend ABC with runtime detection"
```

---

### Task 2: LXC Backend

**Files:**
- Create: `tinyagentos/containers/lxc.py`

- [ ] **Step 1: Move existing code to LXCBackend class**

Create `tinyagentos/containers/lxc.py` with `LXCBackend(ContainerBackend)`. Move the `_run()` helper and all incus functions from the old `containers.py` into methods on this class. Each method keeps the exact same logic, just as a method instead of a module-level function.

- [ ] **Step 2: Commit**

```bash
git add tinyagentos/containers/lxc.py
git commit -m "refactor: move LXC container code to LXCBackend class"
```

---

### Task 3: Docker Backend

**Files:**
- Create: `tinyagentos/containers/docker.py`
- Test: `tests/test_container_docker.py`

- [ ] **Step 1: Write Docker backend tests**

Create `tests/test_container_docker.py` with mocked subprocess calls testing:
- `test_list_containers` - mock `docker ps` JSON output, verify parsing
- `test_create_container` - verify `docker run` called with correct flags (name, image, memory limit, CPU limit, detach)
- `test_create_with_volume_mounts` - verify workspace/memory/shared mounts added
- `test_exec_in_container` - verify `docker exec` called
- `test_push_file` - verify `docker cp` called
- `test_start_stop_restart` - verify correct docker commands
- `test_destroy` - verify `docker rm -f`
- `test_get_logs` - verify `docker logs --tail N`
- `test_get_container_ip` - mock `docker inspect` output, verify IP extraction

- [ ] **Step 2: Implement DockerBackend**

Create `tinyagentos/containers/docker.py` with `DockerBackend(ContainerBackend)`. Constructor takes optional `binary` parameter (default `"docker"`, can be `"podman"` for Podman support). Implements all 9 methods using docker CLI commands.

Key differences from LXC:
- `create_container`: uses `docker run -d --name {name} --memory {limit} --cpus {limit} -v /data/agent-workspaces/{agent}:/workspace -v /data/agent-memory/{agent}:/memory {image}`
- `list_containers`: uses `docker ps -a --filter name={prefix} --format json`, parses JSON per line
- `get_container_logs`: uses `docker logs {name} --tail {lines}` instead of journalctl inside container
- `push_file`: uses `docker cp {local} {name}:{remote}`

- [ ] **Step 3: Run tests, verify pass**
- [ ] **Step 4: Commit**

```bash
git add tinyagentos/containers/docker.py tests/test_container_docker.py
git commit -m "feat: add DockerBackend for container management"
```

---

### Task 4: Package init with backward compatibility

**Files:**
- Create: `tinyagentos/containers/__init__.py`
- Delete: `tinyagentos/containers.py`
- Modify: `tests/test_containers.py`

- [ ] **Step 1: Create __init__.py with re-exports**

Create `tinyagentos/containers/__init__.py` that:
- Imports `get_backend`, `set_backend`, `detect_runtime` from `.backend`
- Imports `ContainerInfo`, `_parse_memory` from `.backend`
- Defines wrapper functions that delegate to `get_backend()`:

```python
async def list_containers(prefix="agent-"):
    return await get_backend().list_containers(prefix)

async def create_container(name, image="images:debian/bookworm", memory_limit="2GB", cpu_limit=2):
    return await get_backend().create_container(name, image, memory_limit, cpu_limit)
```

(Same for all 9 functions)

- [ ] **Step 2: Delete old containers.py**

```bash
git rm tinyagentos/containers.py
```

- [ ] **Step 3: Update test_containers.py imports**

The existing tests import from `tinyagentos.containers`. The `__init__.py` re-exports should make them work unchanged. But the mock paths need updating from `tinyagentos.containers._run` to `tinyagentos.containers.lxc._run`. Update the patch targets in the test file.

Also update the test to set the LXC backend before running:

```python
from tinyagentos.containers import set_backend
from tinyagentos.containers.lxc import LXCBackend

@pytest.fixture(autouse=True)
def use_lxc_backend():
    set_backend(LXCBackend())
```

- [ ] **Step 4: Run ALL existing tests**

Run: `.venv/bin/python -m pytest tests/ --ignore=tests/e2e --tb=short -q`
All 858+ tests must pass. The backward-compat re-exports must not break any consumers.

- [ ] **Step 5: Commit**

```bash
git add tinyagentos/containers/__init__.py tests/test_containers.py
git rm tinyagentos/containers.py
git commit -m "refactor: convert containers module to package with backward-compatible re-exports"
```

---

### Task 5: Runtime settings endpoint

**Files:**
- Modify: `tinyagentos/routes/settings.py`
- Modify: `tinyagentos/app.py`
- Modify: `tests/test_routes_settings.py`

- [ ] **Step 1: Write tests**

Add to `tests/test_routes_settings.py`:
- `test_get_container_runtime` - GET /api/settings/container-runtime returns detected runtime, active runtime, available runtimes
- `test_set_container_runtime` - PUT /api/settings/container-runtime with `{"runtime": "docker"}`, verify it persists

- [ ] **Step 2: Add endpoints to settings.py**

```python
@router.get("/api/settings/container-runtime")
async def get_container_runtime(request: Request):
    from tinyagentos.containers import detect_runtime, get_backend
    backend = get_backend()
    return {
        "active": backend.__class__.__name__.replace("Backend", "").lower() if backend else "none",
        "detected": detect_runtime(),
        "configured": request.app.state.config.container_runtime if hasattr(request.app.state.config, "container_runtime") else "auto",
    }

@router.put("/api/settings/container-runtime")
async def set_container_runtime(request: Request):
    body = await request.json()
    runtime = body.get("runtime", "auto")
    # Update config
    config = request.app.state.config
    config.container_runtime = runtime
    await save_config_locked(config, request.app.state.config_path)
    # Apply the change
    from tinyagentos.containers import set_backend, detect_runtime
    from tinyagentos.containers.lxc import LXCBackend
    from tinyagentos.containers.docker import DockerBackend
    if runtime == "auto":
        detected = detect_runtime()
        runtime = detected
    if runtime == "lxc":
        set_backend(LXCBackend())
    elif runtime in ("docker", "podman"):
        set_backend(DockerBackend(binary=runtime))
    return {"status": "updated", "runtime": runtime}
```

- [ ] **Step 3: Wire detection into app.py startup**

In `tinyagentos/app.py`, in the lifespan (after store inits), add:

```python
from tinyagentos.containers import detect_runtime, set_backend
from tinyagentos.containers.lxc import LXCBackend
from tinyagentos.containers.docker import DockerBackend
runtime = getattr(config, "container_runtime", "auto")
if runtime == "auto":
    runtime = detect_runtime()
if runtime == "lxc":
    set_backend(LXCBackend())
elif runtime in ("docker", "podman"):
    set_backend(DockerBackend(binary=runtime))
```

- [ ] **Step 4: Run tests, verify pass**
- [ ] **Step 5: Commit**

```bash
git add tinyagentos/routes/settings.py tinyagentos/app.py tests/test_routes_settings.py
git commit -m "feat: add container runtime settings with auto-detection and user override"
```

---

### Task 6: Full test suite + README

- [ ] **Step 1: Run full suite**

Run: `.venv/bin/python -m pytest tests/ --ignore=tests/e2e --tb=short -q`
Expected: all tests pass

- [ ] **Step 2: Fix any failures**

- [ ] **Step 3: Update README**

Add to the Supported Hardware section or Architecture:
- Note that both LXC and Docker are supported
- VPS users get Docker, SBC/bare metal users get LXC
- Auto-detected on startup, configurable in settings

Update test count.

- [ ] **Step 4: Commit and push**

```bash
git add README.md
git commit -m "docs: update README with dual container runtime support"
git push
```

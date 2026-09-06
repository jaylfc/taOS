# Dual Container Runtime: LXC + Docker

**Status:** Implemented — `tinyagentos/containers/backend.py` auto-detects runtime at startup; both LXC and Docker backends are in `tinyagentos/containers/`.

**Amended:** 2026-04-11 — container runtime detection follows
**backend-driven discovery**. Rather than storing "lxc" or "docker" in a
config file, the platform probes `incus version` and `docker info` at
startup, caches the result briefly, and routes container operations to
whichever is live right now. If both are available, user preference
wins; if neither, container-dependent features gracefully disable. See
[resource-scheduler.md §Backend-driven discovery](resource-scheduler.md).

## Overview

Refactor the container management layer to support both LXC (via incus) and Docker as deployment backends. LXC is preferred on bare metal/SBCs for lower overhead and systemd support. Docker is required for VPS and cloud deployments where LXC isn't available. The runtime is auto-detected on startup with a user override in settings.

## Why

LXC requires privileged host access or nested virtualisation. Most VPS providers (DigitalOcean, Hetzner, Linode, Vultr) only support Docker. Supporting both means TinyAgentOS works everywhere: SBCs at home, gaming PCs in the office, and VPS instances in the cloud.

## Container Backend Interface

Abstract base class with two implementations. The interface matches the existing function signatures exactly so consumers don't need changes.

```
ContainerBackend (ABC)
├── list_containers(prefix) -> list[ContainerInfo]
├── create_container(name, image, memory_limit, cpu_limit) -> dict
├── exec_in_container(name, cmd, timeout) -> tuple[int, str]
├── push_file(name, local_path, remote_path) -> tuple[int, str]
├── start_container(name) -> dict
├── stop_container(name) -> dict
├── restart_container(name) -> dict
├── destroy_container(name) -> dict
├── get_container_logs(name, lines) -> str
│
├── LXCBackend — current incus CLI code, moved from containers.py
└── DockerBackend — docker/podman CLI, same interface
```

### File structure

```
tinyagentos/containers/
├── __init__.py      — re-exports function names for backward compat
├── backend.py       — ABC + ContainerInfo dataclass + detection logic
├── lxc.py           — LXCBackend (current containers.py code)
└── docker.py        — DockerBackend (new)
```

### Command mapping

| Operation | LXC (incus) | Docker |
|---|---|---|
| Create + start | `incus launch image name` | `docker run -d --name name image` |
| Execute command | `incus exec name -- cmd` | `docker exec name cmd` |
| Push file | `incus file push local name/remote` | `docker cp local name:remote` |
| Start | `incus start name` | `docker start name` |
| Stop | `incus stop name` | `docker stop name` |
| Restart | `incus restart name` | `docker restart name` |
| Destroy | `incus delete --force name` | `docker rm -f name` |
| List | `incus list -f json` | `docker ps -a --format json` |
| Get IP | Parse state.network from JSON | `docker inspect` NetworkSettings |
| Logs | `journalctl` inside container | `docker logs --tail N` |

## Pre-built Framework Images

Each agent framework gets a curated Docker image built from a TinyAgentOS base image. This gives us:

- Version pinning: test framework updates before releasing to users
- Pre-configured mounts for workspace, memory, and shared folders
- QMD and agent-bridge pre-installed
- Protection against upstream breaking changes (e.g., OpenClaw lockout incident)

### Base image (`taos-agent-base`)

```
Ubuntu 22.04 (ARM64 + AMD64)
├── Python 3.11 + Node 20
├── QMD (npm global)
├── Supervisor (process manager, replaces systemd in Docker)
├── Mount points: /workspace, /memory, /shared
└── Entrypoint: supervisord starts QMD serve + framework process
```

### Framework image example

```dockerfile
FROM taos-agent-base:latest
RUN pip install smolagents==0.2.0
ENV TAOS_FRAMEWORK=smolagents
COPY supervisord-smolagents.conf /etc/supervisor/conf.d/
```

Tagged: `taos-agent-smolagents:0.2.0`

### Image selection

The deployer resolves the image from the app catalog manifest:

```yaml
# app-catalog/agents/smolagents/manifest.yaml
install:
  method: pip
  package: smolagents
  docker_image: taos-agent-smolagents:0.2.0  # new field
```

If `docker_image` is set and runtime is Docker, use it. Otherwise fall back to base image with runtime install.

## Volume Mounts

Docker containers use bind mounts so agent data persists outside the container:

```
/data/agent-workspaces/{name}/  -> /workspace   (agent files)
/data/agent-memory/{name}/      -> /memory      (QMD database)
/data/shared-folders/           -> /shared       (shared folders, controlled access)
```

Rebuilding or upgrading the container image doesn't lose any agent data. This also means backup/restore and agent export/import work the same regardless of runtime.

LXC containers currently store files internally. For consistency, the LXC backend could optionally use the same bind mount pattern, but this is not required for the initial implementation.

## Auto-detection + Override

### Detection logic (on startup)

```python
def detect_runtime() -> str:
    if shutil.which("incus"):
        return "lxc"
    if shutil.which("docker"):
        return "docker"
    if shutil.which("podman"):
        return "podman"
    return "none"
```

Podman uses the DockerBackend with the binary name swapped (`podman` instead of `docker`). The Podman CLI is Docker-compatible by design.

### Configuration

```yaml
# config.yaml
container_runtime: auto  # auto | lxc | docker | podman
```

`auto` runs the detection logic. An explicit value skips detection. Stored in config and editable from the settings page.

### Settings page

A card in the settings page shows:
- Detected runtimes (which CLIs are available)
- Active runtime (what's currently being used)
- Override dropdown (auto / lxc / docker / podman)

## Deployer Changes

The deployer (`deployer.py`) currently imports specific functions from `containers`. After the refactor, it imports the same function names from `containers/__init__.py`, which routes to the active backend. The deploy flow changes slightly for Docker:

**LXC flow (unchanged):**
1. `create_container(name, "images:debian/bookworm", ...)` — creates LXC container
2. `exec_in_container(name, ["apt-get", "install", ...])` — installs deps
3. `push_file(name, service_file, "/etc/systemd/...")` — pushes QMD service
4. `exec_in_container(name, ["systemctl", "start", ...])` — starts services

**Docker flow (new):**
1. `create_container(name, "taos-agent-smolagents:0.2.0", ...)` — runs pre-built image with volume mounts
2. Container starts QMD serve + framework via supervisord automatically
3. No apt-get, no systemd, no push_file needed for setup
4. `push_file` still works for runtime config injection (uses `docker cp`)

The deployer detects which backend is active and skips the manual install steps for Docker since the image already has everything.

## Backward Compatibility

`containers/__init__.py` exports the same function names:

```python
from tinyagentos.containers.backend import get_backend

async def create_container(name, image="images:debian/bookworm", memory_limit="2GB", cpu_limit=2):
    return await get_backend().create_container(name, image, memory_limit, cpu_limit)

# ... same for all other functions
```

`deployer.py` and `routes/agents.py` continue to `from tinyagentos.containers import create_container` without changes.

## Testing

- All existing container tests should pass unchanged (they mock the container functions)
- New tests: DockerBackend unit tests (mock docker CLI calls)
- New tests: detection logic (mock shutil.which)
- New tests: settings endpoint for runtime override
- Integration test: verify backward-compat re-exports work

## Non-goals

- Running Docker inside LXC or LXC inside Docker
- Container image build pipeline (future work, images are pre-built and published)
- Migration tool to move agents from LXC to Docker or vice versa
- Mixed-mode (some agents LXC, some Docker on the same host)

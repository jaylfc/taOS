"""Generic container-app deploy — SEPARATE from the agent deployer.

Launches a userspace app's backend as a Docker container. No agent
environment (no LiteLLM / skills / AGENTS.md / bridge). Shares only the
low-level tinyagentos.containers Docker primitive. The agent deploy path
(deployer.py, incus) is intentionally not reused here.
"""
from __future__ import annotations

import shutil

from tinyagentos.containers.docker import DockerBackend
from tinyagentos.installers.port_allocator import allocate_host_port

# Untrusted third-party app backends get a conservative, fixed resource cap --
# there is no per-app config for this today and these apps are not vetted the
# way agent containers are.
_MEMORY_LIMIT = "512m"
_CPU_LIMIT = 1


def _app_container_name(app_id: str) -> str:
    return f"taos-app-{app_id}"


async def deploy_app_container(app_id: str, container_spec: dict) -> dict:
    """Deploy a userspace app's backend container via Docker.

    container_spec: {"image": str, "ports": [int, ...]} (already validated
    at manifest-parse time). The first port is the app's service port.
    Returns {"success": True, "host": "127.0.0.1", "port": <host_port>}
    or {"success": False, "error": str}.
    """
    if shutil.which("docker") is None:
        return {"success": False,
                "error": "Docker is required to run container apps but was not found. "
                         "Install Docker (taOS installs it by default) and retry."}
    image = container_spec["image"]
    container_port = int(container_spec["ports"][0])
    name = _app_container_name(app_id)
    backend = DockerBackend("docker")

    # Find a free host port and publish container_port to it, bound to
    # 127.0.0.1 only (enforced inside DockerBackend.create_container). Retry
    # on the narrow TOCTOU window where the chosen port gets taken before
    # docker binds.
    last_error = "deploy failed"
    tried: set[int] = set()
    for _ in range(3):
        host_port = allocate_host_port(app_id, exclude=tried)
        result = await backend.create_container(
            name,
            image=image,
            ports=[(host_port, container_port)],
            memory_limit=_MEMORY_LIMIT,
            cpu_limit=_CPU_LIMIT,
        )
        if result.get("success"):
            return {"success": True, "host": "127.0.0.1", "port": host_port}
        last_error = result.get("error", "container create failed")
        tried.add(host_port)
        if "port is already allocated" not in last_error and "address already in use" not in last_error:
            break  # a non-port error won't be fixed by retrying
    return {"success": False, "error": last_error}


async def destroy_app_container(app_id: str) -> None:
    """Remove an app's backend container (idempotent). Best-effort."""
    backend = DockerBackend("docker")
    await backend.destroy_container(_app_container_name(app_id))

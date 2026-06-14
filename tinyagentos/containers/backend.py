"""Container backend abstraction layer."""
from __future__ import annotations

import logging
import os
import shutil
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ContainerInfo:
    name: str
    status: str  # Running | Stopped | ...
    ip: str | None
    memory_mb: int
    cpu_cores: int


def _parse_memory(mem_str: str) -> int:
    """Parse memory string like '2GB' or '512MB' to megabytes."""
    mem_str = mem_str.strip().upper()
    if not mem_str or mem_str == "0":
        return 0
    if mem_str.endswith("GB"):
        return int(float(mem_str[:-2]) * 1024)
    if mem_str.endswith("MB"):
        return int(float(mem_str[:-2]))
    if mem_str.endswith("KB"):
        return int(float(mem_str[:-2]) / 1024)
    try:
        return int(mem_str) // (1024 * 1024)  # assume bytes
    except ValueError:
        return 0


class PtyHandle(ABC):
    """Abstract handle to an open PTY session inside a container."""

    @abstractmethod
    def read(self, size: int = 4096) -> bytes:
        """Read up to size bytes from the PTY. Blocks until data available."""

    @abstractmethod
    def write(self, data: bytes) -> None:
        """Write bytes to the PTY stdin."""

    @abstractmethod
    def resize(self, rows: int, cols: int) -> None:
        """Notify the PTY of a terminal resize."""

    @abstractmethod
    def close(self) -> None:
        """Close the PTY and terminate the subprocess."""


class ContainerBackend(ABC):
    """Abstract base class for container runtime backends."""

    @abstractmethod
    async def list_containers(self, prefix: str = "taos-agent-") -> list[ContainerInfo]:
        """List all containers matching the given name prefix."""
        ...

    @abstractmethod
    async def set_root_quota(self, name: str, size_gib: int) -> dict:
        """Set per-container rootfs quota.

        On btrfs-backed LXC pools, the quota is immediately enforced via
        btrfs qgroups. On ZFS, same. On dir-backed pools, this is
        accounting-only (soft) because dir pools don't enforce. Docker
        requires a supported storage driver (btrfs, ZFS, devicemapper);
        on overlay2 the call is a no-op and logged.

        Returns a dict with ``success`` (bool) and ``note`` (str).
        """
        ...

    @abstractmethod
    async def create_container(
        self,
        name: str,
        image: str = "images:debian/bookworm",
        memory_limit: str | None = None,
        cpu_limit: int | None = None,
        mounts: list[tuple[str, str]] | None = None,
        env: dict[str, str] | None = None,
        host_uid: int | None = None,
        root_size_gib: int | None = None,
        ports: list[tuple[int, int]] | None = None,
    ) -> dict:
        """Create and start a new container.

        ``mounts`` is a list of ``(host_path, container_path)`` pairs.

        ``env`` is a dict of environment variables injected at container
        creation time. Used for host-service endpoints (LLM proxy, embeddings,
        skills, user memory) so the container holds no baked-in config and
        can be destroyed and rebuilt without losing its wiring.

        ``host_uid``: when provided, apply a UID mapping so container root
        (uid 0) maps to this host UID.

        ``root_size_gib``: when provided, set the rootfs disk quota via
        ``set_root_quota`` after the container is created. On btrfs/ZFS
        pools this is enforced at the kernel level; on dir-backed pools
        and Docker overlay2 without pquota it is accounting-only.

        ``ports``: list of ``(host_port, container_port)`` tuples to publish
        via ``-p host_port:container_port``. Docker implements this; LXC uses
        proxy devices instead (accepted and ignored here).
        """
        ...

    @abstractmethod
    async def exec_in_container(
        self, name: str, cmd: list[str], timeout: int = 300
    ) -> tuple[int, str]:
        """Execute a command inside a container."""
        ...

    @abstractmethod
    async def push_file(
        self, name: str, local_path: str, remote_path: str
    ) -> tuple[int, str]:
        """Push a file into a container."""
        ...

    @abstractmethod
    async def start_container(self, name: str) -> dict:
        """Start a stopped container."""
        ...

    @abstractmethod
    async def stop_container(self, name: str, force: bool = False) -> dict:
        """Stop a running container. Pass force=True to kill immediately."""
        ...

    @abstractmethod
    async def restart_container(self, name: str) -> dict:
        """Restart a container."""
        ...

    @abstractmethod
    async def destroy_container(self, name: str) -> dict:
        """Stop and delete a container."""
        ...

    @abstractmethod
    async def get_container_logs(self, name: str, lines: int = 100) -> str:
        """Get recent logs from a container."""
        ...

    @abstractmethod
    async def rename_container(self, old_name: str, new_name: str) -> dict:
        """Rename a stopped container."""
        ...

    @abstractmethod
    async def add_proxy_device(
        self, name: str, device_name: str, listen: str, connect: str,
        bind_mode: str | None = None,
    ) -> dict:
        """Attach a TCP proxy device so the container's localhost:<port>
        transparently reaches the host's localhost:<port>.

        bind_mode: incus bind_mode value ('instance' binds inside the
        container; omit or 'host' binds on the host).  Use 'instance'
        when the host already owns the listen port (e.g. litellm on 4000).
        """
        ...

    @abstractmethod
    async def snapshot_create(self, name: str, snapshot_name: str) -> dict:
        """Create a named snapshot of the container.

        LXC: ``incus snapshot create <name> <snapshot_name>``.
        Docker: ``docker commit <name> taos/<snapshot_name>:latest``.

        Returns ``{"success": bool, "output": str}`` (and optionally
        ``"note"`` for partial-success situations).
        """
        ...

    @abstractmethod
    async def snapshot_restore(self, name: str, snapshot_name: str) -> dict:
        """Restore a container to a previously-created snapshot.

        LXC: ``incus snapshot restore <name> <snapshot_name>``.
        Docker: not natively supported; returns
        ``{"success": False, "note": "docker snapshot restore not supported"}``.

        Returns ``{"success": bool, "output": str}``.
        """
        ...

    @abstractmethod
    async def snapshot_list(self, name: str) -> dict:
        """List snapshots for a container.

        LXC: parses ``incus info <name>`` for the Snapshots section.
        Docker: lists ``docker images`` filtered to the ``taos/`` namespace.

        Returns ``{"success": bool, "snapshots": list[str], "output": str}``.
        """
        ...

    @abstractmethod
    def spawn_pty(self, name: str, cmd: list[str] | None = None) -> PtyHandle:
        """Open an interactive PTY in container name.

        If cmd is None, the container's default shell is used.
        cmd is passed via bash -lc <cmd> so PATH from .bashrc is honoured.
        """

    @abstractmethod
    async def set_env(self, name: str, key: str, value: str) -> dict:
        """Set a single environment variable on a container without recreating it.

        LXC: ``incus config set <name> environment.<key> <value>``.  The
        change is picked up by the container on next start (or immediately
        if the container is already running and the process re-reads its
        environment via systemd unit restart).

        Docker: requires container recreation to change env vars; returns
        ``{"success": False, "note": "docker env change requires recreate"}``.

        Returns ``{"success": bool, "output": str}``.
        """
        ...


def detect_runtime() -> str:
    """Detect the available container runtime.

    On macOS, the Mac launcher signals its bundled apple/container CLI by
    setting ``TAOS_CONTAINER_BIN``. When present, the apple backend wins
    over every other runtime (Mac .app is a self-contained product).

    Otherwise checks for incus, docker, podman in priority order.
    Returns 'apple', 'lxc', 'docker', 'podman', or 'none'.
    """
    available = []
    if sys.platform == "darwin" and os.environ.get("TAOS_CONTAINER_BIN"):
        available.append("apple")
    if shutil.which("incus"):
        available.append("lxc")
    if shutil.which("docker"):
        available.append("docker")
    if shutil.which("podman"):
        available.append("podman")

    if "apple" in available:
        selected = "apple"
    elif "lxc" in available:
        selected = "lxc"
    elif available:
        selected = available[0]
    else:
        selected = "none"

    logger.info(
        "detect_runtime: selected=%s, available=%s, policy=apple-on-mac>lxc>others",
        selected,
        available,
    )
    return selected


_active_backend: ContainerBackend | None = None


def get_backend() -> ContainerBackend:
    """Return the active container backend.

    Raises RuntimeError if no backend has been set.
    """
    if _active_backend is None:
        raise RuntimeError(
            "No container backend detected on this host. taOS needs Incus or Docker to run "
            "worker containers. Install one (e.g. 'sudo apt install incus' on Ubuntu/Debian, "
            "'sudo dnf install incus' on Fedora) and restart taOS."
        )
    return _active_backend


def set_backend(backend: ContainerBackend) -> None:
    """Set the active container backend."""
    global _active_backend
    _active_backend = backend


def configure_container_runtime(config: object = None) -> str | None:
    """Detect the container runtime, set it as the active backend, and return it.

    Reads ``config.container_runtime`` (default ``"auto"``) to allow the
    operator to pin a specific runtime.  When ``"auto"``, ``detect_runtime()``
    is called to probe the host.

    Returns the runtime name (``"apple"``, ``"lxc"``, ``"docker"``, or
    ``"podman"``), or ``None`` (after logging a warning) if no runtime is
    available.
    """
    from tinyagentos.containers.lxc import LXCBackend
    from tinyagentos.containers.docker import DockerBackend

    runtime = getattr(config, "container_runtime", "auto")
    if runtime == "auto":
        runtime = detect_runtime()
    if runtime == "apple":
        from tinyagentos.containers.apple_backend import AppleContainerBackend
        set_backend(AppleContainerBackend())
        return runtime
    if runtime == "lxc":
        set_backend(LXCBackend())
        return runtime
    if runtime in ("docker", "podman"):
        set_backend(DockerBackend(binary=runtime))
        return runtime
    logger.warning(
        "No container backend detected (Incus / Docker / Podman / Apple). "
        "Cluster features and worker containers will be disabled. "
        "Install one (e.g. 'sudo apt install incus' on Ubuntu/Debian, "
        "'sudo dnf install incus' on Fedora) and restart taOS."
    )
    return None

"""Native (bare-metal) container backend.

Runs agent workloads directly on the host without container overhead.
Useful on constrained nodes where container runtimes (Docker, Incus)
are too heavy, such as CPU-only Qwen3-Embedding-8B deployments.

Selected when ``container_runtime`` is explicitly set to ``"native"``
in the taOS config.  Not auto-detected — bare metal is always available
on a Linux host, so detecting it unconditionally would shadow LXC/Docker.
"""

from __future__ import annotations

import asyncio
import logging
import os
import pty
import select
import shlex
import shutil
import signal
import subprocess
from pathlib import Path

from .backend import ContainerBackend, ContainerInfo, PtyHandle, _parse_memory

logger = logging.getLogger(__name__)

_SYSTEMD_DIR = Path("/etc/systemd/system")

# ---------------------------------------------------------------------------
# SECURITY: the "name" parameter on exec / push_file / spawn_pty is
# DECORATIVE on the native backend — it does NOT provide container-style
# isolation.  All commands run directly on the bare host, and push_file
# writes to arbitrary host paths unless guarded by _safe_host_path.
# Callers MUST treat name as an opaque identifier, not a security boundary.
# ---------------------------------------------------------------------------

# Directories that push_file is allowed to write into on the host.
# Any remote_path that resolves outside these roots is rejected.
# This set MUST be kept minimal — add entries only when a concrete
# deployment path genuinely needs them.
_ALLOWED_WRITE_ROOTS: tuple[str, ...] = (
    "/opt/taos/",
    "/etc/systemd/system/",
    "/var/lib/taos/",
    "/tmp/",
)


def _validate_env_value(key: str, value: str) -> str | None:
    """Return an error message if *value* is unsafe for systemd unit files.

    Newlines in env values can inject arbitrary systemd directives
    into the unit file (e.g. ``ExecStart=/bin/bash\\nRestart=always``
    exploiting naive line-by-line construction).  This validator rejects
    any value containing ``\\n`` or ``\\r``.

    Returns ``None`` when the value is safe to write.
    """
    if "\n" in value or "\r" in value:
        return (
            f"env value for {key!r} must not contain newlines or "
            f"carriage returns (potential systemd directive injection)"
        )
    return None


def _safe_host_path(remote_path: str, *, allowed_roots: tuple[str, ...] = _ALLOWED_WRITE_ROOTS) -> str | None:
    """Return the resolved real path if *remote_path* is safe, or None.

    Rejects:
    - Paths containing ``..`` segments (path traversal).
    - Paths that, when resolved, fall outside every directory in
      *allowed_roots*.
    - Symlinks that escape the allowed roots (realpath is checked after
      resolution).

    Returns the resolved absolute path on success, or ``None`` when the
    path is unsafe.
    """
    if not remote_path or not remote_path.startswith("/"):
        return None

    # Block literal ".." components — simple, hard to bypass, and
    # catches the common case before we touch the filesystem.
    parts = remote_path.split("/")
    if ".." in parts:
        return None

    # Resolve symlinks + collapse any indirect traversal.
    try:
        resolved = os.path.realpath(remote_path)
    except (OSError, ValueError):
        return None

    for root in allowed_roots:
        if resolved == root or resolved.startswith(root.rstrip("/") + "/"):
            return resolved
    return None


class _NativePtyHandle(PtyHandle):
    """PtyHandle backed by a subprocess on the native host."""

    def __init__(self, proc: subprocess.Popen, master_fd: int) -> None:
        self._proc = proc
        self._master_fd = master_fd

    def read(self, size: int = 4096) -> bytes:
        ready, _, _ = select.select([self._master_fd], [], [], 0.1)
        if ready:
            return os.read(self._master_fd, size)
        return b""

    def write(self, data: bytes) -> None:
        os.write(self._master_fd, data)

    def resize(self, rows: int, cols: int) -> None:
        import fcntl
        import struct
        import termios
        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        fcntl.ioctl(self._master_fd, termios.TIOCSWINSZ, winsize)

    def close(self) -> None:
        """Close the PTY and terminate the subprocess.

        Sends SIGTERM, closes the master fd, then waits for the process
        with a short timeout.  The wait is fire-and-forget — callers in
        async contexts should wrap this in ``asyncio.to_thread`` if they
        need to avoid blocking the event loop.
        """
        try:
            self._proc.send_signal(signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            os.close(self._master_fd)
        except OSError:
            pass
        try:
            self._proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            try:
                self._proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass


async def _run(cmd: list[str], timeout: int = 120) -> tuple[int, str]:
    """Run a command on the host and return (returncode, output)."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    return proc.returncode or 0, stdout.decode() if stdout else ""


def _service_unit_path(name: str) -> Path:
    """Return the systemd service unit path for a container name."""
    return _SYSTEMD_DIR / f"{name}.service"


def _has_systemd() -> bool:
    """Return True if systemd is available on this host."""
    return shutil.which("systemctl") is not None


def _systemd_dir_is_writable() -> bool:
    """Return True if the systemd unit directory exists and is writable.

    Called once at backend init time so a read-only /etc/systemd/system
    (common in containers) is caught early rather than failing mid-deploy.
    """
    try:
        _SYSTEMD_DIR.mkdir(parents=True, exist_ok=True)
        test = _SYSTEMD_DIR / ".taos-native-write-test"
        test.touch()
        test.unlink()
        return True
    except (OSError, PermissionError):
        return False


class NativeBackend(ContainerBackend):
    """Container backend that runs workloads directly on the bare-metal host.

    Each "container" is a systemd service unit named after the container
    (e.g. ``taos-agent-foo.service``).  Commands run directly on the host
    via subprocess — no container isolation.  This is intended for
    single-purpose constrained nodes where every cycle counts.

    When systemd is not available (container-in-container, CI), falls back
    to a simple subprocess-pid tracking approach for ``list_containers``
    and treats ``start/stop/destroy`` as no-ops with a warning.

    .. SECURITY::

       The ``name`` parameter on ``exec_in_container``, ``push_file``,
       and ``spawn_pty`` is DECORATIVE — it does NOT provide the
       container-style name-is-the-boundary guarantee that LXC and Docker
       backends offer.  Commands run directly on the host, and
       ``push_file`` is gated by :data:`_ALLOWED_WRITE_ROOTS` rather
       than a container rootfs.
    """

    def __init__(self) -> None:
        pass  # writability is checked lazily at create_container time

    # ------------------------------------------------------------------
    # list_containers
    # ------------------------------------------------------------------

    async def list_containers(self, prefix: str = "taos-agent-") -> list[ContainerInfo]:
        """List systemd services whose name starts with *prefix*.

        Parses the ACTIVE column of ``systemctl list-units`` (index 2),
        not the SUB column (index 3), so states like "active",
        "inactive", and "failed" are correctly detected instead of
        misreading sub-states like "running" / "dead" / "exited".
        """
        if not _has_systemd():
            return await self._list_containers_fallback(prefix)
        code, output = await _run(
            ["systemctl", "list-units", "--type=service", "--all",
             "--no-legend", "--no-pager", f"{prefix}*"],
            timeout=15,
        )
        if code != 0:
            logger.warning("systemctl list-units failed: %s", output)
            return []
        results: list[ContainerInfo] = []
        for line in output.splitlines():
            parts = line.split()
            if len(parts) < 4:
                continue
            unit = parts[0]
            # strip .service suffix to get container name
            name = unit[:-len(".service")] if unit.endswith(".service") else unit
            if not name.startswith(prefix):
                continue
            status = parts[2]  # ACTIVE column: active, inactive, failed, etc.
            # Map systemd states to container-ish statuses
            if status == "active":
                mapped = "Running"
            elif status == "inactive":
                mapped = "Stopped"
            elif status == "failed":
                mapped = "Stopped"
            else:
                mapped = status.capitalize()
            results.append(ContainerInfo(
                name=name,
                status=mapped,
                ip="127.0.0.1",   # bare metal — always reachable at localhost
                memory_mb=0,
                cpu_cores=0,
            ))
        return results

    async def _list_containers_fallback(
        self, prefix: str = "taos-agent-",
    ) -> list[ContainerInfo]:
        """Fallback when systemd is unavailable: probe /etc/systemd/system."""
        results: list[ContainerInfo] = []
        if not _SYSTEMD_DIR.exists():
            return results
        for unit_file in sorted(_SYSTEMD_DIR.glob(f"{prefix}*.service")):
            name = unit_file.stem  # strip .service
            if not name.startswith(prefix):
                continue
            results.append(ContainerInfo(
                name=name,
                status="Stopped",
                ip="127.0.0.1",
                memory_mb=0,
                cpu_cores=0,
            ))
        return results

    # ------------------------------------------------------------------
    # set_root_quota
    # ------------------------------------------------------------------

    async def set_root_quota(self, name: str, size_gib: int) -> dict:
        """Disk quotas are not enforced on bare metal.

        Returns success=True with an advisory note so callers don't block."""
        return {
            "success": True,
            "note": "disk quota not enforced on bare metal; OS-managed filesystem",
        }

    # ------------------------------------------------------------------
    # create_container
    # ------------------------------------------------------------------

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
    ) -> dict:
        """Create a systemd service unit for the agent.

        The created unit is a STUB — its ``ExecStart`` is a placeholder
        (``/bin/false``) so a bare ``start_container`` will fail rather
        than silently report success with nothing running.  The deployer
        is expected to follow up with ``push_file`` and
        ``exec_in_container`` to install the agent payload, then call
        ``set_env`` to update ``ExecStart`` before starting.

        The ``image`` parameter is ignored on bare metal (the host *is*
        the image).

        Returns ``{"success": False, "name": name, "note": ...}`` when
        the unit directory is not writable or systemd is unavailable —
        the caller must not proceed assuming a working service.
        """
        if not _has_systemd():
            return {
                "success": False,
                "name": name,
                "error": (
                    "systemd not available on this host. "
                    "bare-metal backend requires systemd to manage agent services."
                ),
            }

        if not _systemd_dir_is_writable():
            return {
                "success": False,
                "name": name,
                "error": (
                    f"{_SYSTEMD_DIR} is not writable. "
                    "bare-metal backend needs write access to the systemd unit directory."
                ),
            }

        unit_path = _service_unit_path(name)
        # Build environment lines — reject newlines in values (they can
        # inject arbitrary systemd directives into the unit file).
        env_lines = ""
        for key, value in (env or {}).items():
            err = _validate_env_value(key, value)
            if err is not None:
                return {"success": False, "name": name, "error": err}
            env_lines += f"Environment={key}={value}\n"

        memory_limit_line = ""
        if memory_limit is not None:
            memory_limit_line = f"MemoryLimit={memory_limit}\n"

        cpu_limit_line = ""
        if cpu_limit is not None:
            cpu_limit_line = f"CPUQuota={cpu_limit * 100}%\n"

        # ExecStart is a sentinel: /bin/false exits 1 immediately so a
        # bare start_container call fails instead of silently succeeding
        # with nothing running.  The deployer MUST update ExecStart to a
        # real payload (via set_env or direct unit-file edit) before
        # starting the agent.
        unit_content = f"""[Unit]
Description=taOS Agent: {name}
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/bin/false
Restart=no
{memory_limit_line}{cpu_limit_line}{env_lines}
[Install]
WantedBy=multi-user.target
"""

        try:
            _SYSTEMD_DIR.mkdir(parents=True, exist_ok=True)
            unit_path.write_text(unit_content)
            code, output = await _run(["systemctl", "daemon-reload"], timeout=15)
            if code != 0:
                logger.warning("daemon-reload failed: %s", output)
                # Not fatal — the unit is still on disk.
        except OSError as exc:
            logger.error("Failed to write unit file %s: %s", unit_path, exc)
            return {"success": False, "error": str(exc)}

        logger.info("bare-metal: created systemd unit %s (stub — update ExecStart before starting)", name)
        return {
            "success": True,
            "name": name,
            "note": (
                "stub unit created with ExecStart=/bin/false. "
                "update ExecStart (via set_env or direct edit) to a real agent "
                "payload before calling start_container."
            ),
        }

    # ------------------------------------------------------------------
    # exec_in_container
    # ------------------------------------------------------------------

    async def exec_in_container(
        self, name: str, cmd: list[str], timeout: int = 300
    ) -> tuple[int, str]:
        """Execute a command directly on the host.

        The *name* parameter is ignored — all commands run on the bare
        host with no isolation.  See the class-level SECURITY note.
        """
        return await _run(cmd, timeout=timeout)

    # ------------------------------------------------------------------
    # push_file
    # ------------------------------------------------------------------

    async def push_file(
        self, name: str, local_path: str, remote_path: str
    ) -> tuple[int, str]:
        """Copy a file to the host filesystem.

        The *name* parameter is ignored — files are copied directly.
        ``remote_path`` MUST resolve within :data:`_ALLOWED_WRITE_ROOTS`
        or the call is rejected with an error.  See the class-level
        SECURITY note.
        """
        safe = _safe_host_path(remote_path)
        if safe is None:
            return 1, (
                f"push_file rejected unsafe path {remote_path!r}. "
                f"remote_path must be an absolute path within one of: "
                f"{', '.join(_ALLOWED_WRITE_ROOTS)}"
            )
        try:
            remote_dir = os.path.dirname(safe)
            if remote_dir:
                os.makedirs(remote_dir, exist_ok=True)
            shutil.copy2(local_path, safe)
            return 0, ""
        except OSError as exc:
            return 1, str(exc)

    # ------------------------------------------------------------------
    # start / stop / restart / destroy
    # ------------------------------------------------------------------

    async def start_container(self, name: str) -> dict:
        """Start the systemd service for *name*."""
        if not _has_systemd():
            return {"success": False, "output": "systemd not available; service not started"}
        unit_path = _service_unit_path(name)
        if not unit_path.exists():
            return {"success": False, "output": f"unit {name}.service not found"}
        code, output = await _run(["systemctl", "start", f"{name}.service"], timeout=30)
        return {"success": code == 0, "output": output}

    async def stop_container(self, name: str, force: bool = False) -> dict:
        """Stop the systemd service for *name*."""
        if not _has_systemd():
            return {"success": True, "output": "systemd not available"}
        cmd = ["systemctl", "stop", f"{name}.service"]
        if force:
            cmd.insert(1, "--force")
        code, output = await _run(cmd, timeout=30)
        return {"success": code == 0, "output": output}

    async def restart_container(self, name: str) -> dict:
        """Restart the systemd service for *name*."""
        if not _has_systemd():
            return {"success": True, "output": "systemd not available"}
        code, output = await _run(
            ["systemctl", "restart", f"{name}.service"], timeout=30,
        )
        return {"success": code == 0, "output": output}

    async def destroy_container(self, name: str) -> dict:
        """Stop and delete the systemd unit for *name*."""
        if not _has_systemd():
            return {"success": True, "output": "systemd not available"}
        unit_path = _service_unit_path(name)
        # Stop first
        await _run(["systemctl", "stop", f"{name}.service"], timeout=30)
        # Disable
        await _run(["systemctl", "disable", f"{name}.service"], timeout=30)
        # Remove unit file
        try:
            unit_path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("Failed to remove unit %s: %s", unit_path, exc)
        await _run(["systemctl", "daemon-reload"], timeout=15)
        return {"success": True, "output": f"destroyed {name}"}

    # ------------------------------------------------------------------
    # get_container_logs
    # ------------------------------------------------------------------

    async def get_container_logs(self, name: str, lines: int = 100) -> str:
        """Get recent journalctl logs for the service."""
        if not _has_systemd():
            return "systemd not available; no logs"
        code, output = await _run(
            ["journalctl", "--no-pager", "-n", str(lines),
             "-u", f"{name}.service"],
            timeout=30,
        )
        return output if code == 0 else f"Error getting logs: {output}"

    # ------------------------------------------------------------------
    # rename_container
    # ------------------------------------------------------------------

    async def rename_container(self, old_name: str, new_name: str) -> dict:
        """Rename the systemd service unit file."""
        if not _has_systemd():
            return {"success": True, "output": "systemd not available"}
        old_path = _service_unit_path(old_name)
        new_path = _service_unit_path(new_name)
        if not old_path.exists():
            return {"success": False, "output": f"unit {old_name}.service not found"}
        try:
            old_path.rename(new_path)
            await _run(["systemctl", "daemon-reload"], timeout=15)
            return {"success": True, "output": f"renamed {old_name} -> {new_name}"}
        except OSError as exc:
            return {"success": False, "output": str(exc)}

    # ------------------------------------------------------------------
    # add_proxy_device
    # ------------------------------------------------------------------

    async def add_proxy_device(
        self, name: str, device_name: str, listen: str, connect: str,
        bind_mode: str | None = None,
    ) -> dict:
        """No-op on bare metal — everything is localhost already."""
        return {"success": True, "output": "proxy devices not needed on bare metal"}

    # ------------------------------------------------------------------
    # snapshots
    # ------------------------------------------------------------------

    async def snapshot_create(self, name: str, snapshot_name: str) -> dict:
        """Snapshots not supported on bare metal."""
        return {
            "success": False,
            "output": "",
            "note": "snapshots not supported on bare metal; use host-level backup tools",
        }

    async def snapshot_restore(self, name: str, snapshot_name: str) -> dict:
        """Snapshots not supported on bare metal."""
        return {
            "success": False,
            "output": "",
            "note": "snapshots not supported on bare metal",
        }

    async def snapshot_list(self, name: str) -> dict:
        """Snapshots not supported on bare metal."""
        return {"success": False, "snapshots": [], "output": "not supported on bare metal"}

    # ------------------------------------------------------------------
    # spawn_pty
    # ------------------------------------------------------------------

    def spawn_pty(self, name: str, cmd: list[str] | None = None) -> PtyHandle:
        """Open an interactive PTY directly on the host.

        *name* is ignored — the PTY runs on the bare host.
        See the class-level SECURITY note.
        """
        master_fd, slave_fd = pty.openpty()
        shell_cmd = "exec bash -l" if cmd is None else " ".join(shlex.quote(c) for c in cmd)
        proc = subprocess.Popen(
            ["bash", "-lc", shell_cmd],
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            close_fds=True,
        )
        os.close(slave_fd)
        return _NativePtyHandle(proc, master_fd)

    # ------------------------------------------------------------------
    # set_env
    # ------------------------------------------------------------------

    async def set_env(self, name: str, key: str, value: str) -> dict:
        """Update an Environment= line in the systemd service unit.

        Systemd does not support hot-reload of environment variables on a
        running service — the service must be restarted to pick up the
        change.  The unit file is updated in-place and daemon-reloaded.

        Special handling for ``ExecStart``: when *key* is ``ExecStart``,
        the ``ExecStart=`` line in the ``[Service]`` section is replaced
        rather than treated as an ``Environment=`` entry.  This lets the
        deployer wire the stub unit to a real agent payload after install.
        """
        if not _has_systemd():
            return {
                "success": True,
                "output": "systemd not available; env not persisted",
            }
        unit_path = _service_unit_path(name)
        if not unit_path.exists():
            return {"success": False, "output": f"unit {name}.service not found"}

        # Reject newlines in env values — they can inject arbitrary
        # systemd directives into the unit file.
        err = _validate_env_value(key, value)
        if err is not None:
            return {"success": False, "output": err}

        try:
            content = unit_path.read_text()
        except OSError as exc:
            return {"success": False, "output": str(exc)}

        is_exec_start = key == "ExecStart"

        if is_exec_start:
            # Replace the ExecStart= line in [Service] section.
            old_prefix = "ExecStart="
            lines = content.splitlines()
            new_lines = []
            for line in lines:
                stripped = line.strip()
                if stripped.startswith(old_prefix):
                    new_lines.append(f"ExecStart={value}")
                else:
                    new_lines.append(line)
            content = "\n".join(new_lines) + "\n"
        else:
            # Environment variable path
            env_line = f"Environment={key}={value}"
            old_prefix = f"Environment={key}="
            if old_prefix in content:
                # Replace the existing line
                lines = content.splitlines()
                new_lines = []
                for line in lines:
                    if line.strip().startswith(old_prefix):
                        new_lines.append(env_line)
                    else:
                        new_lines.append(line)
                content = "\n".join(new_lines) + "\n"
            else:
                # Insert before [Install] or at end
                if "\n[Install]" in content:
                    content = content.replace("\n[Install]", f"\n{env_line}\n[Install]")
                else:
                    content = content.rstrip("\n") + f"\n{env_line}\n"

        try:
            unit_path.write_text(content)
            await _run(["systemctl", "daemon-reload"], timeout=15)
        except OSError as exc:
            return {"success": False, "output": str(exc)}

        return {"success": True, "output": f"set {key}={value} in {name}.service"}

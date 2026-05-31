"""LXC container backend using the incus CLI."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import pty
import select
import shlex
import signal
import subprocess

from .backend import ContainerBackend, ContainerInfo, PtyHandle, _parse_memory

logger = logging.getLogger(__name__)


class _IncusPtyHandle(PtyHandle):
    """PtyHandle backed by a real incus exec subprocess with a pseudo-tty."""

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
        try:
            self._proc.send_signal(signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            os.close(self._master_fd)
        except OSError:
            pass
        self._proc.wait(timeout=5)


def _open_incus_pty(container: str, shell_cmd: str) -> _IncusPtyHandle:
    """Open an incus exec session with a real PTY."""
    master_fd, slave_fd = pty.openpty()
    proc = subprocess.Popen(
        ["incus", "exec", "--tty", "--interactive", container, "--",
         "bash", "-lc", shell_cmd],
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        close_fds=True,
    )
    os.close(slave_fd)
    return _IncusPtyHandle(proc, master_fd)


async def _run(cmd: list[str], timeout: int = 120) -> tuple[int, str]:
    """Run a command and return (returncode, output)."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    return proc.returncode, stdout.decode() if stdout else ""


class LXCBackend(ContainerBackend):
    """Container backend that talks to incus via CLI."""

    async def _run(self, cmd: list[str], timeout: int = 120) -> tuple[int, str]:
        return await _run(cmd, timeout=timeout)

    async def set_root_quota(self, name: str, size_gib: int) -> dict:
        """Set per-container rootfs quota via incus config device override/set root size.

        On btrfs-backed pools, the quota is enforced by btrfs qgroups.
        On ZFS pools, by ZFS dataset quotas. On dir-backed pools incus
        does not enforce the limit at the kernel level (accounting-only);
        callers should warn users of this limitation.

        Uses ``incus config device override`` to create a per-instance copy of
        the root device when it is inherited from a profile (plain ``device set``
        is rejected in that case). Falls back to ``device set`` if an override
        is already present.
        """
        # Override the profile-inherited root disk device on this instance,
        # then set the size. `override` creates a per-instance copy of the
        # device if it doesn't already have one.
        code, output = await _run([
            "incus", "config", "device", "override", name, "root",
            f"size={size_gib}GiB",
        ])
        # If override fails because a per-instance root device already exists,
        # fall back to plain `set` which works in that case.
        if code != 0 and "already exists" in output.lower():
            code, output = await _run([
                "incus", "config", "device", "set", name, "root",
                f"size={size_gib}GiB",
            ])
        if code != 0:
            logger.warning("set_root_quota: incus config device override/set failed for %s: %s", name, output)
            return {"success": False, "note": output}
        return {"success": True, "note": f"root quota set to {size_gib} GiB"}

    async def list_containers(self, prefix: str = "taos-agent-") -> list[ContainerInfo]:
        """List all agent containers."""
        code, output = await _run(["incus", "list", "-f", "json"])
        if code != 0:
            logger.error(f"incus list failed: {output}")
            return []
        try:
            containers = json.loads(output)
        except json.JSONDecodeError:
            return []
        results = []
        for c in containers:
            name = c.get("name", "")
            if not name.startswith(prefix):
                continue
            status = c.get("status", "Unknown")
            ip = None
            network = c.get("state", {}).get("network", {})
            for iface in network.values():
                for addr in iface.get("addresses", []):
                    if addr.get("family") == "inet" and addr.get("scope") == "global":
                        ip = addr.get("address")
                        break
                if ip:
                    break
            config = c.get("config", {})
            mem_str = config.get("limits.memory", "0")
            memory_mb = _parse_memory(mem_str)
            cpu_str = config.get("limits.cpu", "0")
            cpu_cores = int(cpu_str) if cpu_str.isdigit() else 0
            results.append(ContainerInfo(
                name=name, status=status, ip=ip,
                memory_mb=memory_mb, cpu_cores=cpu_cores,
            ))
        return results

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
        """Create and start a new LXC container.

        Bind mounts are attached as disk devices via ``incus config device
        add`` and env vars via ``incus config set environment.KEY VALUE``.

        root_size_gib: when set, apply a rootfs disk quota via
        ``incus config device set root size=<N>GiB`` immediately after
        launch. On btrfs/ZFS-backed pools this is enforced at the kernel
        level; on dir-backed pools incus does not enforce it.

        host_uid: when set, apply ``raw.idmap`` so that container root (uid 0)
        maps to this uid on the host.  Required when bind-mounting directories
        owned by a non-root host user (e.g. the taOS process user) so that
        the container can write to them.  The container is stopped, the idmap
        is applied, then it is restarted before mounts are attached.
        """
        # LXC publishes ports via add_proxy_device, not at create time.
        _ = ports
        code, output = await _run(
            ["incus", "launch", image, name], timeout=300,
        )
        if code != 0:
            return {"success": False, "error": output}

        if host_uid is not None:
            # Apply uid/gid mapping: container root -> host_uid.  This
            # requires a stop/start cycle to take effect.
            await _run([
                "incus", "config", "set", name, "raw.idmap",
                f"both {host_uid} 0",
            ])
            await _run(["incus", "stop", name, "--force"])
            await _run(["incus", "start", name])
            import asyncio as _asyncio
            await _asyncio.sleep(3)

        # Root quota — set before mounts/env so any subsequent writes are
        # already subject to the limit.
        if root_size_gib is not None:
            quota_result = await self.set_root_quota(name, root_size_gib)
            if not quota_result["success"]:
                logger.warning(
                    "create_container: root quota not applied for %s: %s",
                    name, quota_result.get("note", ""),
                )

        if memory_limit is not None:
            await _run(["incus", "config", "set", name, "limits.memory", memory_limit])
        if cpu_limit is not None:
            await _run(["incus", "config", "set", name, "limits.cpu", str(cpu_limit)])

        for idx, (host_path, container_path) in enumerate(mounts or []):
            device_name = f"taos-mount-{idx}"
            mcode, mout = await _run([
                "incus", "config", "device", "add", name, device_name, "disk",
                f"source={host_path}", f"path={container_path}",
            ])
            if mcode != 0:
                logger.error(f"incus mount {host_path}->{container_path} failed: {mout}")

        for key, value in (env or {}).items():
            ecode, eout = await _run([
                "incus", "config", "set", name, f"environment.{key}={value}",
            ])
            if ecode != 0:
                logger.error(f"incus env set {key} failed: {eout}")

        return {"success": True, "name": name}

    async def exec_in_container(
        self, name: str, cmd: list[str], timeout: int = 300
    ) -> tuple[int, str]:
        """Execute a command inside a container."""
        return await _run(["incus", "exec", name, "--"] + cmd, timeout=timeout)

    async def push_file(
        self, name: str, local_path: str, remote_path: str
    ) -> tuple[int, str]:
        """Push a file into a container."""
        return await _run(["incus", "file", "push", local_path, f"{name}{remote_path}"])

    async def start_container(self, name: str) -> dict:
        code, output = await _run(["incus", "start", name])
        return {"success": code == 0, "output": output}

    async def stop_container(self, name: str, force: bool = False) -> dict:
        cmd = ["incus", "stop", name]
        if force:
            cmd.append("--force")
        code, output = await _run(cmd)
        return {"success": code == 0, "output": output}

    async def restart_container(self, name: str) -> dict:
        code, output = await _run(["incus", "restart", name])
        return {"success": code == 0, "output": output}

    async def destroy_container(self, name: str) -> dict:
        """Stop and delete a container."""
        await _run(["incus", "stop", name, "--force"])
        code, output = await _run(["incus", "delete", name, "--force"])
        return {"success": code == 0, "output": output}

    async def get_container_logs(self, name: str, lines: int = 100) -> str:
        """Get recent logs from a container's journal."""
        code, output = await self.exec_in_container(
            name, ["journalctl", "--no-pager", "-n", str(lines)], timeout=30,
        )
        return output if code == 0 else f"Error getting logs: {output}"

    async def rename_container(self, old_name: str, new_name: str) -> dict:
        code, output = await _run(["incus", "rename", old_name, new_name])
        return {"success": code == 0, "output": output}

    async def add_proxy_device(
        self, name: str, device_name: str, listen: str, connect: str,
        bind_mode: str | None = None,
    ) -> dict:
        cmd = [
            "incus", "config", "device", "add", name, device_name, "proxy",
            f"listen={listen}",
            f"connect={connect}",
        ]
        if bind_mode:
            cmd.append(f"bind={bind_mode}")
        code, output = await _run(cmd)
        return {"success": code == 0, "output": output}

    async def snapshot_create(self, name: str, snapshot_name: str) -> dict:
        """Create a named snapshot of the container via incus.

        The container must already be stopped before snapshotting; callers
        are responsible for stopping it first.  On btrfs/ZFS-backed pools
        this is a zero-copy COW operation; on dir-backed pools incus does a
        full rsync of the rootfs.
        """
        code, output = await _run(["incus", "snapshot", "create", name, snapshot_name])
        return {"success": code == 0, "output": output}

    async def snapshot_restore(self, name: str, snapshot_name: str) -> dict:
        """Restore the container to a previously-created snapshot.

        The container must be stopped.  The restore is in-place — the
        running filesystem is discarded and replaced with the snapshot's
        state. Subsequent snapshots taken after the restored one are not
        affected (they remain accessible for re-restore if needed).
        """
        code, output = await _run(["incus", "snapshot", "restore", name, snapshot_name])
        return {"success": code == 0, "output": output}

    async def snapshot_list(self, name: str) -> dict:
        """Return snapshot names for a container by parsing ``incus info``."""
        code, output = await _run(["incus", "info", name])
        if code != 0:
            return {"success": False, "snapshots": [], "output": output}
        snapshots: list[str] = []
        in_section = False
        for line in output.splitlines():
            stripped = line.strip()
            if stripped.lower().startswith("snapshots:"):
                in_section = True
                continue
            if in_section:
                # Stop at the next top-level section (no leading whitespace).
                if line and not line[0].isspace():
                    break
                if stripped and not stripped.startswith("-"):
                    # lines like "  name-here (created ...)"
                    snap_name = stripped.split()[0]
                    snapshots.append(snap_name)
        return {"success": True, "snapshots": snapshots, "output": output}

    def spawn_pty(self, name: str, cmd: list[str] | None = None) -> PtyHandle:
        container = f"taos-agent-{name}"
        if cmd is None:
            shell_cmd = "exec bash -l"
        else:
            shell_cmd = " ".join(shlex.quote(c) for c in cmd)
        return _open_incus_pty(container, shell_cmd)

    async def set_env(self, name: str, key: str, value: str) -> dict:
        """Set an environment variable on the container via incus config set.

        The variable is persisted in incus config and injected into the
        container's environment on next start (or on restart of individual
        services inside the container).
        """
        code, output = await _run([
            "incus", "config", "set", name, f"environment.{key}={value}",
        ])
        return {"success": code == 0, "output": output}

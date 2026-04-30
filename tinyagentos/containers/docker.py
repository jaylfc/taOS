"""Docker/Podman container backend using the docker CLI."""
from __future__ import annotations

import asyncio
import json
import logging

from .backend import ContainerBackend, ContainerInfo, _parse_memory

logger = logging.getLogger(__name__)


class DockerBackend(ContainerBackend):
    """Container backend that talks to docker or podman via CLI.

    Pass binary='podman' to use Podman instead of Docker.
    """

    def __init__(self, binary: str = "docker") -> None:
        self.binary = binary

    async def _run(self, cmd: list[str], timeout: int = 120) -> tuple[int, str]:
        """Run a command and return (returncode, output)."""
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode, stdout.decode() if stdout else ""

    async def list_containers(self, prefix: str = "taos-agent-") -> list[ContainerInfo]:
        """List all containers whose name starts with prefix."""
        code, output = await self._run([
            self.binary, "ps", "-a",
            "--filter", f"name={prefix}",
            "--format", "{{json .}}",
        ])
        if code != 0:
            logger.error(f"{self.binary} ps failed: {output}")
            return []

        results = []
        for line in output.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue

            name = item.get("Names", item.get("Name", ""))
            # docker ps --format may return comma-separated names
            if isinstance(name, str):
                name = name.lstrip("/").split(",")[0].strip()

            if not name.startswith(prefix):
                continue

            state = item.get("State", item.get("Status", "unknown"))

            # Fetch IP via inspect
            ip = None
            icode, iout = await self._run([self.binary, "inspect", name])
            if icode == 0:
                try:
                    inspect = json.loads(iout)
                    if inspect:
                        networks = (
                            inspect[0]
                            .get("NetworkSettings", {})
                            .get("Networks", {})
                        )
                        for net in networks.values():
                            addr = net.get("IPAddress", "")
                            if addr:
                                ip = addr
                                break
                except (json.JSONDecodeError, IndexError, KeyError):
                    pass

            results.append(ContainerInfo(
                name=name,
                status=state,
                ip=ip,
                memory_mb=0,
                cpu_cores=0,
            ))
        return results

    async def set_root_quota(self, name: str, size_gib: int) -> dict:
        """Set per-container rootfs quota via docker container update.

        Docker supports ``--storage-opt size=<n>g`` only on drivers that
        provide per-container quota (devicemapper, overlay2 on XFS with
        pquota, btrfs). On overlay2 without pquota the command is
        accepted but silently not enforced; we treat that as success with
        a soft note.
        """
        size_g = f"{size_gib}g"
        code, output = await self._run([
            self.binary, "container", "update",
            "--storage-opt", f"size={size_g}", name,
        ])
        if code != 0:
            # Specific well-known message from overlay2 without pquota.
            no_support_markers = (
                "storage driver does not support",
                "storage-opt",
                "not supported",
                "invalid option",
            )
            low = output.lower()
            if any(m in low for m in no_support_markers):
                logger.warning(
                    "set_root_quota: storage driver does not enforce quota for %s "
                    "(overlay2 without pquota?); treating as soft limit: %s",
                    name, output.strip(),
                )
                return {"success": True, "note": "storage driver does not enforce quota"}
            logger.warning("set_root_quota: docker update failed for %s: %s", name, output)
            return {"success": False, "note": output}
        return {"success": True, "note": f"root quota set to {size_gib} GiB"}

    async def create_container(
        self,
        name: str,
        image: str = "ubuntu:22.04",
        memory_limit: str | None = None,
        cpu_limit: int | None = None,
        mounts: list[tuple[str, str]] | None = None,
        env: dict[str, str] | None = None,
        host_uid: int | None = None,
        root_size_gib: int | None = None,
    ) -> dict:
        """Create and start a new container.

        Every bind mount and env var the container needs must be passed
        explicitly — nothing is baked into the image.

        root_size_gib: when set, call set_root_quota after the container
        is created. Enforcement depends on the Docker storage driver;
        on overlay2 without pquota this is accounting-only.
        """
        args = [
            self.binary, "run", "-d",
            "--name", name,
        ]
        if memory_limit is not None:
            args += ["--memory", memory_limit]
        if cpu_limit is not None:
            args += ["--cpus", str(cpu_limit)]
        for host_path, container_path in mounts or []:
            args += ["-v", f"{host_path}:{container_path}"]
        for key, value in (env or {}).items():
            args += ["-e", f"{key}={value}"]
        args.append(image)
        code, output = await self._run(args, timeout=300)
        if code != 0:
            return {"success": False, "error": output}

        # Root quota — set after container exists, before it receives writes.
        if root_size_gib is not None:
            quota_result = await self.set_root_quota(name, root_size_gib)
            if not quota_result["success"]:
                logger.warning(
                    "create_container: root quota not applied for %s: %s",
                    name, quota_result.get("note", ""),
                )

        return {"success": True, "name": name}

    async def exec_in_container(
        self, name: str, cmd: list[str], timeout: int = 300
    ) -> tuple[int, str]:
        """Execute a command inside a container."""
        return await self._run([self.binary, "exec", name] + cmd, timeout=timeout)

    async def push_file(
        self, name: str, local_path: str, remote_path: str
    ) -> tuple[int, str]:
        """Copy a file into a container."""
        return await self._run([self.binary, "cp", local_path, f"{name}:{remote_path}"])

    async def start_container(self, name: str) -> dict:
        code, output = await self._run([self.binary, "start", name])
        return {"success": code == 0, "output": output}

    async def stop_container(self, name: str, force: bool = False) -> dict:
        cmd = [self.binary, "kill", name] if force else [self.binary, "stop", name]
        code, output = await self._run(cmd)
        return {"success": code == 0, "output": output}

    async def restart_container(self, name: str) -> dict:
        code, output = await self._run([self.binary, "restart", name])
        return {"success": code == 0, "output": output}

    async def destroy_container(self, name: str) -> dict:
        """Force-remove a container."""
        code, output = await self._run([self.binary, "rm", "-f", name])
        return {"success": code == 0, "output": output}

    async def get_container_logs(self, name: str, lines: int = 100) -> str:
        """Get recent log output from a container."""
        code, output = await self._run([
            self.binary, "logs", name, "--tail", str(lines),
        ])
        return output if code == 0 else f"Error getting logs: {output}"

    async def rename_container(self, old_name: str, new_name: str) -> dict:
        code, output = await self._run([self.binary, "rename", old_name, new_name])
        return {"success": code == 0, "output": output}

    async def add_proxy_device(
        self, name: str, device_name: str, listen: str, connect: str,
        bind_mode: str | None = None,
    ) -> dict:
        # Docker containers reach the host via docker-provided networking
        # (host.docker.internal on Mac/Windows, --add-host on Linux). No
        # proxy device equivalent; the deployer's docker path is expected
        # to choose a different TAOS_HOST default. Return success so the
        # deploy doesn't stall.
        return {"success": True, "output": "proxy devices not supported on docker"}

    async def snapshot_create(self, name: str, snapshot_name: str) -> dict:
        """Create a named image snapshot via ``docker commit``.

        Docker has no native snapshot primitive equivalent to incus snapshots.
        We commit the container to an image tagged ``taos/<snapshot_name>:latest``
        so the image survives across container deletions. Restore is not
        currently supported (see ``snapshot_restore``).
        """
        image_tag = f"taos/{snapshot_name}:latest"
        code, output = await self._run([self.binary, "commit", name, image_tag])
        return {"success": code == 0, "output": output}

    async def snapshot_restore(self, name: str, snapshot_name: str) -> dict:
        """Docker does not support in-place snapshot restore.

        Restoring from a committed image would require stopping the running
        container, creating a new one from the image, reattaching all bind
        mounts and env vars, and renaming — a destructive multi-step process
        that is not safe to automate without more context. Fall back to the
        caller's rsync-based archive path instead (per pivot doc §3.1).
        """
        return {
            "success": False,
            "output": "",
            "note": "docker snapshot restore not supported; use rsync-based archive fallback",
        }

    async def snapshot_list(self, name: str) -> dict:
        """List committed snapshot images in the ``taos/`` namespace."""
        code, output = await self._run([
            self.binary, "images", "--format", "{{.Repository}}:{{.Tag}}",
            "--filter", "reference=taos/*",
        ])
        if code != 0:
            return {"success": False, "snapshots": [], "output": output}
        snapshots = [
            line.strip()
            for line in output.splitlines()
            if line.strip()
        ]
        return {"success": True, "snapshots": snapshots, "output": output}

    def spawn_pty(self, name: str, cmd: list[str] | None = None):
        raise NotImplementedError("docker spawn_pty not yet implemented")

    async def set_env(self, name: str, key: str, value: str) -> dict:
        """Docker does not support per-container env changes without recreating.

        Changing an environment variable on a running Docker container requires
        stopping it, deleting it, and re-running ``docker run`` with the updated
        ``-e`` flags plus all the original mounts and options. This is not safe
        to automate transparently. The caller should recreate the container or
        use a bind-mounted env file instead.
        """
        return {
            "success": False,
            "output": "",
            "note": "docker env change requires container recreation; use a bind-mounted env file",
        }

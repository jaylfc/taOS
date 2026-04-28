"""Apple Containerization backend — shells out to apple/container CLI.

The Mac .app launcher injects ``TAOS_CONTAINER_BIN`` pointing at the
bundled CLI under ``Contents/Resources/bin/container``. On developer
machines without the .app, falls back to ``container`` on ``PATH``.

All ``subprocess`` calls go through ``asyncio.create_subprocess_exec``
(no shell). Failure shape matches the other backends:
``{success: bool, output: str, note?: str}``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os

from .backend import ContainerBackend, ContainerInfo, _parse_memory

logger = logging.getLogger(__name__)


class AppleContainerBackend(ContainerBackend):
    def __init__(self) -> None:
        self.binary = os.environ.get("TAOS_CONTAINER_BIN", "container")

    async def _run(self, cmd: list[str], timeout: int = 120) -> tuple[int, str]:
        """Run a command and return (returncode, output)."""
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode, stdout.decode() if stdout else ""

    # All ABC methods raise NotImplementedError until subsequent tasks.
    async def list_containers(self, prefix: str = "taos-agent-") -> list[ContainerInfo]:
        """List all containers whose name starts with prefix."""
        code, output = await self._run([self.binary, "ls", "-a", "--format", "json"])
        if code != 0:
            logger.error("apple container ls failed: %s", output)
            return []
        try:
            items = json.loads(output) if output.strip() else []
        except json.JSONDecodeError:
            logger.error("apple container ls returned non-JSON: %s", output[:200])
            return []

        results: list[ContainerInfo] = []
        for it in items:
            name = it.get("name", "")
            if not name.startswith(prefix):
                continue
            results.append(
                ContainerInfo(
                    name=name,
                    status=it.get("status", "unknown"),
                    ip=it.get("ip"),
                    memory_mb=_parse_memory(str(it.get("memory", "0"))),
                    cpu_cores=int(it.get("cpus", 0) or 0),
                )
            )
        return results

    async def set_root_quota(self, name: str, size_gib: int) -> dict:
        raise NotImplementedError

    async def create_container(
        self,
        name: str,
        image: str = "docker.io/library/debian:bookworm",
        memory_limit: str | None = None,
        cpu_limit: int | None = None,
        mounts: list[tuple[str, str]] | None = None,
        env: dict[str, str] | None = None,
        host_uid: int | None = None,
        root_size_gib: int | None = None,
    ) -> dict:
        argv = [self.binary, "run", "-d", "--name", name]
        if memory_limit:
            # Convert "2GB"/"512MB" → "2g"/"512m" for Apple CLI
            ml = memory_limit.strip().lower().replace("gb", "g").replace("mb", "m")
            argv += ["--memory", ml]
        if cpu_limit:
            argv += ["--cpus", str(cpu_limit)]
        for host_path, guest_path in mounts or []:
            argv += ["-v", f"{host_path}:{guest_path}"]
        for key, value in (env or {}).items():
            argv += ["-e", f"{key}={value}"]
        argv.append(image)

        code, output = await self._run(argv)
        if code != 0:
            return {"success": False, "output": output}

        if root_size_gib is not None:
            try:
                quota_result = await self.set_root_quota(name, root_size_gib)
                if isinstance(quota_result, dict) and not quota_result.get("success"):
                    logger.warning(
                        "set_root_quota for %s did not succeed: %s",
                        name,
                        quota_result.get("note") or quota_result.get("output"),
                    )
            except NotImplementedError:
                logger.warning(
                    "set_root_quota not yet implemented on Apple backend; "
                    "ignoring root_size_gib=%s for %s",
                    root_size_gib,
                    name,
                )

        return {"success": True, "output": output.strip()}

    async def exec_in_container(
        self, name: str, cmd: list[str], timeout: int = 300
    ) -> tuple[int, str]:
        return await self._run([self.binary, "exec", name, *cmd], timeout=timeout)

    async def push_file(
        self, name: str, local_path: str, remote_path: str
    ) -> tuple[int, str]:
        return await self._run(
            [self.binary, "cp", local_path, f"{name}:{remote_path}"]
        )

    async def start_container(self, name: str) -> dict:
        code, output = await self._run([self.binary, "start", name])
        return {"success": code == 0, "output": output}

    async def stop_container(self, name: str, force: bool = False) -> dict:
        verb = "kill" if force else "stop"
        code, output = await self._run([self.binary, verb, name])
        return {"success": code == 0, "output": output}

    async def restart_container(self, name: str) -> dict:
        code, output = await self._run([self.binary, "restart", name])
        return {"success": code == 0, "output": output}

    async def destroy_container(self, name: str) -> dict:
        code, output = await self._run([self.binary, "rm", "-f", name])
        return {"success": code == 0, "output": output}

    async def get_container_logs(self, name: str, lines: int = 100) -> str:
        code, output = await self._run(
            [self.binary, "logs", "--tail", str(lines), name]
        )
        return output if code == 0 else f"Error getting logs: {output}"

    async def rename_container(self, old_name: str, new_name: str) -> dict:
        code, output = await self._run(
            [self.binary, "rename", old_name, new_name]
        )
        return {"success": code == 0, "output": output}

    async def add_proxy_device(
        self, name: str, device_name: str, listen: str, connect: str,
        bind_mode: str | None = None,
    ) -> dict:
        raise NotImplementedError

    async def snapshot_create(self, name: str, snapshot_name: str) -> dict:
        raise NotImplementedError

    async def snapshot_restore(self, name: str, snapshot_name: str) -> dict:
        raise NotImplementedError

    async def snapshot_list(self, name: str) -> dict:
        raise NotImplementedError

    async def set_env(self, name: str, key: str, value: str) -> dict:
        raise NotImplementedError

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
import logging
import os

from .backend import ContainerBackend, ContainerInfo

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
        raise NotImplementedError

    async def set_root_quota(self, name: str, size_gib: int) -> dict:
        raise NotImplementedError

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
        raise NotImplementedError

    async def exec_in_container(
        self, name: str, cmd: list[str], timeout: int = 300
    ) -> tuple[int, str]:
        raise NotImplementedError

    async def push_file(
        self, name: str, local_path: str, remote_path: str
    ) -> tuple[int, str]:
        raise NotImplementedError

    async def start_container(self, name: str) -> dict:
        raise NotImplementedError

    async def stop_container(self, name: str, force: bool = False) -> dict:
        raise NotImplementedError

    async def restart_container(self, name: str) -> dict:
        raise NotImplementedError

    async def destroy_container(self, name: str) -> dict:
        raise NotImplementedError

    async def get_container_logs(self, name: str, lines: int = 100) -> str:
        raise NotImplementedError

    async def rename_container(self, old_name: str, new_name: str) -> dict:
        raise NotImplementedError

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

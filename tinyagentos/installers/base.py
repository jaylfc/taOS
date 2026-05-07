from __future__ import annotations

import asyncio
import subprocess
from abc import ABC, abstractmethod


async def run_cmd(cmd: list[str], cwd: str | None = None, timeout: int = 300) -> tuple[int, str]:
    """Run a command asynchronously, return (returncode, output)."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=cwd,
    )
    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    return proc.returncode, stdout.decode() if stdout else ""


class AppInstaller(ABC):
    @abstractmethod
    async def install(self, app_id: str, install_config: dict, **kwargs) -> dict:
        ...

    @abstractmethod
    async def uninstall(self, app_id: str) -> dict:
        ...

    async def start(self, app_id: str) -> dict:
        return {"success": False, "error": "start not supported for this installer"}

    async def stop(self, app_id: str) -> dict:
        return {"success": False, "error": "stop not supported for this installer"}


def get_installer(method: str, **kwargs) -> AppInstaller:
    from tinyagentos.installers.pip_installer import PipInstaller
    from tinyagentos.installers.docker_installer import DockerInstaller
    from tinyagentos.installers.download_installer import DownloadInstaller
    from tinyagentos.installers.lxc_installer import LXCInstaller
    from tinyagentos.installers.rkllama_installer import RkllamaInstaller

    if method == "pip":
        return PipInstaller(**kwargs)
    elif method == "docker":
        return DockerInstaller(**kwargs)
    elif method == "download":
        return DownloadInstaller(**kwargs)
    elif method == "lxc":
        return LXCInstaller(**kwargs)
    elif method == "rkllama":
        return RkllamaInstaller(**kwargs)
    elif method in ("rkllamacpp", "rk-llama.cpp"):
        from tinyagentos.installers.rkllamacpp_installer import RkLlamaCppInstaller
        return RkLlamaCppInstaller(**kwargs)
    elif method == "script":
        from tinyagentos.installers.script_installer import ScriptInstaller
        return ScriptInstaller(**kwargs)
    else:
        raise ValueError(f"Unknown install method: '{method}'")

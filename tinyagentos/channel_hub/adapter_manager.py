from __future__ import annotations
import asyncio
import logging
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

ADAPTER_DIR = Path(__file__).parent.parent / "adapters"

# Channel-level adapters (run in-process via channel-hub connect API, not subprocesses)
CHANNEL_ADAPTER_DIR = Path(__file__).parent / "adapters"
_CHANNEL_ADAPTERS = {
    "github": CHANNEL_ADAPTER_DIR / "github.py",
    # === Discord adapter (issue #335) ===
    "discord": CHANNEL_ADAPTER_DIR / "discord.py",
}

class AdapterManager:
    def __init__(self, router):
        self.router = router
        self._processes: dict[str, subprocess.Popen] = {}

    async def start_adapter(self, agent_name: str, framework: str, env: dict | None = None) -> int:
        port = self.router.allocate_port(agent_name)
        adapter_file = ADAPTER_DIR / f"{framework}_adapter.py"
        if not adapter_file.exists():
            adapter_file = ADAPTER_DIR / "generic_adapter.py"

        process_env = dict(__import__("os").environ)
        if env:
            process_env.update(env)
        process_env["TAOS_ADAPTER_PORT"] = str(port)
        process_env["TAOS_AGENT_NAME"] = agent_name

        proc = subprocess.Popen(
            [sys.executable, str(adapter_file)],
            env=process_env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._processes[agent_name] = proc

        # Wait for adapter to be ready
        for _ in range(10):
            await asyncio.sleep(1)
            try:
                import httpx
                async with httpx.AsyncClient(timeout=3) as client:
                    resp = await client.get(f"http://localhost:{port}/health")
                    if resp.status_code == 200:
                        self.router.register_adapter(agent_name, port)
                        logger.info(f"Adapter started for {agent_name} ({framework}) on port {port}")
                        return port
            except Exception:
                pass

        logger.error(f"Adapter for {agent_name} failed to start")
        return port

    def stop_adapter(self, agent_name: str):
        proc = self._processes.pop(agent_name, None)
        if proc:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()

    def stop_all(self):
        for name in list(self._processes.keys()):
            self.stop_adapter(name)

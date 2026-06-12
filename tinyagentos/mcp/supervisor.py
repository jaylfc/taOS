from __future__ import annotations

import asyncio
import collections
import logging
import signal
import time
from dataclasses import dataclass, field
from typing import AsyncIterator

from tinyagentos.mcp.registry import MCPServerStore

logger = logging.getLogger(__name__)


@dataclass
class ServerProcess:
    process: asyncio.subprocess.Process
    transport: str
    log_buffer: collections.deque = field(
        default_factory=lambda: collections.deque(maxlen=1000)
    )
    started_at: float = field(default_factory=time.time)
    stderr_task: asyncio.Task | None = None


class MCPSupervisor:
    def __init__(self, store: MCPServerStore, catalog, notif_store, secrets_store=None) -> None:
        self._store = store
        self._catalog = catalog
        self._notif_store = notif_store
        self._secrets_store = secrets_store
        self._processes: dict[str, ServerProcess] = {}

    async def start(self, server_id: str) -> bool:
        if server_id in self._processes:
            return True

        server = await self._store.get_server(server_id)
        if server is None:
            logger.error("mcp start: server %s not found in store", server_id)
            return False

        cmd = self._resolve_cmd(server_id, server)
        if not cmd:
            logger.error("mcp start: no launch command for %s", server_id)
            return False

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except Exception as exc:
            logger.exception("mcp start: failed to spawn %s", server_id)
            await self._store.mark_stopped(server_id, error=str(exc))
            return False

        sp = ServerProcess(process=proc, transport=server["transport"])
        self._processes[server_id] = sp

        sp.stderr_task = asyncio.create_task(
            self._drain_stderr(server_id, sp),
            name=f"mcp-stderr-{server_id}",
        )

        await self._store.mark_running(server_id, proc.pid)
        logger.info("mcp: started %s pid=%s", server_id, proc.pid)
        return True

    async def stop(self, server_id: str, timeout: float = 10.0) -> bool:
        sp = self._processes.get(server_id)
        if sp is None:
            return False

        proc = sp.process
        try:
            proc.send_signal(signal.SIGTERM)
        except ProcessLookupError:
            pass

        try:
            await asyncio.wait_for(proc.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.wait()

        exit_code = proc.returncode
        if sp.stderr_task and not sp.stderr_task.done():
            sp.stderr_task.cancel()
            try:
                await sp.stderr_task
            except asyncio.CancelledError:
                pass

        self._processes.pop(server_id, None)
        await self._store.mark_stopped(server_id, exit_code=exit_code)
        logger.info("mcp: stopped %s exit_code=%s", server_id, exit_code)
        return True

    async def restart(self, server_id: str) -> bool:
        await self.stop(server_id)
        return await self.start(server_id)

    async def uninstall(self, server_id: str) -> dict:
        await self.stop(server_id)

        attachments = await self._store.list_attachments(server_id)
        agents_affected = list({
            a["scope_id"] for a in attachments
            if a["scope_kind"] == "agent" and a["scope_id"]
        })

        secrets_dropped = 0
        if self._secrets_store is not None:
            try:
                prefix = f"mcp:{server_id}:"
                all_secrets = await self._secrets_store.list()
                for s in all_secrets:
                    if s["name"].startswith(prefix):
                        await self._secrets_store.delete(s["name"])
                        secrets_dropped += 1
            except Exception:
                logger.exception("mcp uninstall: error cleaning secrets for %s", server_id)

        await self._store.delete_server(server_id)
        return {"agents_affected": agents_affected, "env_secrets_dropped": secrets_dropped}

    async def stop_all(self) -> None:
        sids = list(self._processes.keys())
        if not sids:
            return
        results = await asyncio.gather(
            *(self.stop(sid) for sid in sids),
            return_exceptions=True,
        )
        for sid, result in zip(sids, results):
            if isinstance(result, Exception):
                logger.error("mcp stop failed for %s: %s", sid, result)

    def logs(self, server_id: str, since_idx: int = 0, limit: int = 200) -> list[dict]:
        sp = self._processes.get(server_id)
        if sp is None:
            return []
        buf = list(sp.log_buffer)
        return buf[since_idx : since_idx + limit]

    async def stream_logs(self, server_id: str) -> AsyncIterator[dict]:
        sp = self._processes.get(server_id)
        if sp is None:
            return

        seen = len(sp.log_buffer)
        while server_id in self._processes:
            buf = list(sp.log_buffer)
            if len(buf) > seen:
                for entry in buf[seen:]:
                    yield entry
                seen = len(buf)
            await asyncio.sleep(0.2)

    def get_status(self, server_id: str) -> dict:
        sp = self._processes.get(server_id)
        if sp is None:
            return {"running": False, "pid": None, "uptime_s": None, "log_count": 0}
        return {
            "running": True,
            "pid": sp.process.pid,
            "uptime_s": time.time() - sp.started_at,
            "log_count": len(sp.log_buffer),
        }

    def _resolve_cmd(self, server_id: str, server: dict) -> list[str] | None:
        config = server.get("config", {})
        cmd = config.get("cmd")
        if cmd:
            return cmd if isinstance(cmd, list) else [cmd]
        try:
            if self._catalog is not None:
                manifest = self._catalog.get_manifest(server_id)
                if manifest:
                    lifecycle = getattr(manifest, "lifecycle", {})
                    start_cmd = lifecycle.get("start") or lifecycle.get("cmd")
                    if start_cmd:
                        return start_cmd if isinstance(start_cmd, list) else [start_cmd]
        except Exception:
            pass
        return None

    async def _drain_stderr(self, server_id: str, sp: ServerProcess) -> None:
        assert sp.process.stderr is not None
        try:
            async for raw_line in sp.process.stderr:
                line = raw_line.decode(errors="replace").rstrip()
                level = "error" if any(
                    kw in line.lower() for kw in ("error", "exception", "traceback", "fatal")
                ) else "info"
                entry = {
                    "idx": len(sp.log_buffer),
                    "ts": time.time(),
                    "level": level,
                    "line": line,
                }
                sp.log_buffer.append(entry)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("mcp stderr drain error for %s", server_id)
        finally:
            if server_id in self._processes:
                exit_code = sp.process.returncode
                self._processes.pop(server_id, None)
                await self._store.mark_stopped(server_id, exit_code=exit_code)

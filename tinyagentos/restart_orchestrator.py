from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Literal

import httpx

logger = logging.getLogger(__name__)

Reason = Literal["pause", "stop", "system-shutdown"]


def _pending_restart_path() -> Path:
    """Return the path for the pending-restart flag file.

    Resolution order:
    1. ``$TAOS_DATA_DIR/pending-restart.json`` — preferred when the process runs
       as the non-root ``taos`` service user, which has no real home directory.
       The systemd unit sets ``WorkingDirectory`` to the install dir; the data
       dir is always ``<install_dir>/data``, which ``taos`` owns.
    2. ``<install_dir>/data/pending-restart.json`` — derived from this module's
       location (``tinyagentos/restart_orchestrator.py`` → ``../../data``).
       Matches the ``PROJECT_DIR / "data"`` convention used throughout app.py.
    3. ``~/.config/taos/pending-restart.json`` — backward-compatible fallback
       for root-based or developer installs where ``TAOS_DATA_DIR`` is unset and
       ``~`` resolves to a writable home directory.
    """
    env_data = os.environ.get("TAOS_DATA_DIR")
    if env_data:
        return Path(env_data) / "pending-restart.json"
    # Derive from module location: tinyagentos/restart_orchestrator.py → ../../data
    install_data = Path(__file__).parent.parent / "data"
    if install_data.is_dir():
        return install_data / "pending-restart.json"
    # Fallback for root/dev installs (taos service user has no usable ~)
    return Path("~/.config/taos/pending-restart.json").expanduser()


def write_pending_restart(target_sha: str) -> None:
    path = _pending_restart_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"target_sha": target_sha, "pulled_at": int(time.time())})
    )


def read_pending_restart() -> dict | None:
    path = _pending_restart_path()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def clear_pending_restart() -> None:
    try:
        _pending_restart_path().unlink(missing_ok=True)
    except Exception:
        pass


class RestartOrchestrator:
    def __init__(self, app_state) -> None:
        self._app_state = app_state
        self._status: dict = {"phase": "idle", "reason": "", "started_at": 0, "agents": {}}

    def get_status(self) -> dict:
        return dict(self._status)

    async def prepare(
        self,
        scope: Literal["all"] | list[str],
        reason: Reason,
    ) -> dict:
        config = self._app_state.config
        notif = self._app_state.notifications
        data_dir: Path = self._app_state.data_dir

        if scope == "all":
            agents = list(config.agents)
        else:
            agents = [a for a in config.agents if a["name"] in scope]

        self._status = {
            "phase": "preparing",
            "reason": reason,
            "started_at": int(time.time()),
            "agents": {a["name"]: {"status": "preparing", "duration_s": 0, "note_path": None} for a in agents},
        }

        await notif.add(
            title="Graceful shutdown started",
            message=f"Preparing {len(agents)} agent(s) — reason: {reason}",
            level="info",
            source="system.lifecycle",
        )

        tasks = [
            asyncio.wait_for(
                self._prepare_agent(a, reason, data_dir),
                timeout=300,
            )
            for a in agents
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        report: dict = {}
        for agent, result in zip(agents, results):
            name = agent["name"]
            if isinstance(result, asyncio.TimeoutError):
                self._status["agents"][name] = {"status": "timeout", "duration_s": 300, "note_path": None}
                await notif.add(
                    title=f"Agent {name} timed out",
                    message=f"Agent did not acknowledge shutdown within 300s (reason: {reason})",
                    level="warning",
                    source="system.lifecycle",
                )
                report[name] = {"status": "timeout", "duration_s": 300, "note_path": None}
            elif isinstance(result, Exception):
                self._status["agents"][name] = {"status": "error", "duration_s": 0, "note_path": None}
                report[name] = {"status": "error", "duration_s": 0, "note_path": None}
            else:
                self._status["agents"][name] = result
                report[name] = result

        self._status["phase"] = "ready"

        await notif.add(
            title="All agents ready",
            message=f"Graceful shutdown complete — {len(agents)} agent(s) prepared",
            level="info",
            source="system.lifecycle",
        )

        return report

    async def _prepare_agent(self, agent: dict, reason: Reason, data_dir: Path) -> dict:
        name = agent["name"]
        host = agent.get("host", "")
        port = agent.get("port", 8080)
        t0 = time.monotonic()

        note_path = None
        status = "ready"

        if host:
            try:
                async with httpx.AsyncClient(
                    timeout=httpx.Timeout(connect=10, read=300, write=10, pool=10)
                ) as client:
                    resp = await client.post(
                        f"http://{host}:{port}/prepare-for-shutdown",
                        json={"reason": reason, "deadline_s": 300},
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        note_path = data.get("note_path")
                    else:
                        note_path = await self._write_controller_note(agent, reason, data_dir)
            except Exception:
                note_path = await self._write_controller_note(agent, reason, data_dir)
        else:
            note_path = await self._write_controller_note(agent, reason, data_dir)

        duration_s = round(time.monotonic() - t0, 2)

        # Mark agent paused in config
        config = self._app_state.config
        for a in config.agents:
            if a["name"] == name:
                a["paused"] = True
                break
        from tinyagentos.config import save_config_locked
        await save_config_locked(config, config.config_path)

        entry = {"status": status, "duration_s": duration_s, "note_path": note_path}
        self._status["agents"][name] = entry
        return entry

    async def _write_controller_note(self, agent: dict, reason: Reason, data_dir: Path) -> str:
        name = agent["name"]
        note_dir = data_dir / "agent-memory" / name
        note_dir.mkdir(parents=True, exist_ok=True)
        note_path = note_dir / "resume_note.json"
        note = {
            "reason": reason,
            "paused_at": int(time.time()),
            "last_user_msg": None,
            "in_progress_task": None,
            "next_step_hint": "controller-side fallback — agent framework did not implement /prepare-for-shutdown",
            "context_snapshot": {},
        }
        note_path.write_text(json.dumps(note, indent=2))
        return str(note_path)


async def apply_pending_restart_check(app_state) -> None:
    pending = read_pending_restart()
    if pending is None:
        return

    target_sha = pending.get("target_sha", "")
    notif = app_state.notifications

    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "rev-parse", "HEAD",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            cwd=str(Path(__file__).parent.parent),
        )
        stdout, _ = await proc.communicate()
        current_sha = stdout.decode().strip() if stdout else ""
    except Exception:
        current_sha = ""

    short_current = current_sha[:7]
    short_target = target_sha[:7]

    if current_sha and target_sha and current_sha == target_sha:
        await notif.add(
            title=f"Update applied ({short_current})",
            message="Restart completed successfully — running the new version.",
            level="info",
            source="system.lifecycle",
        )
        clear_pending_restart()
    else:
        await notif.add(
            title="Restart happened but code didn't update",
            message=f"Still on {short_current}, expected {short_target}. Check git pull output.",
            level="error",
            source="system.lifecycle",
        )


async def resume_agents_from_notes(app_state) -> None:
    config = app_state.config
    data_dir: Path = app_state.data_dir
    notif = app_state.notifications

    resumed = []
    for agent in config.agents:
        if not agent.get("paused", False):
            continue
        name = agent["name"]
        note_path = data_dir / "agent-memory" / name / "resume_note.json"
        if not note_path.exists():
            continue

        try:
            note = json.loads(note_path.read_text())
        except Exception:
            continue

        host = agent.get("host", "")
        port = agent.get("port", 8080)
        if not host:
            continue

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"http://{host}:{port}/resume",
                    json=note,
                )
                if resp.status_code == 200:
                    agent["paused"] = False
                    note_path.unlink(missing_ok=True)
                    resumed.append(name)
        except Exception:
            pass  # leave paused=True and note in place

    if resumed:
        from tinyagentos.config import save_config_locked
        await save_config_locked(config, config.config_path)
        await notif.add(
            title="All agents resumed",
            message=f"Resumed {len(resumed)} agent(s) from resume notes: {', '.join(resumed)}",
            level="info",
            source="system.lifecycle",
        )

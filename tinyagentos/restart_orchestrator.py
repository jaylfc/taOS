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
        logger.warning("Failed to read pending-restart flag at %s", path, exc_info=True)
        return None


def clear_pending_restart() -> None:
    path = _pending_restart_path()
    try:
        path.unlink(missing_ok=True)
    except Exception:
        logger.warning("Failed to clear pending-restart flag at %s", path, exc_info=True)


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


# The graceful-shutdown pause (prepare()) marks EVERY agent paused=True, so the
# boot-time resume must cover every paused agent or a routine update/restart
# strands agents paused with no indication (#97):
#   - a framework that answered /prepare-for-shutdown itself leaves NO
#     controller-side note, so a note-gated resume skipped exactly the agents
#     that implemented the protocol correctly;
#   - hostless agents have no /resume to call and were never unpaused;
#   - agents whose containers boot slower than the controller failed the single
#     resume attempt and stayed paused silently.

_RESUME_RETRY_INTERVAL_S = 30
_RESUME_RETRY_WINDOW_S = 600


def _load_or_synthesize_note(note_path: Path) -> dict:
    if note_path.exists():
        try:
            return json.loads(note_path.read_text())
        except Exception:
            logger.warning("unreadable resume note at %s; synthesizing", note_path)
    # No controller-side note: the framework handled /prepare-for-shutdown
    # itself and keeps its own state, so a minimal note is enough. Field
    # shapes mirror _write_controller_note (paused_at is an int there) so a
    # framework parsing either source sees a single contract.
    return {
        "reason": "restart",
        "paused_at": int(time.time()),
        "last_user_msg": None,
        "in_progress_task": None,
        "next_step_hint": "controller restarted; resume normal operation",
        "context_snapshot": {},
    }


async def _post_resume(host: str, port: int, note: dict) -> bool:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(f"http://{host}:{port}/resume", json=note)
            return resp.status_code == 200
    except Exception:
        return False


async def _unpause(app_state, agent: dict, note_path: Path | None) -> None:
    agent["paused"] = False
    from tinyagentos.config import save_config_locked

    config = app_state.config
    await save_config_locked(config, config.config_path)
    # Delete the note only AFTER the unpause is persisted: a failed config
    # write must never leave paused=True with the recovery note already gone.
    if note_path is not None:
        note_path.unlink(missing_ok=True)


async def resume_agents_from_notes(app_state) -> None:
    config = app_state.config
    data_dir: Path = app_state.data_dir
    notif = app_state.notifications

    paused = [a for a in config.agents if a.get("paused", False)]
    if not paused:
        return

    resumed: list[str] = []
    pending: list[str] = []
    # (agent, note_path-to-delete) pairs; flags flip and persist in ONE locked
    # config write below instead of one write per agent.
    finalize: list[tuple[dict, Path | None]] = []

    for agent in paused:
        name = agent["name"]
        note_path = data_dir / "agent-memory" / name / "resume_note.json"
        host = agent.get("host", "")
        port = agent.get("port", 8080)

        if not host:
            # Nothing to call: the pause flag is the only thing holding the
            # agent back, so clear it. Keep any note on disk: nothing consumed
            # it, and it may carry state worth inspecting.
            finalize.append((agent, None))
            resumed.append(name)
            continue

        note = _load_or_synthesize_note(note_path)
        if await _post_resume(host, port, note):
            finalize.append((agent, note_path))
            resumed.append(name)
        else:
            pending.append(name)

    if finalize:
        # In-memory flags flip regardless of persistence: the agents were
        # genuinely resumed over /resume, so blocking them in memory because a
        # disk write failed would be worse than the divergence. A failed write
        # is surfaced loudly and the recovery notes are kept.
        for agent, _ in finalize:
            agent["paused"] = False
        from tinyagentos.config import save_config_locked

        try:
            await save_config_locked(config, config.config_path)
        except Exception:
            logger.exception("persisting agent unpauses failed")
            try:
                await notif.add(
                    title="Agent resume not persisted",
                    message=(
                        "Agents were resumed but the config write failed; they may "
                        "show as paused again after the next restart."
                    ),
                    level="warning",
                    source="system.lifecycle",
                )
            except Exception:
                # The log line above already carries the failure; a broken
                # notification store must not abort the rest of the resume.
                logger.exception("could not post the persist-failure warning")
        else:
            # Notes go only after the unpauses are persisted (see _unpause).
            for _, np_ in finalize:
                if np_ is not None:
                    np_.unlink(missing_ok=True)

    if resumed:
        await notif.add(
            title="Agents resumed",
            message=f"Resumed {len(resumed)} agent(s) after restart: {', '.join(resumed)}",
            level="info",
            source="system.lifecycle",
        )

    if pending:
        # Agent containers can boot slower than the controller; keep retrying
        # in the background instead of stranding them paused, and say so loudly
        # if they never come back.
        task = asyncio.create_task(_resume_retry_loop(app_state, pending))
        bg = getattr(app_state, "_background_tasks", None)
        if bg is not None:
            bg.add(task)
            task.add_done_callback(bg.discard)


async def _resume_retry_loop(app_state, names: list[str]) -> None:
    config = app_state.config
    data_dir: Path = app_state.data_dir
    notif = app_state.notifications

    remaining = set(names)
    deadline = time.monotonic() + _RESUME_RETRY_WINDOW_S

    while remaining and time.monotonic() < deadline:
        await asyncio.sleep(_RESUME_RETRY_INTERVAL_S)
        # Rebuilt each tick on purpose: agents can be added or removed while
        # the 10-minute window runs, and a stale index would resume a deleted
        # agent or miss a re-added one.
        by_name = {a["name"]: a for a in config.agents}
        for name in list(remaining):
            # Any raise past this point (config write, notification store)
            # would kill the loop and re-create the silent pause this fix
            # exists to remove; log and keep going instead.
            try:
                agent = by_name.get(name)
                if agent is None or not agent.get("paused", False):
                    remaining.discard(name)
                    continue
                note_path = data_dir / "agent-memory" / name / "resume_note.json"
                note = _load_or_synthesize_note(note_path)
                if await _post_resume(agent.get("host", ""), agent.get("port", 8080), note):
                    # Per-agent config write is fine here: retry successes are
                    # rare, isolated events (one agent per 30s tick at worst),
                    # unlike the boot pass which batches.
                    await _unpause(app_state, agent, note_path)
                    remaining.discard(name)
                    await notif.add(
                        title=f"Agent {name} resumed",
                        message="Resumed after the controller restart (agent took a while to come back up).",
                        level="info",
                        source="system.lifecycle",
                    )
            except Exception:
                logger.exception("resume retry for agent %s failed; will retry", name)

    if remaining:
        # The whole point of this warning is to make the failure visible; a
        # notification-store error must not silently swallow it.
        try:
            await notif.add(
                title="Some agents are still paused",
                message=(
                    f"Could not resume after the restart: {', '.join(sorted(remaining))}. "
                    "They stay paused; check the agent containers, then unpause from the Agents app."
                ),
                level="warning",
                source="system.lifecycle",
            )
        except Exception:
            logger.exception(
                "could not post the still-paused warning; agents still paused: %s",
                ", ".join(sorted(remaining)),
            )

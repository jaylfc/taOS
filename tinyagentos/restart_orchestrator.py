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

_MAX_CONTEXT_SNAPSHOT_BYTES = 32768

# A resume note without these is not worth resuming: the framework cannot tell
# which agent or session it is waking. Preserving them was documented and
# asserted but never implemented - both drop loops ordered purely by value size,
# so the fields survived only while their values happened to be small. A large
# agent_id beside many long-NAME fields sorts FIRST and is dropped first, and
# ordering by serialized entry size instead does not help: the long names move
# the entries, not the ordering. Exclude them by name.
_REQUIRED_SNAPSHOT_FIELDS = ("agent_id", "session_id")
_TRUNCATION_REASON = (
    "snapshot exceeded context window; largest fields dropped first, "
    "agent_id and session_id kept where the cap allows"
)
_NON_OBJECT_TRUNCATION_REASON = (
    "context_snapshot was not an object and exceeded the context window; "
    "the whole value was dropped"
)


def _entry_bytes(key: str, value) -> int:
    """Serialized cost of one `"key":value` pair inside a JSON object."""
    return len(json.dumps(key)) + 1 + len(json.dumps(value, separators=(",", ":")))


def _object_bytes(entry_total: int, count: int) -> int:
    """Serialized size of an object whose entries cost `entry_total` bytes:
    the two braces plus the comma between each adjacent pair.

    Sizes are tracked arithmetically because the cap tests apply once per
    dropped field, and re-serializing a 32 KB snapshot on every iteration made
    the drop loop quadratic in the number of fields.
    """
    return 2 + entry_total + max(count - 1, 0)


# Room held back so the marker always has somewhere to go: dropping fields
# until the payload merely fits leaves the snapshot flush against the cap, and
# the record of what was lost then cannot be written at all. This is the
# CHEAPEST marker - names are reported out of whatever room is left over, never
# by sacrificing another payload field to describe the ones already gone.
_MARKER_RESERVE_BYTES = (
    _entry_bytes("_truncated", {"dropped_fields": [], "reason": _TRUNCATION_REASON}) + 1
)


def _cap_context_snapshot(note: dict) -> None:
    snapshot = note.get("context_snapshot")
    if not isinstance(snapshot, dict):
        _cap_non_object_snapshot(note, snapshot)
        return
    if not snapshot:
        return

    sizes = {key: _entry_bytes(key, value) for key, value in snapshot.items()}
    entry_total = sum(sizes.values())
    total = _object_bytes(entry_total, len(sizes))
    if total <= _MAX_CONTEXT_SNAPSHOT_BYTES:
        return
    logger.warning(
        "resume note context_snapshot capped: %d bytes over limit (%d bytes total)",
        total - _MAX_CONTEXT_SNAPSHOT_BYTES,
        total,
    )
    snapshot = dict(snapshot)
    note["context_snapshot"] = snapshot

    def _drop(key: str) -> None:
        nonlocal entry_total
        snapshot.pop(key)
        entry_total -= sizes.pop(key)
        dropped.append(key)

    def _fits(reserve: int = 0) -> bool:
        return (
            _object_bytes(entry_total, len(sizes))
            <= _MAX_CONTEXT_SNAPSHOT_BYTES - reserve
        )

    # Ordered by VALUE size, which is what "largest fields dropped first"
    # means to whoever reads the marker; the ordering is computed once, and
    # the running total above is what makes each cap test O(1).
    def _value_bytes(key: str) -> int:
        return len(json.dumps(snapshot[key], separators=(",", ":")))

    dropped: list[str] = []
    for key in sorted(
        (k for k in snapshot if k not in _REQUIRED_SNAPSHOT_FIELDS),
        key=_value_bytes,
        reverse=True,
    ):
        if _fits(_MARKER_RESERVE_BYTES):
            break
        _drop(key)

    if not _fits():
        # Only the required fields are left and the snapshot still overflows.
        # The cap is the harder invariant - an over-limit note re-triggers the
        # very overflow the cap exists to prevent - so give them up too,
        # largest first, and record them like any other dropped field.
        for key in sorted(
            (k for k in snapshot if k in _REQUIRED_SNAPSHOT_FIELDS),
            key=_value_bytes,
            reverse=True,
        ):
            if _fits():
                break
            _drop(key)

    # Priced against the room actually left, so adding the marker can never
    # put the snapshot back over the cap and there is no second drop pass to
    # undo it. Only a snapshot with no room at all for a bare marker goes out
    # without one, and that is said out loud rather than silently.
    marker = _build_truncated_marker(
        dropped, entry_total, len(sizes), _MAX_CONTEXT_SNAPSHOT_BYTES
    )
    if marker is not None:
        snapshot["_truncated"] = marker
    else:
        logger.warning(
            "resume note context_snapshot left no room for the truncation "
            "marker; %d dropped field name(s) are not recorded",
            len(dropped),
        )


def _cap_non_object_snapshot(note: dict, snapshot) -> None:
    """Bound a `context_snapshot` that is not an object.

    The agent's own framework writes `resume_note.json` when it answers
    /prepare-for-shutdown, so the snapshot is only an object by convention: a
    transcript dumped as a bare string or a list of turns arrives here too.
    Returning early on those posted them to /resume unchanged, which is the
    exact overflow the cap exists to prevent, reached through the one shape
    the guard never checked. A scalar has no fields to drop, so an oversized
    one is replaced wholesale by the marker - bounded by construction, and
    back in the documented object shape.
    """
    if snapshot is None:
        return
    encoded = json.dumps(snapshot, separators=(",", ":"))
    if len(encoded) <= _MAX_CONTEXT_SNAPSHOT_BYTES:
        return
    logger.warning(
        "resume note context_snapshot is a %s of %d bytes, over the %d-byte "
        "limit; replacing it with the truncation marker",
        type(snapshot).__name__,
        len(encoded),
        _MAX_CONTEXT_SNAPSHOT_BYTES,
    )
    note["context_snapshot"] = {
        "_truncated": {
            "dropped_fields": ["context_snapshot"],
            "reason": _NON_OBJECT_TRUNCATION_REASON,
        }
    }


def _build_truncated_marker(
    dropped: list[str], entry_total: int, count: int, max_bytes: int
) -> dict | None:
    """The largest marker that still fits, or None when even a bare one does not.

    `entry_total`/`count` price the snapshot WITHOUT the marker (see
    `_object_bytes`), so a candidate costs one dump of the marker alone rather
    than a fresh dump of the whole 32 KB snapshot.
    """
    _MAX_DROPPED = 100
    candidates = []
    for n_reported in range(min(len(dropped), _MAX_DROPPED), -1, -1):
        extra = len(dropped) - n_reported
        candidates.append(
            dropped[:n_reported] + ([f"...and {extra} more"] if extra > 0 else [])
        )
    if dropped:
        # Even the "...and N more" placeholder can be more than the room left;
        # record that fields went missing rather than nothing at all.
        candidates.append([])
    for names in candidates:
        marker = {"dropped_fields": names, "reason": _TRUNCATION_REASON}
        if (
            _object_bytes(entry_total + _entry_bytes("_truncated", marker), count + 1)
            <= max_bytes
        ):
            return marker
    return None


def _load_or_synthesize_note(note_path: Path) -> dict:
    if note_path.exists():
        try:
            note = json.loads(note_path.read_text())
            # Valid JSON is not necessarily a valid note (null, [], "x");
            # posting one of those to /resume would fail until the retry
            # window expires, so synthesize instead.
            if isinstance(note, dict):
                return note
            logger.warning("resume note at %s is not an object; synthesizing", note_path)
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
            if resp.status_code != 200:
                logger.warning("resume POST to %s:%s returned HTTP %s", host, port, resp.status_code)
            return resp.status_code == 200
    except Exception as exc:
        # The cause matters for a post-mortem (DNS vs refused vs TLS vs
        # timeout); without this line a failed fleet resume is invisible
        # until the final still-paused warning, which carries no cause.
        logger.warning("resume POST to %s:%s failed: %r", host, port, exc)
        return False


async def _unpause(app_state, agent: dict, note_path: Path | None) -> None:
    # Same contract as the boot-time batch pass: the agent was genuinely
    # resumed over /resume, so the in-memory flag flips even if persistence
    # fails; the failure is surfaced and the recovery note is kept.
    agent["paused"] = False
    from tinyagentos.config import save_config_locked

    config = app_state.config
    try:
        await save_config_locked(config, config.config_path)
    except Exception:
        logger.exception(
            "persisting unpause of agent %s failed; keeping its resume note",
            agent.get("name"),
        )
        return
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
        _cap_context_snapshot(note)
        if await _post_resume(host, port, note):
            finalize.append((agent, note_path))
            resumed.append(name)
        else:
            pending.append(name)

    if pending:
        # Agent containers can boot slower than the controller; keep retrying
        # in the background instead of stranding them paused, and say so loudly
        # if they never come back. Scheduled FIRST: the persistence and
        # notification steps below are best-effort, and a raise there must not
        # cost the pending agents their retry window.
        task = asyncio.create_task(_resume_retry_loop(app_state, pending))
        bg = getattr(app_state, "_background_tasks", None)
        if bg is not None:
            bg.add(task)
            task.add_done_callback(bg.discard)

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
                    try:
                        np_.unlink(missing_ok=True)
                    except OSError:
                        logger.warning("could not delete resume note %s", np_)

    if resumed:
        await notif.add(
            title="Agents resumed",
            message=f"Resumed {len(resumed)} agent(s) after restart: {', '.join(resumed)}",
            level="info",
            source="system.lifecycle",
        )


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
                _cap_context_snapshot(note)
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

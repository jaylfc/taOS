from __future__ import annotations

"""BrowserSessionManager -- live browser sessions backed by neko/CDP containers.

Each session belongs to an owner (user or agent), tracks a URL, container,
and neko/CDP endpoints.  In ``mock=True`` mode all Docker/HTTP calls are
skipped so the manager can be used in unit tests without a container runtime.
"""

import logging
import time
import uuid
from pathlib import Path

import aiosqlite
import httpx

logger = logging.getLogger(__name__)


class BrowserWorkerError(Exception):
    """Raised when a worker browser-container call fails."""

IDLE_TIMEOUT_S = 600

BROWSER_SESSIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS browser_sessions (
    id           TEXT PRIMARY KEY,
    owner_type   TEXT NOT NULL,
    owner_id     TEXT NOT NULL,
    profile_name TEXT NOT NULL,
    url          TEXT,
    node         TEXT,
    status       TEXT NOT NULL DEFAULT 'pending',
    container_id TEXT,
    neko_url     TEXT,
    cdp_url      TEXT,
    is_mobile    INTEGER NOT NULL DEFAULT 0,
    created_at   REAL NOT NULL,
    updated_at   REAL NOT NULL,
    last_active  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bs_owner ON browser_sessions(owner_type, owner_id);
CREATE INDEX IF NOT EXISTS idx_bs_status ON browser_sessions(status);
"""

# Migration: add is_mobile column to existing databases that predate this column.
BROWSER_SESSIONS_MIGRATION = """
ALTER TABLE browser_sessions ADD COLUMN is_mobile INTEGER NOT NULL DEFAULT 0;
"""


def _row_to_session(row: tuple) -> dict:
    return {
        "id": row[0],
        "owner_type": row[1],
        "owner_id": row[2],
        "profile_name": row[3],
        "url": row[4],
        "node": row[5],
        "status": row[6],
        "container_id": row[7],
        "neko_url": row[8],
        "cdp_url": row[9],
        "is_mobile": bool(row[10]),
        "created_at": row[11],
        "updated_at": row[12],
        "last_active": row[13],
    }


class BrowserSessionManager:
    """Manages live browser sessions for users and agents."""

    def __init__(self, db_path: Path, mock: bool = False) -> None:
        self.db_path = Path(db_path)
        self.mock = mock
        self._db: aiosqlite.Connection | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def init(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(str(self.db_path))
        await self._db.executescript(BROWSER_SESSIONS_SCHEMA)
        # Best-effort migration for databases created before is_mobile was added.
        try:
            await self._db.execute(BROWSER_SESSIONS_MIGRATION)
        except Exception:
            pass  # Column already exists
        await self._db.commit()

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _assert_db(self) -> aiosqlite.Connection:
        assert self._db is not None, "BrowserSessionManager not initialised -- call await init() first"
        return self._db

    # ------------------------------------------------------------------
    # Session CRUD
    # ------------------------------------------------------------------

    async def create_session(
        self,
        owner_type: str,
        owner_id: str,
        url: str,
        profile_name: str = "default",
        *,
        mobile: bool = False,
        now: float | None = None,
    ) -> dict:
        db = self._assert_db()
        if now is None:
            now = time.time()
        session_id = uuid.uuid4().hex
        await db.execute(
            """INSERT INTO browser_sessions
               (id, owner_type, owner_id, profile_name, url, node, status,
                container_id, neko_url, cdp_url, is_mobile, created_at, updated_at, last_active)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (session_id, owner_type, owner_id, profile_name, url, None, "pending",
             None, None, None, int(mobile), now, now, now),
        )
        await db.commit()
        return {
            "id": session_id,
            "owner_type": owner_type,
            "owner_id": owner_id,
            "profile_name": profile_name,
            "url": url,
            "node": None,
            "status": "pending",
            "container_id": None,
            "neko_url": None,
            "cdp_url": None,
            "is_mobile": mobile,
            "created_at": now,
            "updated_at": now,
            "last_active": now,
        }

    async def get_session(self, session_id: str) -> dict | None:
        db = self._assert_db()
        cursor = await db.execute(
            """SELECT id, owner_type, owner_id, profile_name, url, node, status,
                      container_id, neko_url, cdp_url, is_mobile, created_at, updated_at, last_active
               FROM browser_sessions WHERE id = ?""",
            (session_id,),
        )
        row = await cursor.fetchone()
        return _row_to_session(row) if row else None

    async def list_sessions(self, owner_type: str, owner_id: str) -> list[dict]:
        db = self._assert_db()
        cursor = await db.execute(
            """SELECT id, owner_type, owner_id, profile_name, url, node, status,
                      container_id, neko_url, cdp_url, is_mobile, created_at, updated_at, last_active
               FROM browser_sessions
               WHERE owner_type = ? AND owner_id = ?
               ORDER BY created_at DESC""",
            (owner_type, owner_id),
        )
        rows = await cursor.fetchall()
        return [_row_to_session(r) for r in rows]

    async def mark_running(
        self,
        session_id: str,
        *,
        node: str,
        container_id: str,
        neko_url: str,
        cdp_url: str,
        now: float | None = None,
    ) -> None:
        db = self._assert_db()
        if now is None:
            now = time.time()
        await db.execute(
            """UPDATE browser_sessions
               SET node=?, container_id=?, neko_url=?, cdp_url=?,
                   status='running', updated_at=?
               WHERE id=?""",
            (node, container_id, neko_url, cdp_url, now, session_id),
        )
        await db.commit()

    async def touch_active(self, session_id: str, *, now: float | None = None) -> None:
        db = self._assert_db()
        if now is None:
            now = time.time()
        await db.execute(
            "UPDATE browser_sessions SET last_active=?, updated_at=? WHERE id=?",
            (now, now, session_id),
        )
        await db.commit()

    async def reap_idle(
        self,
        *,
        now: float | None = None,
        timeout_s: int = IDLE_TIMEOUT_S,
    ) -> list[str]:
        """Mark running sessions idle when last_active is older than timeout_s.

        Returns the list of reaped session ids.  Mock mode: only the DB
        transition (status -> 'idle'), keeping the row/profile.  Real
        container stop is wired in a later task.
        """
        db = self._assert_db()
        if now is None:
            now = time.time()
        cutoff = now - timeout_s
        cursor = await db.execute(
            "SELECT id FROM browser_sessions "
            "WHERE status='running' AND owner_type != 'user' AND last_active < ?",
            (cutoff,),
        )
        rows = await cursor.fetchall()
        ids = [r[0] for r in rows]
        if ids:
            await db.execute(
                f"UPDATE browser_sessions SET status='idle', updated_at=? WHERE id IN ({','.join('?' * len(ids))})",
                (now, *ids),
            )
            await db.commit()
        return ids

    async def mark_migrating(self, session_id: str, *, now: float | None = None) -> None:
        db = self._assert_db()
        if now is None:
            now = time.time()
        await db.execute(
            "UPDATE browser_sessions SET status='migrating', updated_at=? WHERE id=?",
            (now, session_id),
        )
        await db.commit()

    async def mark_error(self, session_id: str, *, now: float | None = None) -> None:
        db = self._assert_db()
        if now is None:
            now = time.time()
        await db.execute(
            "UPDATE browser_sessions SET status='error', updated_at=? WHERE id=?",
            (now, session_id),
        )
        await db.commit()

    async def start_on_worker(
        self,
        session_id: str,
        *,
        node: str,
        worker_url: str,
        profile_volume: str,
        auth_token: str | None = None,
        mobile: bool = False,
    ) -> dict:
        """POST /worker/browser/start on the given worker and update the session.

        On success marks the session running and returns the refreshed session dict.
        On any failure marks the session as 'error' and raises BrowserWorkerError.
        """
        headers = {}
        if auth_token:
            headers["Authorization"] = f"Bearer {auth_token}"
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{worker_url.rstrip('/')}/worker/browser/start",
                    json={"session_id": session_id, "profile_volume": profile_volume,
                          "mobile": mobile},
                    headers=headers,
                )
        except Exception as exc:
            await self.mark_error(session_id)
            raise BrowserWorkerError(f"worker request failed: {exc}") from exc

        if resp.status_code != 200:
            await self.mark_error(session_id)
            raise BrowserWorkerError(
                f"worker returned {resp.status_code}: {resp.text[:200]}"
            )

        data = resp.json()
        await self.mark_running(
            session_id,
            node=node,
            container_id=data["container_id"],
            neko_url=data["neko_url"],
            cdp_url=data["cdp_url"],
        )
        return await self.get_session(session_id)

    async def stop_on_worker(
        self,
        session_id: str,
        *,
        worker_url: str,
        container_id: str,
        http_port: int | None = None,
        auth_token: str | None = None,
        set_status: str | None = "stopped",
    ) -> None:
        """POST /worker/browser/stop on the given worker.  Best-effort — all
        errors are logged as warnings and swallowed.  The profile volume is
        intentionally kept.

        If ``set_status`` is not None the session row's status is updated.
        Pass ``set_status=None`` when the caller (reap loop) has already
        transitioned the status.
        """
        headers = {}
        if auth_token:
            headers["Authorization"] = f"Bearer {auth_token}"
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{worker_url.rstrip('/')}/worker/browser/stop",
                    json={"container_id": container_id, "http_port": http_port},
                    headers=headers,
                )
            if resp.status_code != 200:
                logger.warning(
                    "stop_on_worker for %s returned %s: %s",
                    session_id, resp.status_code, resp.text[:200],
                )
        except Exception as exc:
            logger.warning("stop_on_worker for %s failed: %s", session_id, exc)

        if set_status is not None:
            db = self._assert_db()
            now = time.time()
            await db.execute(
                "UPDATE browser_sessions SET status=?, updated_at=? WHERE id=?",
                (set_status, now, session_id),
            )
            await db.commit()

    async def migrate_session(
        self, session_id: str, *, target: str,
        stop_source, move_volume, start_target, emit,
    ) -> dict | None:
        """Move a session to `target` node: signal → suspend → move profile →
        resume → signal. Effects (stop/move/start/emit) are injected. Returns the
        refreshed running session, or None if the session does not exist.

        emit(kind, payload) is called with kind in {"session_migrating","session_resumed"}
        so agents on the session pause and await reconnection.
        """
        session = await self.get_session(session_id)
        if session is None:
            return None
        source_node = session.get("node") or "host"
        volume = f"taos-browser-{session_id}"
        await emit("session_migrating", {"session_id": session_id, "from": source_node, "target": target})
        await self.mark_migrating(session_id)
        try:
            await stop_source(session)
            await move_volume(volume, source_node, target)
            refreshed = await start_target(session, target)
        except Exception:
            await self.mark_error(session_id)
            await emit("session_resumed", {"session_id": session_id, "node": source_node, "error": True})
            raise
        await emit("session_resumed", {"session_id": session_id, "node": target})
        return refreshed

    async def migrate_agent_browsers(self, rows: list[dict], *, now: float | None = None) -> int:
        """Copy agent_browsers profile rows into browser_sessions as agent sessions.
        Idempotent on (owner_id, profile_name). Returns count inserted."""
        db = self._assert_db()
        if now is None:
            now = time.time()
        existing: set[tuple[str, str]] = set()
        cur = await db.execute(
            "SELECT owner_id, profile_name FROM browser_sessions WHERE owner_type='agent'")
        for owner_id, profile_name in await cur.fetchall():
            existing.add((owner_id, profile_name))
        inserted = 0
        for r in rows:
            key = (r["agent_name"], r.get("profile_name", "default"))
            if key in existing:
                continue
            await db.execute(
                """INSERT INTO browser_sessions
                   (id, owner_type, owner_id, profile_name, url, node, status,
                    container_id, neko_url, cdp_url, created_at, updated_at, last_active)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (uuid.uuid4().hex, "agent", key[0], key[1], None, r.get("node"),
                 "stopped", None, None, None, now, now, now),
            )
            existing.add(key)
            inserted += 1
        await db.commit()
        return inserted

    async def list_visible_sessions(self, owner_id: str, *, owned_agent_ids: set[str]) -> list[dict]:
        """The user's own sessions plus sessions of agents the user owns."""
        db = self._assert_db()
        cursor = await db.execute(
            """SELECT id, owner_type, owner_id, profile_name, url, node, status,
                      container_id, neko_url, cdp_url, is_mobile, created_at, updated_at, last_active
               FROM browser_sessions
               WHERE status != 'stopped'
               ORDER BY created_at DESC""",
        )
        rows = await cursor.fetchall()
        out = []
        for r in rows:
            s = _row_to_session(r)
            if s["owner_type"] == "user" and s["owner_id"] == owner_id:
                out.append(s)
            elif s["owner_type"] == "agent" and s["owner_id"] in owned_agent_ids:
                out.append(s)
        return out

    async def get_or_create_mine(self, owner_id: str, *, url: str = "about:blank",
                                 profile_name: str = "default",
                                 mobile: bool = False) -> dict:
        """Return the user's single live (pending/running/idle) session, creating
        one if none exists. Stopped/error sessions are not reused.

        If a running/pending/idle session exists with a DIFFERENT presentation
        mode (mobile vs desktop), it is re-presented: the container is stopped
        (profile volume kept) and the session row is reset to 'pending' in the
        target mode.  The caller is then responsible for starting the container
        in the new mode (same as when a fresh session is created).

        v1 limitation: simultaneous desktop+mobile viewers can flap. CDP
        live-toggle without a container restart is a future v2.
        """
        db = self._assert_db()
        cursor = await db.execute(
            """SELECT id, owner_type, owner_id, profile_name, url, node, status,
                      container_id, neko_url, cdp_url, is_mobile, created_at, updated_at, last_active
               FROM browser_sessions
               WHERE owner_type='user' AND owner_id=? AND status IN ('pending','running','idle')
               ORDER BY created_at DESC LIMIT 1""",
            (owner_id,),
        )
        row = await cursor.fetchone()
        if row is not None:
            session = _row_to_session(row)
            if bool(session["is_mobile"]) == mobile:
                # Same mode — return as-is; caller starts it if pending/idle.
                return session
            # Different mode — re-present. Capture the old container so the
            # caller can stop it (freeing its port + profile-volume lock)
            # before starting the new one; then reset to pending in the new mode.
            old_container = session.get("container_id")
            old_node = session.get("node")
            old_port = None
            if session.get("neko_url"):
                from urllib.parse import urlparse
                try:
                    old_port = urlparse(session["neko_url"]).port
                except Exception:
                    old_port = None
            now = time.time()
            await db.execute(
                """UPDATE browser_sessions
                   SET status='pending', is_mobile=?, container_id=NULL,
                       neko_url=NULL, cdp_url=NULL, node=NULL, updated_at=?
                   WHERE id=?""",
                (int(mobile), now, session["id"]),
            )
            await db.commit()
            new_session = await self.get_session(session["id"])
            if old_container and new_session is not None:
                new_session["_represent_old"] = {
                    "container_id": old_container,
                    "node": old_node,
                    "http_port": old_port,
                }
            return new_session  # type: ignore[return-value]
        return await self.create_session("user", owner_id, url, profile_name, mobile=mobile)

    async def start_on_host(self, session_id: str, *, profile_volume: str, runner,
                             mobile: bool = False) -> dict:
        """Start a Neko container in-process via BrowserContainerRunner (host-local).

        Mirrors start_on_worker but drives the runner directly. On failure marks
        the session 'error' and raises BrowserWorkerError.
        """
        try:
            data = await runner.start(session_id=session_id, profile_volume=profile_volume,
                                      mobile=mobile)
        except Exception as exc:
            await self.mark_error(session_id)
            raise BrowserWorkerError(f"host browser start failed: {exc}") from exc
        await self.mark_running(
            session_id,
            node="host",
            container_id=data["container_id"],
            neko_url=data["neko_url"],
            cdp_url=data.get("cdp_url"),
        )
        return await self.get_session(session_id)

    async def terminate_session(self, session_id: str) -> bool:
        """Set status='stopped'.  Returns False if the session does not exist."""
        db = self._assert_db()
        session = await self.get_session(session_id)
        if session is None:
            return False
        now = time.time()
        await db.execute(
            "UPDATE browser_sessions SET status='stopped', updated_at=? WHERE id=?",
            (now, session_id),
        )
        await db.commit()
        return True


# ---------------------------------------------------------------------------
# Tier-2 node placement
# ---------------------------------------------------------------------------

# Min specs for a Tier-2 browser node (the 4GB Pi must never qualify).
TIER2_MIN_RAM_MB = 4096
TIER2_MIN_CORES = 4


def _capable_workers(
    cluster,
    min_ram_mb: int,
    min_cores: int,
) -> list:
    """Return online workers advertising the ``browser`` capability that meet
    the given RAM and core floor, sorted GPU-first then by ascending load."""
    candidates = []
    for w in cluster.get_workers():
        if w.status != "online":
            continue
        if "browser" not in (getattr(w, "capabilities", None) or []):
            continue
        hw = w.hardware if isinstance(w.hardware, dict) else {}
        ram = hw.get("ram_mb", 0) if isinstance(hw.get("ram_mb"), int) else 0
        cpu = hw.get("cpu")
        cores = cpu.get("cores", 0) if isinstance(cpu, dict) else 0
        if ram < min_ram_mb or cores < min_cores:
            continue
        gpu = hw.get("gpu")
        has_gpu = False
        if isinstance(gpu, dict):
            has_gpu = bool(gpu.get("cuda")) or (gpu.get("vram_mb") or 0) > 0
        candidates.append((not has_gpu, w.load, w))
    candidates.sort(key=lambda t: (t[0], t[1]))
    return [t[2] for t in candidates]


def pick_browser_node(
    cluster,
    *,
    min_ram_mb: int = TIER2_MIN_RAM_MB,
    min_cores: int = TIER2_MIN_CORES,
) -> str | None:
    """Return the name of an online, browser-capable worker meeting Tier-2
    specs, else None.

    Requires the worker to advertise the ``browser`` capability (only a node
    running the lightweight browser-worker does), then reads
    WorkerInfo.hardware for ram_mb + cpu cores.  Prefers GPU-capable nodes
    (cuda=True or vram_mb > 0), then lowest load.

    The capability requirement is what keeps the controller host (e.g. the
    4 GB Pi) from ever being picked — it never advertises ``browser`` —
    rather than relying on it reporting under the RAM floor.
    """
    workers = _capable_workers(cluster, min_ram_mb, min_cores)
    return workers[0].name if workers else None


def list_browser_nodes(
    cluster,
    *,
    min_ram_mb: int = TIER2_MIN_RAM_MB,
    min_cores: int = TIER2_MIN_CORES,
) -> list[dict]:
    """Return a list of capable browser nodes, GPU-first then by ascending load.

    Each entry has keys: name, gpu (bool), ram_mb (int), cores (int), load (float).
    """
    result = []
    for w in _capable_workers(cluster, min_ram_mb, min_cores):
        hw = w.hardware if isinstance(w.hardware, dict) else {}
        ram = hw.get("ram_mb", 0) if isinstance(hw.get("ram_mb"), int) else 0
        cpu = hw.get("cpu")
        cores = cpu.get("cores", 0) if isinstance(cpu, dict) else 0
        gpu = hw.get("gpu")
        has_gpu = False
        if isinstance(gpu, dict):
            has_gpu = bool(gpu.get("cuda")) or (gpu.get("vram_mb") or 0) > 0
        result.append({
            "name": w.name,
            "gpu": has_gpu,
            "ram_mb": ram,
            "cores": cores,
            "load": w.load,
        })
    return result


# ---------------------------------------------------------------------------
# Host browser-capability check
# ---------------------------------------------------------------------------

# The host runs the browser in-process (not as a Tier-2 worker). It qualifies
# only above a RAM floor; 4GB-class hosts are tier-gated to a cluster device instead.
HOST_MIN_RAM_MB = 6144


def host_is_browser_capable(host_hardware: dict | None) -> bool:
    """True when the controller host can run a local browser container."""
    if not isinstance(host_hardware, dict):
        return False
    ram = host_hardware.get("ram_mb", 0)
    return isinstance(ram, int) and ram >= HOST_MIN_RAM_MB


def resolve_browser_target(
    cluster,
    host_hardware: dict | None,
    *,
    explicit_node: str | None = None,
) -> tuple[str, str | None] | None:
    """Pick where a browser session runs.

    Order: explicit worker (if capable) -> host (if capable) -> best worker.
    Returns ("host", None), ("worker", <name>), or None if nowhere is capable.
    """
    if explicit_node is not None:
        names = {n["name"] for n in list_browser_nodes(cluster)}
        return ("worker", explicit_node) if explicit_node in names else None
    if host_is_browser_capable(host_hardware):
        return ("host", None)
    node = pick_browser_node(cluster)
    return ("worker", node) if node else None


# ---------------------------------------------------------------------------
# App.state wiring helper
# ---------------------------------------------------------------------------

async def wire_browser_runtime(
    app_state,
    hardware_profile,
    agent_browsers,
    browser_sessions: "BrowserSessionManager",
    *,
    host_ip: str,
) -> None:
    """Populate app.state with the two attributes the /api/browser/sessions/mine
    route reads, and fold existing agent_browsers profiles into the unified
    session store.

    Must be called from the lifespan after browser_sessions.init() and the
    signing-key line.  Designed for easy unit testing via a SimpleNamespace
    app_state and stub objects.

    Args:
        app_state:          app.state (or any SimpleNamespace in tests)
        hardware_profile:   the host HardwareProfile dataclass instance
        agent_browsers:     the AgentBrowsersManager already in app.state
        browser_sessions:   the BrowserSessionManager already in app.state
        host_ip:            LAN-reachable IP for NEKO_WEBRTC_NAT1TO1 (NOT 127.0.0.1)
    """
    import dataclasses

    from tinyagentos.worker.browser_container import BrowserContainerRunner

    runner = BrowserContainerRunner(node_ip=host_ip, hw_profile=hardware_profile)
    app_state.browser_container_runner = runner

    hw_dict = dataclasses.asdict(hardware_profile) if hardware_profile is not None else {}
    app_state.host_hardware = hw_dict

    rows = await agent_browsers.list_profiles()
    await browser_sessions.migrate_agent_browsers(rows)

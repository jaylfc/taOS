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
    created_at   REAL NOT NULL,
    updated_at   REAL NOT NULL,
    last_active  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bs_owner ON browser_sessions(owner_type, owner_id);
CREATE INDEX IF NOT EXISTS idx_bs_status ON browser_sessions(status);
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
        "created_at": row[10],
        "updated_at": row[11],
        "last_active": row[12],
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
        now: float | None = None,
    ) -> dict:
        db = self._assert_db()
        if now is None:
            now = time.time()
        session_id = uuid.uuid4().hex
        await db.execute(
            """INSERT INTO browser_sessions
               (id, owner_type, owner_id, profile_name, url, node, status,
                container_id, neko_url, cdp_url, created_at, updated_at, last_active)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (session_id, owner_type, owner_id, profile_name, url, None, "pending",
             None, None, None, now, now, now),
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
            "created_at": now,
            "updated_at": now,
            "last_active": now,
        }

    async def get_session(self, session_id: str) -> dict | None:
        db = self._assert_db()
        cursor = await db.execute(
            """SELECT id, owner_type, owner_id, profile_name, url, node, status,
                      container_id, neko_url, cdp_url, created_at, updated_at, last_active
               FROM browser_sessions WHERE id = ?""",
            (session_id,),
        )
        row = await cursor.fetchone()
        return _row_to_session(row) if row else None

    async def list_sessions(self, owner_type: str, owner_id: str) -> list[dict]:
        db = self._assert_db()
        cursor = await db.execute(
            """SELECT id, owner_type, owner_id, profile_name, url, node, status,
                      container_id, neko_url, cdp_url, created_at, updated_at, last_active
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
            "SELECT id FROM browser_sessions WHERE status='running' AND last_active < ?",
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
                    json={"session_id": session_id, "profile_volume": profile_volume},
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

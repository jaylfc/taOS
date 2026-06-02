from __future__ import annotations

"""BrowserSessionManager -- live browser sessions backed by neko/CDP containers.

Each session belongs to an owner (user or agent), tracks a URL, container,
and neko/CDP endpoints.  In ``mock=True`` mode all Docker/HTTP calls are
skipped so the manager can be used in unit tests without a container runtime.
"""

import time
import uuid
from pathlib import Path

import aiosqlite

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


def pick_browser_node(
    cluster,
    *,
    min_ram_mb: int = TIER2_MIN_RAM_MB,
    min_cores: int = TIER2_MIN_CORES,
) -> str | None:
    """Return the name of an online worker meeting Tier-2 specs, else None.

    Reads WorkerInfo.hardware for ram_mb + cpu cores.  Prefers GPU-capable
    nodes (cuda=True or vram_mb > 0), then lowest load.
    """
    candidates = []
    for w in cluster.get_workers():
        if w.status != "online":
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
        candidates.append((not has_gpu, w.load, w.name))

    if not candidates:
        return None
    candidates.sort()
    return candidates[0][2]

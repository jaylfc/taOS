from __future__ import annotations

"""AgentBrowsersManager -- persistent browser profiles backed by Chromium/Docker.

Each profile maps to a named Docker volume that holds a Chromium user-data
directory.  A profile can be associated with one agent and run on any cluster
node.  In ``mock=True`` mode all Docker/CDP calls are skipped so the manager
can be used in unit tests without a Docker daemon.
"""

import asyncio
import logging
import time
import uuid
from pathlib import Path

import aiosqlite

logger = logging.getLogger(__name__)

# Minimal 1x1 white PNG (67 bytes) returned by mock screenshots.
_MOCK_PNG = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx"
    b"\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00"
    b"\x00\x00\x00IEND\xaeB`\x82"
)

AGENT_BROWSERS_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_browsers (
    id          TEXT PRIMARY KEY,
    agent_name  TEXT,
    profile_name TEXT NOT NULL,
    node        TEXT NOT NULL DEFAULT 'local',
    status      TEXT NOT NULL DEFAULT 'stopped',
    container_id TEXT,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ab_agent ON agent_browsers(agent_name);
CREATE INDEX IF NOT EXISTS idx_ab_status ON agent_browsers(status);
"""

_SCREENSHOT_CACHE_TTL = 30  # seconds


def _row_to_profile(row: tuple) -> dict:
    return {
        "id": row[0],
        "agent_name": row[1],
        "profile_name": row[2],
        "node": row[3],
        "status": row[4],
        "container_id": row[5],
        "created_at": row[6],
        "updated_at": row[7],
    }


class AgentBrowsersManager:
    """Manages persistent browser profiles for agents."""

    def __init__(self, db_path: Path, mock: bool = False) -> None:
        self.db_path = Path(db_path)
        self.mock = mock
        self._db: aiosqlite.Connection | None = None
        # {profile_id: (screenshot_bytes, captured_at)}
        self._screenshot_cache: dict[str, tuple[bytes, float]] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def init(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(str(self.db_path))
        await self._db.executescript(AGENT_BROWSERS_SCHEMA)
        await self._db.commit()

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _assert_db(self) -> aiosqlite.Connection:
        assert self._db is not None, "AgentBrowsersManager not initialised -- call await init() first"
        return self._db

    async def _touch(self, profile_id: str, **fields) -> None:
        """Update arbitrary columns + updated_at for a profile."""
        db = self._assert_db()
        now = time.time()
        set_clauses = [f"{k} = ?" for k in fields]
        set_clauses.append("updated_at = ?")
        params = list(fields.values()) + [now, profile_id]
        await db.execute(
            f"UPDATE agent_browsers SET {', '.join(set_clauses)} WHERE id = ?",
            params,
        )
        await db.commit()

    # ------------------------------------------------------------------
    # Docker helpers (skipped in mock mode)
    # ------------------------------------------------------------------

    async def _docker_start(self, profile_id: str, node: str) -> str | None:
        """Start a Chromium container for the profile and return its container id."""
        if self.mock:
            return f"mock-container-{profile_id[:8]}"
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "run", "-d", "--rm",
                "--name", f"taos-browser-{profile_id[:8]}",
                "-v", f"taos-browser-{profile_id}:/home/chromium",
                "browserless/chromium",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode == 0:
                return stdout.decode().strip()
        except Exception as exc:
            logger.warning("docker run failed for profile %s: %s", profile_id, exc)
        return None

    async def _docker_stop(self, container_id: str) -> None:
        """Stop a running container by id."""
        if self.mock:
            return
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "stop", container_id,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.communicate()
        except Exception as exc:
            logger.warning("docker stop failed for %s: %s", container_id, exc)

    async def _docker_rm_volume(self, profile_id: str) -> None:
        """Delete the Docker volume backing a profile."""
        if self.mock:
            return
        volume_name = f"taos-browser-{profile_id}"
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "volume", "rm", volume_name,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.communicate()
        except Exception as exc:
            logger.warning("docker volume rm failed for %s: %s", volume_name, exc)

    async def _cdp_screenshot(self, container_id: str) -> bytes | None:
        """Take a CDP screenshot via websocket."""
        if self.mock:
            return _MOCK_PNG
        try:
            import aiohttp  # optional dep
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
                async with session.get(
                    "http://localhost:9222/json", timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    tabs = await resp.json()
                    if not tabs:
                        return None
                    ws_url = tabs[0].get("webSocketDebuggerUrl")
                async with session.ws_connect(ws_url, timeout=10) as ws:
                    await ws.send_json({"id": 1, "method": "Page.captureScreenshot"})
                    async for msg in ws:
                        import json, base64
                        data = json.loads(msg.data)
                        if data.get("id") == 1:
                            return base64.b64decode(data["result"]["data"])
        except Exception as exc:
            logger.warning("CDP screenshot failed for %s: %s", container_id, exc)
        return None

    # ------------------------------------------------------------------
    # Profile CRUD
    # ------------------------------------------------------------------

    async def create_profile(
        self,
        profile_name: str,
        agent_name: str | None = None,
        node: str = "local",
    ) -> dict:
        db = self._assert_db()
        now = time.time()
        profile_id = str(uuid.uuid4())
        await db.execute(
            """INSERT INTO agent_browsers
               (id, agent_name, profile_name, node, status, container_id, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (profile_id, agent_name, profile_name, node, "stopped", None, now, now),
        )
        await db.commit()
        return {
            "id": profile_id,
            "agent_name": agent_name,
            "profile_name": profile_name,
            "node": node,
            "status": "stopped",
            "container_id": None,
            "created_at": now,
            "updated_at": now,
        }

    async def get_profile(self, profile_id: str) -> dict | None:
        db = self._assert_db()
        cursor = await db.execute(
            """SELECT id, agent_name, profile_name, node, status, container_id,
                      created_at, updated_at
               FROM agent_browsers WHERE id = ?""",
            (profile_id,),
        )
        row = await cursor.fetchone()
        return _row_to_profile(row) if row else None

    async def list_profiles(self, agent_name: str | None = None) -> list[dict]:
        db = self._assert_db()
        sql = """SELECT id, agent_name, profile_name, node, status, container_id,
                        created_at, updated_at
                 FROM agent_browsers"""
        params: list = []
        if agent_name is not None:
            sql += " WHERE agent_name = ?"
            params.append(agent_name)
        sql += " ORDER BY created_at DESC"
        cursor = await db.execute(sql, params)
        rows = await cursor.fetchall()
        return [_row_to_profile(r) for r in rows]

    async def delete_profile(self, profile_id: str) -> bool:
        """Remove profile from DB.  Stops container if running but does NOT delete volume."""
        db = self._assert_db()
        profile = await self.get_profile(profile_id)
        if profile is None:
            return False
        if profile["status"] == "running" and profile["container_id"]:
            await self._docker_stop(profile["container_id"])
        cursor = await db.execute("DELETE FROM agent_browsers WHERE id = ?", (profile_id,))
        await db.commit()
        return cursor.rowcount > 0

    async def delete_profile_data(self, profile_id: str) -> bool:
        """Delete the Docker volume backing the profile (irreversible)."""
        await self._docker_rm_volume(profile_id)
        return True

    # ------------------------------------------------------------------
    # Browser lifecycle
    # ------------------------------------------------------------------

    async def start_browser(self, profile_id: str) -> bool:
        """Start the browser for a profile.

        If the owning agent already has another running profile, that one is
        stopped first (one active browser per agent).
        """
        profile = await self.get_profile(profile_id)
        if profile is None:
            return False

        agent_name = profile.get("agent_name")
        if agent_name:
            # Stop any other running profiles for this agent
            others = await self.list_profiles(agent_name=agent_name)
            for other in others:
                if other["id"] != profile_id and other["status"] == "running":
                    await self.stop_browser(other["id"])

        container_id = await self._docker_start(profile_id, profile["node"])
        if container_id is None and not self.mock:
            return False

        await self._touch(profile_id, status="running", container_id=container_id)
        return True

    async def stop_browser(self, profile_id: str) -> bool:
        profile = await self.get_profile(profile_id)
        if profile is None:
            return False
        if profile["container_id"]:
            await self._docker_stop(profile["container_id"])
        await self._touch(profile_id, status="stopped", container_id=None)
        return True

    # ------------------------------------------------------------------
    # Assignment & routing
    # ------------------------------------------------------------------

    async def assign_agent(self, profile_id: str, agent_name: str) -> bool:
        profile = await self.get_profile(profile_id)
        if profile is None:
            return False
        await self._touch(profile_id, agent_name=agent_name)
        return True

    async def move_to_node(self, profile_id: str, node: str) -> bool:
        """Move a profile to another node.  Stops the container if running."""
        profile = await self.get_profile(profile_id)
        if profile is None:
            return False
        if profile["status"] == "running" and profile["container_id"]:
            await self._docker_stop(profile["container_id"])
            await self._touch(profile_id, status="stopped", container_id=None, node=node)
        else:
            await self._touch(profile_id, node=node)
        return True

    # ------------------------------------------------------------------
    # Browser inspection
    # ------------------------------------------------------------------

    async def get_screenshot(self, profile_id: str) -> bytes | None:
        """Return a PNG screenshot.  Cached for _SCREENSHOT_CACHE_TTL seconds."""
        if self.mock:
            return _MOCK_PNG

        profile = await self.get_profile(profile_id)
        if profile is None or profile["status"] != "running":
            return None

        now = time.time()
        cached = self._screenshot_cache.get(profile_id)
        if cached and (now - cached[1]) < _SCREENSHOT_CACHE_TTL:
            return cached[0]

        screenshot = await self._cdp_screenshot(profile["container_id"])
        if screenshot:
            self._screenshot_cache[profile_id] = (screenshot, now)
        return screenshot

    async def get_cookies(self, profile_id: str, domain: str) -> list[dict]:
        """Read cookies for a domain from the Chromium SQLite cookie store."""
        if self.mock:
            return []
        # Real implementation would read from
        # <volume>/Default/Cookies (SQLite) filtered by host_key LIKE domain.
        return []

    async def get_login_status(self, profile_id: str) -> dict[str, bool]:
        """Return a per-site auth status by checking for known auth cookies."""
        _sites = ["x", "github", "youtube", "reddit"]
        if self.mock:
            return {site: False for site in _sites}

        profile = await self.get_profile(profile_id)
        if profile is None:
            return {site: False for site in _sites}

        domain_map = {
            "x": "twitter.com",
            "github": "github.com",
            "youtube": "youtube.com",
            "reddit": "reddit.com",
        }
        result: dict[str, bool] = {}
        for site, domain in domain_map.items():
            cookies = await self.get_cookies(profile_id, domain)
            result[site] = bool(cookies)
        return result

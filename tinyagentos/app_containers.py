from __future__ import annotations

import json
import secrets
import time

from tinyagentos.base_store import BaseStore

_ALPHABET = "abcdefghijklmnopqrstuvwxyz234567"

RUNTIMES = ("lxc", "docker")
SHARING = ("shareable", "own", "suite-only")
CREATED_BY = ("install", "suite", "user")

APP_CONTAINERS_SCHEMA = """
CREATE TABLE IF NOT EXISTS app_containers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    runtime TEXT NOT NULL,
    node TEXT NOT NULL DEFAULT 'local',
    backend_ref TEXT NOT NULL,
    base_image TEXT NOT NULL DEFAULT '',
    limits_json TEXT NOT NULL DEFAULT '{}',
    sharing TEXT NOT NULL DEFAULT 'shareable',
    ports_json TEXT NOT NULL DEFAULT '{}',
    origin_port INTEGER NOT NULL DEFAULT 0,
    moving INTEGER NOT NULL DEFAULT 0,
    created_by TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
"""


def _new_id() -> str:
    suffix = "".join(secrets.choice(_ALPHABET) for _ in range(6))
    return f"ac-{suffix}"


class AppContainersStore(BaseStore):
    SCHEMA = APP_CONTAINERS_SCHEMA

    async def create(
        self,
        *,
        name: str,
        runtime: str,
        node: str = "local",
        base_image: str = "",
        limits: dict | None = None,
        sharing: str = "shareable",
        created_by: str = "install",
        backend_ref: str | None = None,
    ) -> dict:
        if runtime not in RUNTIMES:
            raise ValueError("invalid runtime")
        if sharing not in SHARING:
            raise ValueError("invalid sharing")
        if created_by not in CREATED_BY:
            raise ValueError("invalid created_by")
        if not name:
            raise ValueError("name is empty")

        for _ in range(8):
            cid = _new_id()
            async with self._db.execute(
                "SELECT 1 FROM app_containers WHERE id = ?", (cid,)
            ) as cur:
                if await cur.fetchone() is None:
                    break
        else:
            raise RuntimeError("could not allocate app container id")

        if backend_ref is None:
            backend_ref = f"taos-{cid}"
        now = int(time.time())
        limits_json = json.dumps(limits or {})

        await self._db.execute(
            """INSERT INTO app_containers
               (id, name, runtime, node, backend_ref, base_image, limits_json, sharing, ports_json, origin_port, moving, created_by, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, '{}', 0, 0, ?, ?, ?)""",
            (
                cid,
                name,
                runtime,
                node,
                backend_ref,
                base_image,
                limits_json,
                sharing,
                created_by,
                now,
                now,
            ),
        )
        await self._db.commit()
        result = await self.get(cid)
        if result is None:
            raise RuntimeError("failed to create app container")
        return result

    async def get(self, container_id: str) -> dict | None:
        async with self._db.execute(
            """SELECT id, name, runtime, node, backend_ref, base_image, limits_json, sharing, ports_json, origin_port, moving, created_by, created_at, updated_at
               FROM app_containers WHERE id = ?""",
            (container_id,),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        try:
            limits = json.loads(row[6]) if row[6] else {}
        except Exception:
            limits = {}
        try:
            ports = json.loads(row[8]) if row[8] else {}
        except Exception:
            ports = {}
        # ensure values are ints for ports values, keys remain strings
        ports_out = {}
        if isinstance(ports, dict):
            for k, v in ports.items():
                try:
                    ports_out[str(k)] = int(v)
                except Exception:
                    pass
        return {
            "id": row[0],
            "name": row[1],
            "runtime": row[2],
            "node": row[3],
            "backend_ref": row[4],
            "base_image": row[5],
            "limits": limits if isinstance(limits, dict) else {},
            "sharing": row[7],
            "ports": ports_out,
            "origin_port": int(row[9]),
            "moving": bool(row[10]),
            "created_by": row[11],
            "created_at": int(row[12]),
            "updated_at": int(row[13]),
        }

    async def list(self, node: str | None = None, runtime: str | None = None) -> list[dict]:
        sql = """SELECT id, name, runtime, node, backend_ref, base_image, limits_json, sharing, ports_json, origin_port, moving, created_by, created_at, updated_at
                 FROM app_containers"""
        params: list = []
        clauses: list[str] = []
        if node is not None:
            clauses.append("node = ?")
            params.append(node)
        if runtime is not None:
            clauses.append("runtime = ?")
            params.append(runtime)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at ASC, id ASC"
        async with self._db.execute(sql, params) as cur:
            rows = await cur.fetchall()
        result = []
        for row in rows:
            try:
                limits = json.loads(row[6]) if row[6] else {}
            except Exception:
                limits = {}
            try:
                ports = json.loads(row[8]) if row[8] else {}
            except Exception:
                ports = {}
            ports_out = {}
            if isinstance(ports, dict):
                for k, v in ports.items():
                    try:
                        ports_out[str(k)] = int(v)
                    except Exception:
                        pass
            result.append(
                {
                    "id": row[0],
                    "name": row[1],
                    "runtime": row[2],
                    "node": row[3],
                    "backend_ref": row[4],
                    "base_image": row[5],
                    "limits": limits if isinstance(limits, dict) else {},
                    "sharing": row[7],
                    "ports": ports_out,
                    "origin_port": int(row[9]),
                    "moving": bool(row[10]),
                    "created_by": row[11],
                    "created_at": int(row[12]),
                    "updated_at": int(row[13]),
                }
            )
        return result

    async def set_node(self, container_id: str, node: str) -> dict | None:
        now = int(time.time())
        await self._db.execute(
            "UPDATE app_containers SET node = ?, updated_at = ? WHERE id = ?",
            (node, now, container_id),
        )
        await self._db.commit()
        return await self.get(container_id)

    async def set_port(self, container_id: str, internal_port: int, host_port: int) -> dict | None:
        existing = await self.get(container_id)
        if existing is None:
            return None
        ports = dict(existing.get("ports") or {})
        ports[str(internal_port)] = int(host_port)
        now = int(time.time())
        await self._db.execute(
            "UPDATE app_containers SET ports_json = ?, updated_at = ? WHERE id = ?",
            (json.dumps(ports), now, container_id),
        )
        await self._db.commit()
        return await self.get(container_id)

    async def set_origin_port(self, container_id: str, port: int) -> dict | None:
        now = int(time.time())
        await self._db.execute(
            "UPDATE app_containers SET origin_port = ?, updated_at = ? WHERE id = ?",
            (int(port), now, container_id),
        )
        await self._db.commit()
        return await self.get(container_id)

    async def try_lease(self, container_id: str) -> bool:
        now = int(time.time())
        cur = await self._db.execute(
            "UPDATE app_containers SET moving = 1, updated_at = ? WHERE id = ? AND moving = 0",
            (now, container_id),
        )
        await self._db.commit()
        return cur.rowcount == 1

    async def release_lease(self, container_id: str) -> None:
        now = int(time.time())
        await self._db.execute(
            "UPDATE app_containers SET moving = 0, updated_at = ? WHERE id = ?",
            (now, container_id),
        )
        await self._db.commit()

    async def delete(self, container_id: str) -> bool:
        row = await self.get(container_id)
        if row is None:
            return False
        await self._db.execute("DELETE FROM app_containers WHERE id = ?", (container_id,))
        await self._db.commit()
        return True

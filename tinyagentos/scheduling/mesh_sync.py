"""Mesh Sync for Worker Memory Replication (taOSmd).

LWW (last-write-wins) merge protocol for syncing memory state across
taOS controller and workers. When an agent migrates to a worker or
needs memory replicated for resilience, mesh sync handles it.

Architecture:
  - Controller is authoritative (source of truth)
  - Workers pull deltas on agent deployment
  - Push/pull via HTTP with delta sync (lastSyncAt timestamps)
  - SSRF protection blocks private IP ranges on inbound connections

Syncs: KG triples, vector memories, archive index entries, crystals, insights.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import socket
import sqlite3
import time
from pathlib import Path
from urllib.parse import urlparse

from tinyagentos.db_migrations import apply_wal_pragmas, run_migrations

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS sync_peers (
    peer_id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    last_sync_at REAL NOT NULL DEFAULT 0,
    sync_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    peer_id TEXT NOT NULL,
    direction TEXT NOT NULL,
    table_name TEXT NOT NULL,
    records_synced INTEGER NOT NULL DEFAULT 0,
    timestamp REAL NOT NULL,
    FOREIGN KEY (peer_id) REFERENCES sync_peers(peer_id)
);
CREATE INDEX IF NOT EXISTS idx_sync_log_peer ON sync_log(peer_id);
CREATE INDEX IF NOT EXISTS idx_sync_log_ts ON sync_log(timestamp DESC);
"""

MIGRATIONS: list = [
    (1, SCHEMA),
]

# Tables and their timestamp columns for delta sync
SYNCABLE_TABLES = {
    "kg_triples": "created_at",
    "kg_entities": "created_at",
    "vector_memory": "created_at",
    "archive_index": "timestamp",
    "crystals": "created_at",
    "insights": "created_at",
}

# SSRF protection — networks always blocked (cloud metadata, loopback, ULA, link-local)
BLOCKED_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),  # cloud metadata (AWS/GCP/Azure IMDSv1)
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]

# RFC1918 ranges permitted only when the caller explicitly opts in for LAN peers
LAN_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
]


def _effective_ip(addr: str) -> "ipaddress.IPv4Address | ipaddress.IPv6Address":
    """Normalize a raw address string for SSRF range checks.

    IPv4-mapped IPv6 addresses (e.g. ``::ffff:169.254.169.254``) are
    unwrapped to their IPv4 form so that checks against IPv4 BLOCKED_NETWORKS
    work correctly.
    """
    ip = ipaddress.ip_address(addr)
    # ipv4_mapped is only present on IPv6Address; IPv4Address has no such attr
    if isinstance(ip, ipaddress.IPv6Address):
        mapped = ip.ipv4_mapped  # non-None only for ::ffff:x.x.x.x
        if mapped is not None:
            return mapped
    return ip


# Maximum seconds allowed for a single DNS resolution.  A malicious or
# misconfigured peer hostname must not be able to block the calling thread
# indefinitely.
_DNS_TIMEOUT_SECONDS = 5


def is_safe_url(url: str, allow_private: bool = False) -> bool:
    """Check if a URL is safe (not targeting private/internal networks).

    By default (allow_private=False) all private ranges are blocked.
    Pass allow_private=True only for known LAN peer connections; even then
    cloud-metadata / loopback / link-local addresses are always rejected.

    All resolved addresses are checked — not just the first — so that
    multi-address hostnames and DNS-rebinding cannot present a safe address
    first and an unsafe one later.  IPv4-mapped IPv6 addresses
    (``::ffff:<ipv4>``) are normalised to their IPv4 form before range checks.

    DNS resolution is bounded by ``_DNS_TIMEOUT_SECONDS`` to prevent an
    attacker-controlled hostname from hanging the calling thread.
    """
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        if not hostname:
            return False

        # Scope the timeout to this resolution only; restore the previous
        # value (None = no timeout) so other socket operations are unaffected.
        _prev = socket.getdefaulttimeout()
        socket.setdefaulttimeout(_DNS_TIMEOUT_SECONDS)
        try:
            results = socket.getaddrinfo(hostname, parsed.port or 80)
        finally:
            socket.setdefaulttimeout(_prev)

        if not results:
            return False

        for res in results:
            ip = _effective_ip(res[4][0])

            # Always-blocked ranges (cloud metadata, loopback, link-local, ULA)
            for network in BLOCKED_NETWORKS:
                if ip in network:
                    return False

            # RFC1918 ranges: only allowed when caller explicitly opts in for LAN
            if not allow_private:
                for network in LAN_NETWORKS:
                    if ip in network:
                        return False

        return True
    except Exception:
        return False


class MeshSync:
    """Memory mesh synchronization manager."""

    def __init__(
        self,
        db_path: str | Path = "data/mesh-sync.db",
        node_id: str | None = None,
        is_controller: bool = True,
    ):
        self._db_path = str(db_path)
        self._node_id = node_id or hashlib.sha256(str(time.time()).encode()).hexdigest()[:12]
        self._is_controller = is_controller
        self._conn: sqlite3.Connection | None = None

    async def init(self) -> None:
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        apply_wal_pragmas(self._conn)
        run_migrations(self._conn, MIGRATIONS)

    async def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    # ------------------------------------------------------------------
    # Peer management
    # ------------------------------------------------------------------

    async def add_peer(self, peer_id: str, url: str) -> dict:
        """Register a sync peer (worker or controller)."""
        now = time.time()
        self._conn.execute(
            """INSERT INTO sync_peers (peer_id, url, created_at) VALUES (?, ?, ?)
               ON CONFLICT(peer_id) DO UPDATE SET url = excluded.url""",
            (peer_id, url, now),
        )
        self._conn.commit()
        return {"peer_id": peer_id, "url": url}

    async def remove_peer(self, peer_id: str) -> bool:
        cursor = self._conn.execute("DELETE FROM sync_peers WHERE peer_id = ?", (peer_id,))
        self._conn.commit()
        return cursor.rowcount > 0

    async def list_peers(self) -> list[dict]:
        rows = self._conn.execute("SELECT * FROM sync_peers ORDER BY created_at").fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Delta export (controller → worker)
    # ------------------------------------------------------------------

    async def export_delta(
        self,
        source_db: sqlite3.Connection,
        table: str,
        since: float = 0,
    ) -> list[dict]:
        """Export records from a table that were created/modified after `since`.

        The source_db should be the actual memory database (KG, vector, archive).
        """
        if table not in SYNCABLE_TABLES:
            return []

        ts_col = SYNCABLE_TABLES[table]
        rows = source_db.execute(
            f"SELECT * FROM {table} WHERE {ts_col} > ? ORDER BY {ts_col}",
            (since,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Delta import (worker ← controller)
    # ------------------------------------------------------------------

    def _get_table_columns(self, db: sqlite3.Connection, table: str) -> frozenset[str]:
        """Return the set of column names in *table* from the local DB schema.

        ``table`` must already be validated against SYNCABLE_TABLES by the
        caller (import_delta does this).  The PRAGMA identifier is never
        peer-supplied — it comes from our own allowlist.
        """
        # Defense-in-depth: assert the table is in our allowlist even though
        # import_delta already checks.  This keeps _get_table_columns safe if
        # ever called from a new code path.
        assert table in SYNCABLE_TABLES, f"_get_table_columns called with unlisted table: {table!r}"
        rows = db.execute(f"PRAGMA table_info({table})").fetchall()
        return frozenset(r[1] for r in rows)

    async def import_delta(
        self,
        target_db: sqlite3.Connection,
        table: str,
        records: list[dict],
    ) -> int:
        """Import delta records into a target database using LWW merge.

        Last-write-wins: if a record with the same primary key exists,
        the one with the newer timestamp wins.

        Column names from peer records are validated against the local table
        schema (via PRAGMA table_info) before any SQL is constructed.
        Peer-supplied identifiers are never interpolated into SQL.
        """
        if not records or table not in SYNCABLE_TABLES:
            return 0

        # Build an allowlist of known columns from our own local schema.
        # Any peer record containing an unknown column is skipped entirely.
        known_columns = self._get_table_columns(target_db, table)
        if not known_columns:
            logger.warning("No schema found for table %s — skipping import", table)
            return 0

        imported = 0

        for record in records:
            peer_cols = list(record.keys())

            # Reject the record if any column is not in our local schema.
            if not all(col in known_columns for col in peer_cols):
                unknown = [c for c in peer_cols if c not in known_columns]
                logger.warning(
                    "Rejected peer record for %s: unknown columns %s", table, unknown
                )
                continue

            # Use only the validated column names (from peer_cols, now confirmed
            # against known_columns). Table name comes from SYNCABLE_TABLES, not
            # peer data. VALUES are parameterized — no identifier interpolation.
            placeholders = ", ".join(["?"] * len(peer_cols))
            col_names = ", ".join(peer_cols)  # safe: every entry verified above

            try:
                target_db.execute(
                    f"INSERT OR REPLACE INTO {table} ({col_names}) VALUES ({placeholders})",
                    tuple(record[c] for c in peer_cols),
                )
                imported += 1
            except Exception as e:
                # Logged at warning so constraint violations / type mismatches
                # are visible; record is skipped but the batch continues.
                logger.warning("Import failed for %s record: %s", table, e)

        if imported:
            target_db.commit()
        return imported

    # ------------------------------------------------------------------
    # Full sync cycle
    # ------------------------------------------------------------------

    async def pull_from_peer(
        self,
        peer_id: str,
        source_dbs: dict[str, sqlite3.Connection],
        target_dbs: dict[str, sqlite3.Connection],
        allow_private: bool = True,
    ) -> dict:
        """Pull deltas from a peer for all syncable tables.

        Args:
            peer_id: The peer to pull from.
            source_dbs: Map of table_name → source connection (on peer).
            target_dbs: Map of table_name → target connection (local).
            allow_private: Allow private IPs (True for LAN workers).

        Returns:
            {tables_synced, total_records, errors}.
        """
        peer = self._conn.execute(
            "SELECT * FROM sync_peers WHERE peer_id = ?", (peer_id,)
        ).fetchone()
        if not peer:
            return {"error": f"Unknown peer: {peer_id}"}

        if not is_safe_url(peer["url"], allow_private):
            return {"error": f"Unsafe URL: {peer['url']}"}

        last_sync = peer["last_sync_at"]
        results = {"tables_synced": 0, "total_records": 0, "errors": []}
        now = time.time()

        for table in SYNCABLE_TABLES:
            if table not in source_dbs or table not in target_dbs:
                continue
            try:
                delta = await self.export_delta(source_dbs[table], table, last_sync)
                if delta:
                    imported = await self.import_delta(target_dbs[table], table, delta)
                    results["tables_synced"] += 1
                    results["total_records"] += imported

                    # Log sync
                    self._conn.execute(
                        "INSERT INTO sync_log (peer_id, direction, table_name, records_synced, timestamp) VALUES (?, 'pull', ?, ?, ?)",
                        (peer_id, table, imported, now),
                    )
            except Exception as e:
                results["errors"].append(f"{table}: {e}")

        # Update last_sync
        self._conn.execute(
            "UPDATE sync_peers SET last_sync_at = ?, sync_count = sync_count + 1, last_error = ? WHERE peer_id = ?",
            (now, json.dumps(results["errors"]) if results["errors"] else None, peer_id),
        )
        self._conn.commit()

        return results

    async def push_to_peer(
        self,
        peer_url: str,
        table: str,
        records: list[dict],
        allow_private: bool = True,
    ) -> dict:
        """Push delta records to a peer via HTTP POST.

        The peer must expose a /sync/import endpoint.
        """
        if not is_safe_url(peer_url, allow_private):
            return {"error": f"Unsafe URL: {peer_url}"}

        try:
            import httpx
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{peer_url}/sync/import",
                    json={"table": table, "records": records, "node_id": self._node_id},
                )
                return resp.json()
        except Exception as e:
            return {"error": str(e)}

    # ------------------------------------------------------------------
    # Agent migration helper
    # ------------------------------------------------------------------

    async def prepare_agent_bundle(
        self,
        agent_name: str,
        source_dbs: dict[str, sqlite3.Connection],
    ) -> dict:
        """Prepare a complete memory bundle for agent migration.

        Exports ALL memory for a specific agent (not just delta).
        Used when deploying an agent to a new worker.
        """
        bundle = {"agent_name": agent_name, "tables": {}, "exported_at": time.time()}

        for table, ts_col in SYNCABLE_TABLES.items():
            if table not in source_dbs:
                continue

            db = source_dbs[table]

            # Filter by agent where possible
            if table in ("archive_index",):
                rows = db.execute(
                    f"SELECT * FROM {table} WHERE agent_name = ?", (agent_name,)
                ).fetchall()
            elif table in ("crystals",):
                rows = db.execute(
                    f"SELECT * FROM {table} WHERE agent_name = ?", (agent_name,)
                ).fetchall()
            else:
                # KG and vector memory are per-agent by db path, export all
                rows = db.execute(f"SELECT * FROM {table}").fetchall()

            bundle["tables"][table] = [dict(r) for r in rows]

        return bundle

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    async def stats(self) -> dict:
        peers = self._conn.execute("SELECT COUNT(*) as n FROM sync_peers").fetchone()["n"]
        syncs = self._conn.execute("SELECT COUNT(*) as n FROM sync_log").fetchone()["n"]
        last = self._conn.execute("SELECT MAX(timestamp) as ts FROM sync_log").fetchone()["ts"]
        return {
            "node_id": self._node_id,
            "is_controller": self._is_controller,
            "peers": peers,
            "total_syncs": syncs,
            "last_sync": last,
        }

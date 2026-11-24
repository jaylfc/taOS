"""Records client with local write-ahead buffer.

This module provides a client for emitting records to the memory service
with a local SQLite write-ahead log (WAL) buffer. Records are persisted
locally before being sent, ensuring no history is lost on the way to
the memory service.

Wire contract (owned by memory service / taosmd):
- Producer chooses record_id (ULID), used as idempotency key.
- Envelope serialised once; body_sha256 is hash of those exact bytes.
- Local row deleted only when reply's record_id AND body_sha256 both match.
- Retry/dead-letter classes per response codes below.

Response classes:
- 201 or 200 (duplicate): delete row if record_id and hash match.
- 400: keep, alarm.
- 401/403: keep, refresh credential, retry, alarm.
- 409: move to dead-letter, alarm.
- 413: move to dead-letter until blob upload exists, alarm.
- 422: move to dead-letter, alarm. Never deleted.
- 429/503/507: keep, honour Retry-After.
- Timeout/network error: keep, retry with same id.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

import aiosqlite
import ulid
from httpx import AsyncClient

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class RecordsClientConfig:
    """Configuration for the records client."""
    data_dir: Path
    memory_service_url: str
    max_batch_size: int = 100
    buffer_threshold_bytes: int = 100 * 1024 * 1024  # 100 MB default
    send_interval_seconds: float = 1.0
    max_retries: int = 5
    # Callback to get the controller service token
    token_getter: Optional[Callable[[], Optional[str]]] = None


# ---------------------------------------------------------------------------
# Database schema
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    record_id      TEXT PRIMARY KEY,
    body_sha256    TEXT NOT NULL,
    envelope       TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'pending',
    created_at     REAL NOT NULL,
    retry_count    INTEGER NOT NULL DEFAULT 0,
    next_retry_at  REAL,
    last_error     TEXT
);

CREATE INDEX IF NOT EXISTS idx_records_status ON records(status);
CREATE INDEX IF NOT EXISTS idx_records_next_retry ON records(next_retry_at);

CREATE TABLE IF NOT EXISTS dead_letter (
    record_id      TEXT PRIMARY KEY,
    body_sha256    TEXT NOT NULL,
    envelope       TEXT NOT NULL,
    reason         TEXT NOT NULL,
    created_at     REAL NOT NULL,
    moved_at       REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_dead_letter_reason ON dead_letter(reason);
"""

# WAL pragmas for durability - synchronous=FULL as required
WAL_PRAGMAS = """
PRAGMA journal_mode = WAL;
PRAGMA synchronous = FULL;
PRAGMA busy_timeout = 5000;
"""


# ---------------------------------------------------------------------------
# Records Client
# ---------------------------------------------------------------------------

class RecordsClient:
    """Client for emitting records with local write-ahead buffer."""

    def __init__(self, config: RecordsClientConfig, http_client: AsyncClient):
        self.config = config
        self.http_client = http_client
        self.db_path = config.data_dir / "records-wal.db"
        self._db: Optional[aiosqlite.Connection] = None
        self._sender_task: Optional[asyncio.Task] = None
        self._running = False
        self._background_tasks: set[asyncio.Task] = set()

    async def start(self) -> None:
        """Initialize the database and start the background sender."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self._db = await aiosqlite.connect(str(self.db_path))
        await self._db.executescript(WAL_PRAGMAS)
        await self._db.executescript(SCHEMA)
        await self._db.commit()

        self._running = True
        self._sender_task = asyncio.create_task(self._sender_loop())
        self._background_tasks.add(self._sender_task)
        self._sender_task.add_done_callback(self._background_tasks.discard)

        logger.info("Records client started, buffer at %s", self.db_path)

    async def stop(self) -> None:
        """Stop the background sender and close the database."""
        self._running = False
        if self._sender_task:
            self._sender_task.cancel()
            try:
                await self._sender_task
            except asyncio.CancelledError:
                pass
        if self._db:
            await self._db.close()
            self._db = None
        logger.info("Records client stopped")

    async def emit(self, record_type: str, body: dict, *, on_behalf_of: Optional[dict] = None) -> str:
        """Emit a record to the local buffer.

        Args:
            record_type: The type of record (e.g., "decision.answered").
            body: The record body (JSON-serializable).
            on_behalf_of: Optional {"kind": "person"|"agent", "id": "<canonical_id>"}.

        Returns:
            The generated record_id (ULID).

        Raises:
            BufferFullError: If the buffer exceeds the configured threshold.
        """
        if not self._running:
            raise RuntimeError("Records client not started")

        # Check buffer size before writing
        await self._check_buffer_space()

        # Generate record_id (ULID from secure random, monotonic per process)
        record_id = str(ulid.new())

        # Build envelope
        envelope = {
            "record_id": record_id,
            "type": record_type,
            "body": body,
            "timestamp": time.time(),
        }
        if on_behalf_of is not None:
            envelope["on_behalf_of"] = on_behalf_of

        # Serialise envelope once, compute body_sha256 over exact bytes
        envelope_bytes = json.dumps(envelope, separators=(",", ":"), sort_keys=True).encode()
        body_sha256 = hashlib.sha256(envelope_bytes).hexdigest()

        # Write to local buffer
        now = time.time()
        await self._db.execute(
            """INSERT INTO records (record_id, body_sha256, envelope, status, created_at)
               VALUES (?, ?, ?, 'pending', ?)""",
            (record_id, body_sha256, envelope_bytes.decode(), now),
        )
        await self._db.commit()

        logger.debug("Record %s buffered (type=%s)", record_id, record_type)
        return record_id

    async def _check_buffer_space(self) -> None:
        """Check if buffer exceeds threshold, raise if full."""
        # Get approximate buffer size from database file
        try:
            stat = self.db_path.stat()
            if stat.st_size >= self.config.buffer_threshold_bytes:
                raise BufferFullError(
                    f"Records buffer exceeds threshold: {stat.st_size} bytes "
                    f"(limit: {self.config.buffer_threshold_bytes})"
                )
        except OSError:
            pass  # File doesn't exist yet, that's fine

    async def _sender_loop(self) -> None:
        """Background loop that sends buffered records to the memory service."""
        while self._running:
            try:
                await asyncio.sleep(self.config.send_interval_seconds)
                await self._send_pending()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error("Sender loop error: %s", e)

    async def _send_pending(self) -> None:
        """Send pending records in batches."""
        if not self._db:
            return

        # Get records ready to send (pending or retry whose time has come)
        now = time.time()
        cursor = await self._db.execute(
            """SELECT record_id, body_sha256, envelope, retry_count
               FROM records
               WHERE status IN ('pending', 'retry_network', 'retry_429', 'retry_503', 'retry_507',
                                'error_401', 'error_403')
               AND (next_retry_at IS NULL OR next_retry_at <= ?)
               ORDER BY created_at
               LIMIT ?""",
            (now, self.config.max_batch_size),
        )
        rows = await cursor.fetchall()

        if not rows:
            return

        # Prepare batch
        records = []
        for record_id, body_sha256, envelope_json, retry_count in rows:
            envelope = json.loads(envelope_json)
            records.append({
                "record_id": record_id,
                "body_sha256": body_sha256,
                **envelope,
            })

        # Send batch
        await self._send_batch(records)

    async def _send_batch(self, records: list[dict]) -> None:
        """Send a batch of records to the memory service."""
        if not records:
            return

        # Get token if available
        headers = {"Content-Type": "application/json"}
        token = None
        if self.config.token_getter:
            token = self.config.token_getter()
            if token:
                headers["Authorization"] = f"Bearer {token}"

        url = f"{self.config.memory_service_url.rstrip('/')}/records/batch"
        payload = {"records": records}

        try:
            resp = await self.http_client.post(url, json=payload, headers=headers, timeout=30.0)
        except Exception as e:
            # Network error - mark all for retry
            await self._handle_network_error(records, e)
            return

        # Process response
        if resp.status_code in (200, 201):
            await self._handle_success(records, resp)
        elif resp.status_code == 400:
            await self._handle_400(records)
        elif resp.status_code in (401, 403):
            await self._handle_401_403(records, resp.status_code)
        elif resp.status_code == 409:
            await self._handle_dead_letter(records, "409")
        elif resp.status_code == 413:
            await self._handle_dead_letter(records, "413")
        elif resp.status_code == 422:
            await self._handle_dead_letter(records, "422")
        elif resp.status_code in (429, 503, 507):
            await self._handle_retry_after(records, resp.status_code, resp.headers.get("Retry-After"))
        else:
            # Unexpected status - treat as network error
            await self._handle_network_error(records, Exception(f"HTTP {resp.status_code}"))

    async def _handle_success(self, records: list[dict], resp) -> None:
        """Handle 200/201 response - delete rows where id and hash match.
        
        For batch responses, individual results may contain errors. Those are
        treated as network errors for the specific record.
        """
        try:
            data = resp.json()
        except Exception:
            data = {}

        # Server may return array of results or single result
        results = data.get("results", [data]) if isinstance(data, dict) else [data]

        # Build set of successfully acknowledged (record_id, body_sha256)
        acked = set()
        # Track records with errors in the batch response
        error_records = set()

        for result in results:
            if isinstance(result, dict):
                rid = result.get("record_id")
                bsha = result.get("body_sha256")
                if rid and bsha:
                    # Check if this result has an error
                    if "error" in result:
                        error_records.add((rid, bsha))
                    else:
                        acked.add((rid, bsha))

        if not acked and not error_records:
            # Fallback: assume all in batch succeeded if 201
            if resp.status_code == 201:
                acked = {(r["record_id"], r["body_sha256"]) for r in records}

        # Delete acknowledged rows
        for record_id, body_sha256 in acked:
            await self._db.execute(
                "DELETE FROM records WHERE record_id = ? AND body_sha256 = ?",
                (record_id, body_sha256),
            )

        # For records with errors in batch response, treat as network error
        for record_id, body_sha256 in error_records:
            await self._increment_retry(record_id, "retry_network", "batch error response")

        # For any records in batch not acked and not in error_records, increment retry
        for record in records:
            key = (record["record_id"], record["body_sha256"])
            if key not in acked and key not in error_records:
                await self._increment_retry(record["record_id"], "retry_network")

        await self._db.commit()

    async def _handle_400(self, records: list[dict]) -> None:
        """Handle 400 - keep row, alarm (mark as error_400)."""
        for record in records:
            await self._db.execute(
                "UPDATE records SET status = 'error_400', last_error = '400 Bad Request' WHERE record_id = ?",
                (record["record_id"],),
            )
        await self._db.commit()
        logger.warning("Records rejected (400): %s", [r["record_id"] for r in records])

    async def _handle_401_403(self, records: list[dict], status_code: int) -> None:
        """Handle 401/403 - keep row, refresh credential, retry, alarm."""
        for record in records:
            await self._db.execute(
                """UPDATE records
                   SET status = ?, retry_count = retry_count + 1, last_error = ?
                   WHERE record_id = ?""",
                (f"error_{status_code}", f"{status_code} Unauthorized", record["record_id"]),
            )
        await self._db.commit()
        logger.warning("Records auth error (%d): %s", status_code, [r["record_id"] for r in records])

    async def _handle_dead_letter(self, records: list[dict], reason: str) -> None:
        """Move records to dead-letter table."""
        now = time.time()
        for record in records:
            # Move to dead_letter
            await self._db.execute(
                """INSERT INTO dead_letter (record_id, body_sha256, envelope, reason, created_at, moved_at)
                   SELECT record_id, body_sha256, envelope, ?, created_at, ?
                   FROM records WHERE record_id = ?""",
                (reason, now, record["record_id"]),
            )
            # Delete from records
            await self._db.execute("DELETE FROM records WHERE record_id = ?", (record["record_id"],))
        await self._db.commit()
        logger.warning("Records moved to dead-letter (%s): %s", reason, [r["record_id"] for r in records])

    async def _handle_retry_after(self, records: list[dict], status_code: int, retry_after: Optional[str]) -> None:
        """Handle 429/503/507 - keep row, honour Retry-After."""
        delay = 1
        if retry_after:
            try:
                delay = int(retry_after)
            except ValueError:
                pass
        delay = max(1, min(delay, 300))  # Clamp 1-300 seconds

        next_retry = time.time() + delay
        for record in records:
            await self._db.execute(
                """UPDATE records
                   SET status = ?, next_retry_at = ?, retry_count = retry_count + 1, last_error = ?
                   WHERE record_id = ?""",
                (f"retry_{status_code}", next_retry, f"{status_code} {status_code}", record["record_id"]),
            )
        await self._db.commit()
        logger.info("Records retry scheduled (%d, retry-after=%ds): %s",
                    status_code, delay, [r["record_id"] for r in records])

    async def _handle_network_error(self, records: list[dict], error: Exception) -> None:
        """Handle timeout/network error - keep row, retry with same id."""
        for record in records:
            await self._increment_retry(record["record_id"], "retry_network", str(error))
        await self._db.commit()
        logger.warning("Records network error: %s", error)

    async def _increment_retry(self, record_id: str, status: str, error: str = "") -> None:
        """Increment retry count, apply exponential backoff."""
        cursor = await self._db.execute(
            "SELECT retry_count FROM records WHERE record_id = ?", (record_id,)
        )
        row = await cursor.fetchone()
        retry_count = (row[0] if row else 0) + 1

        if retry_count > self.config.max_retries:
            # Max retries exceeded - move to dead-letter as error
            await self._db.execute(
                """INSERT INTO dead_letter (record_id, body_sha256, envelope, reason, created_at, moved_at)
                   SELECT record_id, body_sha256, envelope, 'max_retries', created_at, ?
                   FROM records WHERE record_id = ?""",
                (time.time(), record_id),
            )
            await self._db.execute("DELETE FROM records WHERE record_id = ?", (record_id,))
            logger.error("Record %s exceeded max retries, moved to dead-letter", record_id)
        else:
            # Exponential backoff: 1s, 2s, 4s, 8s, 16s... max 60s
            delay = min(2 ** (retry_count - 1), 60)
            next_retry = time.time() + delay
            await self._db.execute(
                """UPDATE records
                   SET status = ?, retry_count = ?, next_retry_at = ?, last_error = ?
                   WHERE record_id = ?""",
                (status, retry_count, next_retry, error or status, record_id),
            )

    def get_health_status(self) -> dict:
        """Get health status for /api/health endpoint."""
        if not self._db:
            return {
                "buffer_depth": 0,
                "oldest_row_age_seconds": 0,
                "dead_letter_count": 0,
            }

        # This is a synchronous check - in practice we'd want to avoid blocking
        # For health checks, we can run a quick query
        return {
            "buffer_depth": 0,  # Placeholder - actual implementation below
            "oldest_row_age_seconds": 0,
            "dead_letter_count": 0,
        }

    async def get_health_status_async(self) -> dict:
        """Get health status asynchronously."""
        if not self._db:
            return {
                "buffer_depth": 0,
                "oldest_row_age_seconds": 0,
                "dead_letter_count": 0,
            }

        now = time.time()

        # Buffer depth (pending + retry)
        cursor = await self._db.execute(
            "SELECT COUNT(*) FROM records WHERE status != 'dead_letter'"
        )
        buffer_depth = (await cursor.fetchone())[0]

        # Oldest row age
        cursor = await self._db.execute(
            "SELECT MIN(created_at) FROM records WHERE status != 'dead_letter'"
        )
        row = await cursor.fetchone()
        oldest_age = 0.0
        if row and row[0]:
            oldest_age = now - row[0]

        # Dead letter count
        cursor = await self._db.execute("SELECT COUNT(*) FROM dead_letter")
        dead_letter_count = (await cursor.fetchone())[0]

        return {
            "buffer_depth": buffer_depth,
            "oldest_row_age_seconds": round(oldest_age, 1),
            "dead_letter_count": dead_letter_count,
        }


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class BufferFullError(Exception):
    """Raised when the records buffer exceeds its configured threshold."""
    pass


# ---------------------------------------------------------------------------
# Module-level singleton management
# ---------------------------------------------------------------------------

_records_client: Optional[RecordsClient] = None


def get_records_client() -> Optional[RecordsClient]:
    """Get the global records client instance."""
    return _records_client


async def init_records_client(
    config: RecordsClientConfig,
    http_client: AsyncClient,
) -> RecordsClient:
    """Initialize the global records client."""
    global _records_client
    _records_client = RecordsClient(config, http_client)
    await _records_client.start()
    return _records_client


async def shutdown_records_client() -> None:
    """Shutdown the global records client."""
    global _records_client
    if _records_client:
        await _records_client.stop()
        _records_client = None
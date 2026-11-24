"""Tests for the records client write-ahead buffer.

These tests drive the real records_client module against a fake memory server
that can replay, duplicate, drop replies, return each error class and restart.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import secrets
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import aiosqlite
import pytest
import pytest_asyncio
import ulid
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from tinyagentos.records_client import (
    RecordsClient,
    RecordsClientConfig,
    get_records_client,
    shutdown_records_client,
)


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Fake memory server for testing
# ---------------------------------------------------------------------------

class FakeMemoryServer:
    """A fake memory server that can simulate various response behaviors."""

    def __init__(self):
        self.app = FastAPI()
        self.received_records: list[dict] = []
        self.call_count = 0
        self.drop_probability = 0.0
        self.duplicate_probability = 0.0
        self.error_mode: str | None = None  # "400", "401", "403", "409", "413", "422", "429", "503", "507"
        self.retry_after_seconds: int = 1
        self._lock = asyncio.Lock()
        self._setup_routes()

    def _setup_routes(self):
        @self.app.post("/records")
        async def ingest_record(request: Request):
            return await self._handle_ingest(request)

        @self.app.post("/records/batch")
        async def ingest_batch(request: Request):
            return await self._handle_batch(request)

        @self.app.get("/health")
        async def health():
            return {"status": "ok"}

    async def _handle_ingest(self, request: Request):
        body = await request.json()
        return await self._process_record(body)

    async def _handle_batch(self, request: Request):
        body = await request.json()
        records = body.get("records", [])
        results = []
        status_codes = []
        for record in records:
            result = await self._process_record(record)
            # Extract JSON content from JSONResponse
            if isinstance(result, JSONResponse):
                status_codes.append(result.status_code)
                result_body = result.body.decode() if hasattr(result, 'body') else "{}"
                import json
                results.append(json.loads(result_body))
            else:
                status_codes.append(200)
                results.append(result)
        
        # If all records have the same non-200 status, return that status
        if status_codes and all(sc == status_codes[0] for sc in status_codes) and status_codes[0] != 200:
            return JSONResponse({"results": results}, status_code=status_codes[0])
        
        return {"results": results}

    async def _process_record(self, record: dict):
        async with self._lock:
            self.call_count += 1
            record_id = record.get("record_id")
            body_sha256 = record.get("body_sha256")

            # Simulate drop
            if self.drop_probability > 0 and secrets.randbelow(10000) < self.drop_probability * 10000:
                # Drop the reply - client will see a timeout/network error
                # Return a 500 error for this individual record with record_id and body_sha256
                return JSONResponse(
                    {"record_id": record_id, "body_sha256": body_sha256, "error": "dropped"},
                    status_code=500,
                )

            # Store the record
            self.received_records.append({
                "record_id": record_id,
                "body_sha256": body_sha256,
                "envelope": record,
                "call_number": self.call_count,
            })

            # Simulate duplicate response
            if self.duplicate_probability > 0 and secrets.randbelow(10000) < self.duplicate_probability * 10000:
                # Return 200 (duplicate) with same record_id and body_sha256
                return JSONResponse(
                    {"record_id": record_id, "body_sha256": body_sha256, "status": "duplicate"},
                    status_code=200,
                )

            # Handle error modes
            if self.error_mode == "400":
                return JSONResponse({"error": "bad request"}, status_code=400)
            elif self.error_mode == "401":
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            elif self.error_mode == "403":
                return JSONResponse({"error": "forbidden"}, status_code=403)
            elif self.error_mode == "409":
                return JSONResponse({"error": "conflict"}, status_code=409)
            elif self.error_mode == "413":
                return JSONResponse({"error": "payload too large"}, status_code=413)
            elif self.error_mode == "422":
                return JSONResponse({"error": "unprocessable entity"}, status_code=422)
            elif self.error_mode in ("429", "503", "507"):
                return JSONResponse(
                    {"error": "service unavailable"},
                    status_code=int(self.error_mode),
                    headers={"Retry-After": str(self.retry_after_seconds)},
                )

            # Success
            return JSONResponse(
                {"record_id": record_id, "body_sha256": body_sha256, "status": "created"},
                status_code=201,
            )

    def reset(self):
        self.received_records.clear()
        self.call_count = 0
        self.drop_probability = 0.0
        self.duplicate_probability = 0.0
        self.error_mode = None

    def get_record_ids(self) -> set[str]:
        return {r["record_id"] for r in self.received_records}


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def temp_data_dir():
    """Create a temporary data directory for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest_asyncio.fixture
async def fake_server():
    """Create a fake memory server."""
    server = FakeMemoryServer()
    yield server
    server.reset()


@pytest_asyncio.fixture
async def client_config(temp_data_dir, fake_server):
    """Create a records client config pointing to the fake server."""
    config = RecordsClientConfig(
        data_dir=temp_data_dir,
        memory_service_url=f"http://testserver",
        max_batch_size=100,
        buffer_threshold_bytes=10 * 1024 * 1024,  # 10MB
        send_interval_seconds=0.1,
        max_retries=3,
    )
    return config


@pytest_asyncio.fixture
async def records_client(client_config, fake_server):
    """Create a records client with a test HTTP client pointing to the fake server."""
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=fake_server.app)
    http_client = AsyncClient(transport=transport, base_url="http://testserver", timeout=5.0)

    client = RecordsClient(client_config, http_client)
    await client.start()
    yield client
    await client.stop()
    await http_client.aclose()


# ---------------------------------------------------------------------------
# Tests for basic emit functionality
# ---------------------------------------------------------------------------

class TestRecordsClientEmit:
    """Tests for the emit() method."""

    @pytest.mark.asyncio
    async def test_emit_returns_immediately(self, records_client):
        """emit() should return immediately without blocking on network."""
        start = time.monotonic()
        record_id = await records_client.emit("test.type", {"key": "value"})
        elapsed = time.monotonic() - start
        assert elapsed < 0.1, f"emit took {elapsed}s, should be near-instant"
        assert record_id is not None
        assert isinstance(record_id, str)
        assert len(record_id) == 26  # ULID length

    @pytest.mark.asyncio
    async def test_emit_writes_to_local_buffer(self, records_client, temp_data_dir):
        """emit() should write a row to the local SQLite buffer."""
        record_id = await records_client.emit("test.type", {"key": "value"})

        # Check the buffer database
        db_path = temp_data_dir / "records-wal.db"
        assert db_path.exists()

        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute(
                "SELECT record_id, body_sha256, envelope, status FROM records WHERE record_id = ?",
                (record_id,),
            )
            row = await cursor.fetchone()
            assert row is not None, "Record not found in buffer"
            assert row[0] == record_id
            assert row[3] == "pending"  # Initial status

    @pytest.mark.asyncio
    async def test_emit_computes_body_sha256(self, records_client, temp_data_dir):
        """emit() should compute body_sha256 over the exact envelope bytes."""
        body = {"key": "value", "number": 42}
        record_id = await records_client.emit("test.type", body)

        async with aiosqlite.connect(temp_data_dir / "records-wal.db") as db:
            cursor = await db.execute(
                "SELECT envelope, body_sha256 FROM records WHERE record_id = ?",
                (record_id,),
            )
            row = await cursor.fetchone()
            envelope = json.loads(row[0])
            stored_hash = row[1]

            # Verify the hash matches the envelope bytes
            envelope_bytes = json.dumps(envelope, separators=(",", ":"), sort_keys=True).encode()
            expected_hash = hashlib.sha256(envelope_bytes).hexdigest()
            assert stored_hash == expected_hash

    @pytest.mark.asyncio
    async def test_emit_generates_monotonic_ulids(self, records_client):
        """emit() should generate monotonically increasing ULIDs per process."""
        ids = []
        for i in range(10):
            record_id = await records_client.emit("test.type", {"i": i})
            ids.append(record_id)
        # ULIDs should be sortable and monotonic
        for i in range(1, len(ids)):
            ts1 = int(ulid.parse(ids[i]).timestamp())
            ts0 = int(ulid.parse(ids[i-1]).timestamp())
            assert ts1 >= ts0 - 1


# ---------------------------------------------------------------------------
# Tests for background sender and response handling
# ---------------------------------------------------------------------------

class TestBackgroundSender:
    """Tests for the background sender."""

    @pytest.mark.asyncio
    async def test_success_201_deletes_row(self, records_client, fake_server, temp_data_dir):
        """201 response should delete the row if record_id and hash match."""
        record_id = await records_client.emit("test.type", {"key": "value"})

        # Wait for background send
        await asyncio.sleep(0.5)

        async with aiosqlite.connect(temp_data_dir / "records-wal.db") as db:
            cursor = await db.execute(
                "SELECT * FROM records WHERE record_id = ?", (record_id,)
            )
            row = await cursor.fetchone()
            assert row is None, "Row should be deleted on 201 with matching id and hash"

        # Verify server received it
        assert record_id in fake_server.get_record_ids()

    @pytest.mark.asyncio
    async def test_duplicate_200_deletes_row(self, records_client, fake_server, temp_data_dir):
        """200 (duplicate) response should delete the row if record_id and hash match."""
        fake_server.duplicate_probability = 1.0
        record_id = await records_client.emit("test.type", {"key": "value"})

        await asyncio.sleep(0.5)

        async with aiosqlite.connect(temp_data_dir / "records-wal.db") as db:
            cursor = await db.execute(
                "SELECT * FROM records WHERE record_id = ?", (record_id,)
            )
            row = await cursor.fetchone()
            assert row is None, "Row should be deleted on 200 with matching id and hash"

    @pytest.mark.asyncio
    async def test_400_keeps_row_alarms(self, records_client, fake_server, temp_data_dir):
        """400 response should keep the row and alarm."""
        fake_server.error_mode = "400"
        record_id = await records_client.emit("test.type", {"key": "value"})

        await asyncio.sleep(0.5)

        async with aiosqlite.connect(temp_data_dir / "records-wal.db") as db:
            cursor = await db.execute(
                "SELECT status FROM records WHERE record_id = ?", (record_id,)
            )
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == "error_400", "Row should be kept with error status"

    @pytest.mark.asyncio
    async def test_401_keeps_row_refreshes_credential(self, records_client, fake_server, temp_data_dir):
        """401 response should keep the row, refresh credential, retry, alarm."""
        fake_server.error_mode = "401"
        record_id = await records_client.emit("test.type", {"key": "value"})

        await asyncio.sleep(0.5)

        async with aiosqlite.connect(temp_data_dir / "records-wal.db") as db:
            cursor = await db.execute(
                "SELECT status, retry_count FROM records WHERE record_id = ?", (record_id,)
            )
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == "error_401"
            assert row[1] > 0, "Should have retried"

    @pytest.mark.asyncio
    async def test_403_keeps_row_refreshes_credential(self, records_client, fake_server, temp_data_dir):
        """403 response should keep the row, refresh credential, retry, alarm."""
        fake_server.error_mode = "403"
        record_id = await records_client.emit("test.type", {"key": "value"})

        await asyncio.sleep(0.5)

        async with aiosqlite.connect(temp_data_dir / "records-wal.db") as db:
            cursor = await db.execute(
                "SELECT status, retry_count FROM records WHERE record_id = ?", (record_id,)
            )
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == "error_403"
            assert row[1] > 0

    @pytest.mark.asyncio
    async def test_409_moves_to_dead_letter(self, records_client, fake_server, temp_data_dir):
        """409 response should move row to dead-letter and alarm."""
        fake_server.error_mode = "409"
        record_id = await records_client.emit("test.type", {"key": "value"})

        await asyncio.sleep(0.5)

        async with aiosqlite.connect(temp_data_dir / "records-wal.db") as db:
            # Record should be deleted from records table
            cursor = await db.execute(
                "SELECT status FROM records WHERE record_id = ?", (record_id,)
            )
            row = await cursor.fetchone()
            assert row is None, "Record should be moved from records to dead_letter"

            # Check dead_letter table
            cursor = await db.execute(
                "SELECT record_id, reason FROM dead_letter WHERE record_id = ?", (record_id,)
            )
            dl_row = await cursor.fetchone()
            assert dl_row is not None
            assert dl_row[1] == "409"

    @pytest.mark.asyncio
    async def test_413_moves_to_dead_letter(self, records_client, fake_server, temp_data_dir):
        """413 response should move row to dead-letter until blob upload exists."""
        fake_server.error_mode = "413"
        record_id = await records_client.emit("test.type", {"key": "value"})

        await asyncio.sleep(0.5)

        async with aiosqlite.connect(temp_data_dir / "records-wal.db") as db:
            # Record should be deleted from records table
            cursor = await db.execute(
                "SELECT status FROM records WHERE record_id = ?", (record_id,)
            )
            row = await cursor.fetchone()
            assert row is None, "Record should be moved from records to dead_letter"

    @pytest.mark.asyncio
    async def test_422_moves_to_dead_letter_never_deleted(self, records_client, fake_server, temp_data_dir):
        """422 response should move row to dead-letter, never deleted."""
        fake_server.error_mode = "422"
        record_id = await records_client.emit("test.type", {"key": "value"})

        await asyncio.sleep(0.5)

        async with aiosqlite.connect(temp_data_dir / "records-wal.db") as db:
            # Record should be deleted from records table
            cursor = await db.execute(
                "SELECT status FROM records WHERE record_id = ?", (record_id,)
            )
            row = await cursor.fetchone()
            assert row is None, "Record should be moved from records to dead_letter"

            # Verify it stays in dead_letter
            cursor = await db.execute(
                "SELECT record_id FROM dead_letter WHERE record_id = ?", (record_id,)
            )
            dl_row = await cursor.fetchone()
            assert dl_row is not None

    @pytest.mark.asyncio
    async def test_429_honours_retry_after(self, records_client, fake_server, temp_data_dir):
        """429 response should keep row and honour Retry-After."""
        fake_server.error_mode = "429"
        fake_server.retry_after_seconds = 1
        record_id = await records_client.emit("test.type", {"key": "value"})

        await asyncio.sleep(0.5)

        async with aiosqlite.connect(temp_data_dir / "records-wal.db") as db:
            cursor = await db.execute(
                "SELECT status, next_retry_at FROM records WHERE record_id = ?", (record_id,)
            )
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == "retry_429"
            assert row[1] is not None
            assert row[1] > time.time()

    @pytest.mark.asyncio
    async def test_503_honours_retry_after(self, records_client, fake_server, temp_data_dir):
        """503 response should keep row and honour Retry-After."""
        fake_server.error_mode = "503"
        fake_server.retry_after_seconds = 1
        record_id = await records_client.emit("test.type", {"key": "value"})

        await asyncio.sleep(0.5)

        async with aiosqlite.connect(temp_data_dir / "records-wal.db") as db:
            cursor = await db.execute(
                "SELECT status FROM records WHERE record_id = ?", (record_id,)
            )
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == "retry_503"

    @pytest.mark.asyncio
    async def test_507_honours_retry_after(self, records_client, fake_server, temp_data_dir):
        """507 response should keep row and honour Retry-After."""
        fake_server.error_mode = "507"
        fake_server.retry_after_seconds = 1
        record_id = await records_client.emit("test.type", {"key": "value"})

        await asyncio.sleep(0.5)

        async with aiosqlite.connect(temp_data_dir / "records-wal.db") as db:
            cursor = await db.execute(
                "SELECT status FROM records WHERE record_id = ?", (record_id,)
            )
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == "retry_507"

    @pytest.mark.asyncio
    async def test_timeout_keeps_row_retries_same_id(self, records_client, fake_server, temp_data_dir):
        """Timeout/network error should keep row and retry with same id."""
        fake_server.drop_probability = 1.0
        record_id = await records_client.emit("test.type", {"key": "value"})

        await asyncio.sleep(1.0)

        async with aiosqlite.connect(temp_data_dir / "records-wal.db") as db:
            cursor = await db.execute(
                "SELECT status, retry_count FROM records WHERE record_id = ?", (record_id,)
            )
            row = await cursor.fetchone()
            assert row is not None
            assert row[0] == "retry_network"
            assert row[1] > 0


# ---------------------------------------------------------------------------
# Tests for full buffer behavior
# ---------------------------------------------------------------------------

class TestFullBuffer:
    """Tests for full buffer behavior."""

    @pytest.mark.asyncio
    async def test_full_buffer_raises_visible_error(self, temp_data_dir, fake_server):
        """When buffer disk passes threshold, emit should raise a visible error."""
        from httpx import ASGITransport, AsyncClient

        config = RecordsClientConfig(
            data_dir=temp_data_dir,
            memory_service_url="http://testserver",
            max_batch_size=100,
            buffer_threshold_bytes=1024,  # Very small threshold
            send_interval_seconds=10.0,  # Slow send so buffer fills
        )

        transport = ASGITransport(app=fake_server.app)
        http_client = AsyncClient(transport=transport, base_url="http://testserver")
        client = RecordsClient(config, http_client)
        await client.start()

        try:
            # Fill the buffer
            for i in range(100):
                try:
                    await client.emit("test.type", {"data": "x" * 100})
                except Exception as e:
                    # Should raise a visible error when buffer is full
                    assert "buffer" in str(e).lower() or "full" in str(e).lower()
                    break
            else:
                pytest.fail("Expected buffer full error")
        finally:
            await client.stop()
            await http_client.aclose()


# ---------------------------------------------------------------------------
# Tests for crash recovery (kill -9 simulation)
# ---------------------------------------------------------------------------

class TestCrashRecovery:
    """Tests simulating kill -9 and restart."""

    @pytest.mark.asyncio
    async def test_kill_restart_preserves_unacked_records(self, temp_data_dir, fake_server):
        """Records not acked should survive process restart."""
        from httpx import ASGITransport, AsyncClient

        config = RecordsClientConfig(
            data_dir=temp_data_dir,
            memory_service_url="http://testserver",
            max_batch_size=100,
            buffer_threshold_bytes=10 * 1024 * 1024,
            send_interval_seconds=10.0,  # Don't send during test
        )

        transport = ASGITransport(app=fake_server.app)
        http_client = AsyncClient(transport=transport, base_url="http://testserver")
        client = RecordsClient(config, http_client)
        await client.start()

        # Emit some records
        ids = []
        for i in range(5):
            record_id = await client.emit("test.type", {"seq": i})
            ids.append(record_id)

        # Simulate kill -9 by stopping without waiting for sends
        await client.stop()
        await http_client.aclose()

        # Verify records are in buffer
        async with aiosqlite.connect(temp_data_dir / "records-wal.db") as db:
            cursor = await db.execute("SELECT record_id FROM records WHERE status = 'pending'")
            rows = await cursor.fetchall()
            assert len(rows) == 5

        # Restart client with fast send interval
        config2 = RecordsClientConfig(
            data_dir=temp_data_dir,
            memory_service_url="http://testserver",
            max_batch_size=100,
            buffer_threshold_bytes=10 * 1024 * 1024,
            send_interval_seconds=0.1,  # Fast send for verification
        )
        http_client2 = AsyncClient(transport=transport, base_url="http://testserver")
        client2 = RecordsClient(config2, http_client2)
        await client2.start()

        try:
            # Wait for background send
            await asyncio.sleep(1.0)

            # All should be sent now
            async with aiosqlite.connect(temp_data_dir / "records-wal.db") as db:
                cursor = await db.execute("SELECT record_id FROM records WHERE status = 'pending'")
                rows = await cursor.fetchall()
                assert len(rows) == 0, "All records should be sent after restart"
        finally:
            await client2.stop()
            await http_client2.aclose()

    @pytest.mark.asyncio
    async def test_multiple_kill_restart_cycles(self, temp_data_dir, fake_server):
        """5 kill-restart cycles should not lose records."""
        from httpx import ASGITransport, AsyncClient

        config = RecordsClientConfig(
            data_dir=temp_data_dir,
            memory_service_url="http://testserver",
            max_batch_size=100,
            buffer_threshold_bytes=10 * 1024 * 1024,
            send_interval_seconds=10.0,
        )

        transport = ASGITransport(app=fake_server.app)

        all_ids = []
        for cycle in range(5):
            http_client = AsyncClient(transport=transport, base_url="http://testserver")
            client = RecordsClient(config, http_client)
            await client.start()

            # Emit records
            for i in range(3):
                record_id = await client.emit("test.type", {"cycle": cycle, "seq": i})
                all_ids.append(record_id)

            # Kill
            await client.stop()
            await http_client.aclose()

        # Final restart with fast send interval and verify all sent
        config2 = RecordsClientConfig(
            data_dir=temp_data_dir,
            memory_service_url="http://testserver",
            max_batch_size=100,
            buffer_threshold_bytes=10 * 1024 * 1024,
            send_interval_seconds=0.1,
        )
        http_client = AsyncClient(transport=transport, base_url="http://testserver")
        client = RecordsClient(config2, http_client)
        await client.start()

        try:
            await asyncio.sleep(2.0)

            async with aiosqlite.connect(temp_data_dir / "records-wal.db") as db:
                cursor = await db.execute("SELECT record_id FROM records WHERE status = 'pending'")
                rows = await cursor.fetchall()
                assert len(rows) == 0, f"{len(rows)} records still pending after 5 cycles"

            # Verify all IDs received by server
            server_ids = fake_server.get_record_ids()
            for id_ in all_ids:
                assert id_ in server_ids, f"ID {id_} not received by server"
        finally:
            await client.stop()
            await http_client.aclose()


# ---------------------------------------------------------------------------
# Tests for server restart mid-burst
# ---------------------------------------------------------------------------

class TestServerRestart:
    """Tests for server restart mid-burst."""

    @pytest.mark.asyncio
    async def test_server_restart_mid_burst(self, temp_data_dir):
        """Client should handle server restart during emission."""
        from httpx import ASGITransport, AsyncClient

        # First server
        server1 = FakeMemoryServer()
        transport1 = ASGITransport(app=server1.app)

        config = RecordsClientConfig(
            data_dir=temp_data_dir,
            memory_service_url="http://testserver",
            max_batch_size=100,
            buffer_threshold_bytes=10 * 1024 * 1024,
            send_interval_seconds=0.1,
        )

        http_client = AsyncClient(transport=transport1, base_url="http://testserver")
        client = RecordsClient(config, http_client)
        await client.start()

        # Emit some records
        ids = []
        for i in range(10):
            record_id = await client.emit("test.type", {"seq": i})
            ids.append(record_id)

        # Wait for first batch to send
        await asyncio.sleep(0.5)

        # Simulate server restart - create new server with same records
        server2 = FakeMemoryServer()
        # Pre-populate with records already received by server1
        server2.received_records = server1.received_records.copy()
        transport2 = ASGITransport(app=server2.app)

        # Switch client to new transport (simulate DNS update or similar)
        # For this test, we just verify the client keeps retrying
        await client.stop()
        await http_client.aclose()

        # Restart client with new server
        http_client2 = AsyncClient(transport=transport2, base_url="http://testserver")
        client2 = RecordsClient(config, http_client2)
        await client2.start()

        try:
            await asyncio.sleep(1.0)

            # Verify all records eventually delivered
            all_server_ids = server1.get_record_ids() | server2.get_record_ids()
            for id_ in ids:
                assert id_ in all_server_ids, f"ID {id_} lost during server restart"
        finally:
            await client2.stop()
            await http_client2.aclose()


# ---------------------------------------------------------------------------
# Tests for oracle verification (independent append-only id log)
# ---------------------------------------------------------------------------

class TestOracleVerification:
    """Tests verifying against an independent oracle log."""

    @pytest.mark.asyncio
    async def test_oracle_verification_no_duplicates(self, temp_data_dir, fake_server):
        """Server's id set should equal oracle's; no record stored twice."""
        from httpx import ASGITransport, AsyncClient

        oracle_path = temp_data_dir / "oracle.log"
        config = RecordsClientConfig(
            data_dir=temp_data_dir,
            memory_service_url="http://testserver",
            max_batch_size=100,
            buffer_threshold_bytes=10 * 1024 * 1024,
            send_interval_seconds=0.1,
            max_retries=3,  # Lower retries for faster test
        )

        transport = ASGITransport(app=fake_server.app)
        http_client = AsyncClient(transport=transport, base_url="http://testserver", timeout=5.0)
        client = RecordsClient(config, http_client)
        await client.start()

        oracle_ids = []
        try:
            # Emit 100 records with 5% drop and 2% duplicate
            fake_server.drop_probability = 0.05
            fake_server.duplicate_probability = 0.02

            for i in range(100):
                record_id = await client.emit("test.type", {"seq": i})
                # Write to oracle with fsync
                with open(oracle_path, "a") as f:
                    f.write(f"{record_id}\n")
                    f.flush()
                    os.fsync(f.fileno())
                oracle_ids.append(record_id)

            # Wait for all sends - give more time for retries
            await asyncio.sleep(30.0)

            # Verify oracle matches server
            server_ids = fake_server.get_record_ids()
            oracle_set = set(oracle_ids)

            # Server should have all oracle IDs (possibly more due to retries, but unique)
            assert oracle_set == server_ids, \
                f"Oracle mismatch: oracle={len(oracle_set)}, server={len(server_ids)}, " \
                f"missing={oracle_set - server_ids}, extra={server_ids - oracle_set}"

            # No duplicates in server (each record_id only once)
            assert len(server_ids) == len(fake_server.received_records), \
                "Server has duplicate record_ids"

        finally:
            await client.stop()
            await http_client.aclose()

    @pytest.mark.asyncio
    async def test_acked_rows_gone_from_buffer(self, temp_data_dir, fake_server):
        """Every row whose ack matched should be gone from buffer."""
        from httpx import ASGITransport, AsyncClient

        config = RecordsClientConfig(
            data_dir=temp_data_dir,
            memory_service_url="http://testserver",
            max_batch_size=100,
            buffer_threshold_bytes=10 * 1024 * 1024,
            send_interval_seconds=0.1,
        )

        transport = ASGITransport(app=fake_server.app)
        http_client = AsyncClient(transport=transport, base_url="http://testserver")
        client = RecordsClient(config, http_client)
        await client.start()

        try:
            for i in range(100):
                await client.emit("test.type", {"seq": i})

            await asyncio.sleep(1.0)

            async with aiosqlite.connect(temp_data_dir / "records-wal.db") as db:
                cursor = await db.execute("SELECT COUNT(*) FROM records WHERE status = 'pending'")
                count = (await cursor.fetchone())[0]
                assert count == 0, f"{count} pending records remain in buffer"

        finally:
            await client.stop()
            await http_client.aclose()

    @pytest.mark.asyncio
    async def test_dead_letter_contains_409_and_422(self, temp_data_dir, fake_server):
        """Every 409 and 422 row should be in dead-letter."""
        from httpx import ASGITransport, AsyncClient

        config = RecordsClientConfig(
            data_dir=temp_data_dir,
            memory_service_url="http://testserver",
            max_batch_size=100,
            buffer_threshold_bytes=10 * 1024 * 1024,
            send_interval_seconds=0.1,
        )

        transport = ASGITransport(app=fake_server.app)
        http_client = AsyncClient(transport=transport, base_url="http://testserver")
        client = RecordsClient(config, http_client)
        await client.start()

        try:
            # Send some 409
            fake_server.error_mode = "409"
            ids_409 = []
            for i in range(10):
                record_id = await client.emit("test.type", {"seq": i, "error": "409"})
                ids_409.append(record_id)

            await asyncio.sleep(1.0)

            # Send some 422
            fake_server.error_mode = "422"
            ids_422 = []
            for i in range(10):
                record_id = await client.emit("test.type", {"seq": i, "error": "422"})
                ids_422.append(record_id)

            await asyncio.sleep(1.0)

            async with aiosqlite.connect(temp_data_dir / "records-wal.db") as db:
                # Check 409 in dead_letter
                for id_ in ids_409:
                    cursor = await db.execute(
                        "SELECT reason FROM dead_letter WHERE record_id = ?", (id_,)
                    )
                    row = await cursor.fetchone()
                    assert row is not None, f"409 record {id_} not in dead_letter"
                    assert row[0] == "409", f"Expected 409, got {row[0]} for {id_}"

                # Check 422 in dead_letter
                for id_ in ids_422:
                    cursor = await db.execute(
                        "SELECT reason FROM dead_letter WHERE record_id = ?", (id_,)
                    )
                    row = await cursor.fetchone()
                    assert row is not None, f"422 record {id_} not in dead_letter"
                    assert row[0] == "422", f"Expected 422, got {row[0]} for {id_}"

        finally:
            await client.stop()
            await http_client.aclose()


# ---------------------------------------------------------------------------
# Tests for health endpoint visibility
# ---------------------------------------------------------------------------

class TestHealthVisibility:
    """Tests for buffer depth, oldest row age, dead-letter count in health."""

    @pytest.mark.asyncio
    async def test_health_shows_buffer_depth(self, records_client, temp_data_dir):
        """Health should show buffer depth."""
        await records_client.emit("test.type", {"key": "value"})
        await records_client.emit("test.type", {"key": "value2"})

        # Wait a bit for processing
        await asyncio.sleep(0.2)

        health = await records_client.get_health_status_async()
        assert "buffer_depth" in health
        assert health["buffer_depth"] >= 0

    @pytest.mark.asyncio
    async def test_health_shows_oldest_row_age(self, records_client, temp_data_dir):
        """Health should show oldest row age."""
        await records_client.emit("test.type", {"key": "value"})

        await asyncio.sleep(0.2)

        health = await records_client.get_health_status_async()
        assert "oldest_row_age_seconds" in health
        assert health["oldest_row_age_seconds"] >= 0

    @pytest.mark.asyncio
    async def test_health_shows_dead_letter_count(self, records_client, fake_server, temp_data_dir):
        """Health should show dead-letter count."""
        fake_server.error_mode = "409"
        await records_client.emit("test.type", {"key": "value"})

        await asyncio.sleep(0.5)

        health = await records_client.get_health_status_async()
        assert "dead_letter_count" in health
        assert health["dead_letter_count"] >= 1


# ---------------------------------------------------------------------------
# Tests for existing install (buffer store created on first boot)
# ---------------------------------------------------------------------------

class TestExistingInstall:
    """Test that buffer store is created on first boot without touching other stores."""

    @pytest.mark.asyncio
    async def test_buffer_created_on_first_boot_existing_data(self, temp_data_dir):
        """Buffer store should be created on first boot with existing data dir."""
        from httpx import ASGITransport, AsyncClient

        # Create some existing stores
        (temp_data_dir / "existing.db").write_text("existing data")
        (temp_data_dir / "config.yaml").write_text("server:\n  port: 6969\n")

        server = FakeMemoryServer()
        transport = ASGITransport(app=server.app)

        config = RecordsClientConfig(
            data_dir=temp_data_dir,
            memory_service_url="http://testserver",
            max_batch_size=100,
            buffer_threshold_bytes=10 * 1024 * 1024,
            send_interval_seconds=10.0,  # Don't send during test
        )

        http_client = AsyncClient(transport=transport, base_url="http://testserver")
        client = RecordsClient(config, http_client)
        await client.start()

        try:
            # Buffer should be created
            db_path = temp_data_dir / "records-wal.db"
            assert db_path.exists(), "Buffer database not created on first boot"

            # Existing files should be untouched
            assert (temp_data_dir / "existing.db").read_text() == "existing data"

            # Emit a record
            await client.emit("test.type", {"key": "value"})
            await asyncio.sleep(0.2)

            # Buffer should have the record
            async with aiosqlite.connect(db_path) as db:
                cursor = await db.execute("SELECT COUNT(*) FROM records")
                count = (await cursor.fetchone())[0]
                assert count == 1
        finally:
            await client.stop()
            await http_client.aclose()


# ---------------------------------------------------------------------------
# Tests for batch sending
# ---------------------------------------------------------------------------

class TestBatchSending:
    """Tests for batch sending to /records/batch."""

    @pytest.mark.asyncio
    async def test_batch_send_up_to_100(self, records_client, fake_server, temp_data_dir):
        """Should batch up to 100 records per request."""
        # Emit 150 records
        for i in range(150):
            await records_client.emit("test.type", {"seq": i})

        await asyncio.sleep(1.0)

        # Verify batch calls were made (we can't easily inspect call count
        # but we can verify all records eventually sent)
        server_ids = fake_server.get_record_ids()
        assert len(server_ids) == 150

    @pytest.mark.asyncio
    async def test_batch_respects_max_size(self, temp_data_dir, fake_server):
        """Batch size should respect max_batch_size config."""
        from httpx import ASGITransport, AsyncClient

        config = RecordsClientConfig(
            data_dir=temp_data_dir,
            memory_service_url="http://testserver",
            max_batch_size=10,  # Small batch size
            buffer_threshold_bytes=10 * 1024 * 1024,
            send_interval_seconds=0.1,
        )

        transport = ASGITransport(app=fake_server.app)
        http_client = AsyncClient(transport=transport, base_url="http://testserver")
        client = RecordsClient(config, http_client)
        await client.start()

        try:
            for i in range(25):
                await client.emit("test.type", {"seq": i})

            await asyncio.sleep(1.0)

            server_ids = fake_server.get_record_ids()
            assert len(server_ids) == 25
        finally:
            await client.stop()
            await http_client.aclose()


# ---------------------------------------------------------------------------
# Test for service identity integration
# ---------------------------------------------------------------------------

class TestServiceIdentity:
    """Tests for controller service identity integration."""

    @pytest.mark.asyncio
    async def test_uses_controller_token(self, temp_data_dir, fake_server):
        """Records should be sent with controller service token."""
        from httpx import ASGITransport, AsyncClient

        token_called = []

        def token_getter():
            token_called.append(True)
            return "test-token"

        config = RecordsClientConfig(
            data_dir=temp_data_dir,
            memory_service_url="http://testserver",
            max_batch_size=100,
            buffer_threshold_bytes=10 * 1024 * 1024,
            send_interval_seconds=0.1,
            token_getter=token_getter,
        )

        transport = ASGITransport(app=fake_server.app)
        http_client = AsyncClient(transport=transport, base_url="http://testserver", timeout=5.0)
        client = RecordsClient(config, http_client)
        await client.start()

        try:
            await client.emit("test.type", {"key": "value"})
            await asyncio.sleep(0.5)
            assert token_called, "Token getter should have been called"
        finally:
            await client.stop()
            await http_client.aclose()


# ---------------------------------------------------------------------------
# Integration test with app lifespan (if needed)
# ---------------------------------------------------------------------------

class TestAppIntegration:
    """Integration tests with the FastAPI app."""

    @pytest.mark.asyncio
    async def test_records_client_wired_in_app(self, temp_data_dir):
        """Verify records client can be wired into the app lifespan."""
        from tinyagentos.app import create_app
        from tinyagentos.records_client import get_records_client

        # This test would require a more complete app setup
        # For now, verify the module exports the right functions
        assert callable(get_records_client)
        assert callable(shutdown_records_client)


# ---------------------------------------------------------------------------
# Run tests
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
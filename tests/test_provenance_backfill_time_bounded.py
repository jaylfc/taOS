"""tsk-gjh34q — provenance backfill for pre-#2748 rows (time-based)

Tests the new time-bounded provenance backfill that stamps missing _server_raised
on pre-deploy decisions without re-stamping post-cutoff ones.
"""
import aiosqlite
import json
import pytest
import time
from pathlib import Path
from tinyagentos.decisions.decision_store import DecisionStore, SERVER_RAISED_KEY, GATE_GRANT_KINDS


async def _create_test_db(db_path):
    """Create a test database with initial schema."""
    async with aiosqlite.connect(db_path) as db:
        await db.executescript('''
            CREATE TABLE IF NOT EXISTS decisions (
                id TEXT PRIMARY KEY,
                from_agent TEXT NOT NULL,
                project_id TEXT,
                user_id TEXT NOT NULL DEFAULT '',
                question TEXT NOT NULL,
                type TEXT NOT NULL,
                options TEXT NOT NULL DEFAULT '[]',
                context TEXT NOT NULL DEFAULT '',
                priority TEXT NOT NULL DEFAULT 'normal',
                status TEXT NOT NULL DEFAULT 'pending',
                answer TEXT,
                created_at REAL NOT NULL,
                answered_at REAL,
                deadline REAL,
                checkpoint_ref TEXT,
                parent_decision_id TEXT,
                timeline_id TEXT,
                metadata TEXT NOT NULL DEFAULT '{}'
            );
        ''')


async def _insert_decision(db_path, decision_id, created_at, metadata=None, status='pending'):
    """Insert a decision with the given timestamp and metadata."""
    async with aiosqlite.connect(db_path) as db:
        meta_json = json.dumps(metadata or {})
        await db.execute(
            "INSERT INTO decisions (id, from_agent, user_id, question, type, status, created_at, metadata) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (decision_id, "test-agent", "test-user", "test question", "approve_deny",
             status, created_at, meta_json)
        )
        await db.commit()


async def _get_marker(db_path):
    """Get the current backfill marker value."""
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute("SELECT upgraded_at FROM gate_provenance_backfill WHERE key = 'server_raised'")
        marker = await cursor.fetchone()
        return marker[0] if marker else None


async def _has_server_raised(db_path, decision_id):
    """Check if a decision has the server_raised marker."""
    async with aiosqlite.connect(db_path) as db:
        cursor = await db.execute("SELECT metadata FROM decisions WHERE id = ?", (decision_id,))
        row = await cursor.fetchone()
        if not row:
            return False
        metadata = json.loads(row[0]) if row[0] else {}
        return metadata.get(SERVER_RAISED_KEY) is True


@pytest.mark.asyncio
async def test_time_bounded_backfill_with_marker(tmp_path):
    """Test that the backfill runs once and records a time marker."""
    db_path = tmp_path / "test.db"
    
    # Create test database with initial schema
    await _create_test_db(db_path)
    
    # Insert a gate decision BEFORE the marker (will be stamped)
    pre_cutoff_time = time.time() - 3600  # 1 hour ago
    await _insert_decision(
        db_path, "pre-decision", pre_cutoff_time,
        metadata={"kind": "execution_gate", "agent_name": "test-agent"}
    )
    
    # Insert a gate decision AFTER the marker (won't be stamped)
    post_cutoff_time = time.time() + 3600  # 1 hour from now
    await _insert_decision(
        db_path, "post-decision", post_cutoff_time,
        metadata={"kind": "execution_gate", "agent_name": "test-agent"}
    )
    
    # Create store and init (should run backfill)
    store = DecisionStore(db_path)
    await store.init()
    
    # Verify marker exists
    marker = await _get_marker(db_path)
    assert marker is not None, "Backfill marker should exist after first init"
    
    # Verify pre-cutoff decision was stamped
    assert await _has_server_raised(db_path, "pre-decision"), "Pre-cutoff decision should have server_raised"
    # Post-cutoff decision won't be stamped because we filter by created_at < marker
    assert not await _has_server_raised(db_path, "post-decision"), "Post-cutoff decision should NOT be stamped"
    
    await store.close()


@pytest.mark.asyncio
async def test_backfill_idempotent(tmp_path):
    """Test that running backfill twice changes zero rows (idempotency)."""
    db_path = tmp_path / "test_idempotent.db"
    
    # Create test database
    await _create_test_db(db_path)
    
    # Insert a gate decision BEFORE cutoff
    pre_cutoff_time = time.time() - 3600
    await _insert_decision(
        db_path, "decision", pre_cutoff_time,
        metadata={"kind": "execution_gate", "agent_name": "test-agent"}
    )
    
    # Get the marker from first run
    store1 = DecisionStore(db_path)
    await store1.init()
    marker1 = await _get_marker(db_path)
    assert marker1 is not None
    stamped_before = await _has_server_raised(db_path, "decision")
    assert stamped_before, "Decision should be stamped on first run"
    await store1.close()
    
    # Run backfill again (should be idempotent)
    store2 = DecisionStore(db_path)
    await store2.init()
    
    # Verify marker is unchanged
    marker2 = await _get_marker(db_path)
    assert marker2 == marker1, "Marker should be unchanged on second run"
    
    # Verify decision status is unchanged (no double-stamping)
    stamped_after = await _has_server_raised(db_path, "decision")
    assert stamped_after == stamped_before, "Decision should not change on second run"
    
    await store2.close()


@pytest.mark.asyncio
async def test_backfill_stamps_only_created_before_cutoff(tmp_path):
    """Test that the backfill ONLY stamps decisions created before the cutoff time."""
    db_path = tmp_path / "test_cutoff.db"
    
    # Create test database
    await _create_test_db(db_path)
    
    # Insert decisions at different times relative to the marker
    base_time = time.time()
    
    # Decision 1: created 2 hours BEFORE current time (will be stamped)
    await _insert_decision(
        db_path, "decision-early", base_time - 7200,
        metadata={"kind": "execution_gate", "agent_name": "test-agent"}
    )
    
    # Decision 2: created 1 hour BEFORE current time (will be stamped)
    await _insert_decision(
        db_path, "decision-mid", base_time - 3600,
        metadata={"kind": "execution_gate", "agent_name": "test-agent"}
    )
    
    # Decision 3: created 30 minutes AFTER current time (won't be stamped)
    await _insert_decision(
        db_path, "decision-late", base_time + 1800,
        metadata={"kind": "execution_gate", "agent_name": "test-agent"}
    )
    
    # Decision 4: created 5 hours BEFORE current time (will be stamped)
    await _insert_decision(
        db_path, "decision-very-early", base_time - 18000,
        metadata={"kind": "execution_gate", "agent_name": "test-agent"}
    )
    
    # Create store and init (should run backfill)
    store = DecisionStore(db_path)
    await store.init()
    
    # Verify which decisions were stamped
    stamped_early = await _has_server_raised(db_path, "decision-early")
    stamped_mid = await _has_server_raised(db_path, "decision-mid")
    stamped_late = await _has_server_raised(db_path, "decision-late")
    stamped_very_early = await _has_server_raised(db_path, "decision-very-early")
    
    # Only early, mid, and very early should be stamped (created before marker)
    assert stamped_early, "Decision created 2 hours ago should be stamped"
    assert stamped_mid, "Decision created 1 hour ago should be stamped"
    assert not stamped_late, "Decision created 30 minutes from now should NOT be stamped"
    assert stamped_very_early, "Decision created 5 hours ago should be stamped"
    
    await store.close()


@pytest.mark.asyncio
async def test_backfill_proof_two_runs_change_zero(tmp_path):
    """RED-FIRST test: running backfill twice changes zero rows the second time."""
    db_path = tmp_path / "test_proof.db"
    
    # Create test database
    await _create_test_db(db_path)
    
    # Insert multiple decisions before cutoff
    base_time = time.time() - 3600
    for i in range(3):
        await _insert_decision(
            db_path, f"decision-{i}", base_time - i,
            metadata={"kind": "execution_gate", "agent_name": "test-agent"}
        )
    
    # First run - should stamp 3 decisions
    store1 = DecisionStore(db_path)
    await store1.init()
    
    # Check which decisions got stamped
    stamped_first_run = []
    for i in range(3):
        if await _has_server_raised(db_path, f"decision-{i}"):
            stamped_first_run.append(f"decision-{i}")
    
    print(f"Decisions stamped on first run: {stamped_first_run}")
    assert len(stamped_first_run) == 3, "All 3 pre-cutoff decisions should be stamped"
    
    # Store marker from first run
    marker1 = await _get_marker(db_path)
    
    # Second run - should stamp 0 decisions
    store2 = DecisionStore(db_path)
    await store2.init()
    
    # Check which new decisions got stamped
    newly_stamped = []
    for i in range(3):
        if await _has_server_raised(db_path, f"decision-{i}") and f"decision-{i}" not in stamped_first_run:
            newly_stamped.append(f"decision-{i}")
    
    print(f"Decisions newly stamped on second run: {newly_stamped}")
    assert len(newly_stamped) == 0, "No decisions should be newly stamped on second run (idempotency)"
    
    # Verify marker unchanged
    marker2 = await _get_marker(db_path)
    assert marker2 == marker1, "Marker should not change between runs"
    
    await store1.close()
    await store2.close()
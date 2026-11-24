"""Tests for POST /api/cluster/tasks/report (taOS #3233).

Auth is via _require_lease_access (shared with lease endpoints, tested in
test_leases.py). These tests mock it to focus on the report logic.
"""
from __future__ import annotations

import json
import time
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from tinyagentos.routes.cluster import TaskReportRequest
from tinyagentos.trace_store import TraceStoreRegistry, VALID_KINDS, TraceWriteError


@pytest.mark.asyncio
async def test_task_report_kind_registered():
    assert "task_report" in VALID_KINDS


async def _allow_all(request):
    return None


async def _allow_principal(request):
    return "admin"


def _mock_auth():
    class _Ctx:
        def __enter__(self):
            self.stack = ExitStack()
            self.stack.enter_context(patch(
                "tinyagentos.routes.cluster._require_lease_access", side_effect=_allow_all
            ))
            self.stack.enter_context(patch(
                "tinyagentos.routes.cluster._identify_principal", side_effect=_allow_principal
            ))
            return self

        def __exit__(self, *exc):
            return self.stack.__exit__(*exc)

    return _Ctx()


# ---------------------------------------------------------------------------
# Tests using the real client fixture (with admin auth via _mock_auth)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_report_task_records_trace(client, app, tmp_data_dir):
    # Set up trace registry manually (lifespan not run in tests)
    app.state.trace_registry = TraceStoreRegistry(tmp_data_dir)

    # Use admin auth via local token if available, else patch _require_lease_access
    from unittest.mock import patch
    from fastapi.responses import JSONResponse

    async def allow_all(request):
        return None

    with patch(
        "tinyagentos.routes.cluster._require_lease_access", side_effect=allow_all
    ):
        resp = await client.post(
            "/api/cluster/tasks/report",
            json={
                "worker": "w1",
                "resource_id": "gpu-cuda-0",
                "lease_id": "l_abc123",
                "caller": "ext-scheduler",
                "model": "llama3:8b",
                "duration_s": 12.5,
                "status": "success",
                "tokens": {"in": 100, "out": 250},
            },
        )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "recorded"
    assert data["worker"] == "w1"
    assert data["resource_id"] == "gpu-cuda-0"
    assert "id" in data

    # Verify it landed in the trace store
    store = await app.state.trace_registry.get("taos-cluster-tasks")
    events = await store.list(kind="task_report", limit=10)
    assert len(events) == 1
    env = events[0]
    assert env["kind"] == "task_report"
    assert env["model"] == "llama3:8b"
    assert env["duration_ms"] == 12500
    payload = env["payload"]
    assert payload["worker"] == "w1"
    assert payload["resource_id"] == "gpu-cuda-0"
    assert payload["lease_ref"] == "baa03db3f3f84c0b"
    assert payload["caller"] == "ext-scheduler"
    assert payload["status"] == "success"
    assert payload["tokens"] == {"in": 100, "out": 250}

    await app.state.trace_registry.close_all()


@pytest.mark.asyncio
async def test_report_task_failure_status(client, app, tmp_data_dir):
    app.state.trace_registry = TraceStoreRegistry(tmp_data_dir)
    with _mock_auth():
        resp = await client.post(
            "/api/cluster/tasks/report",
            json={
                "worker": "w2",
                "resource_id": "gpu-cuda-0",
                "caller": "ext-scheduler",
                "model": "llama3",
                "duration_s": 3.0,
                "status": "failure",
            },
        )
    assert resp.status_code == 200
    store = await app.state.trace_registry.get("taos-cluster-tasks")
    events = await store.list(kind="task_report", limit=10)
    assert len(events) == 1
    assert events[0]["error"] == "failure"
    await app.state.trace_registry.close_all()


@pytest.mark.asyncio
async def test_report_task_validation(client, app, tmp_data_dir):
    app.state.trace_registry = TraceStoreRegistry(tmp_data_dir)
    with _mock_auth():
        # Invalid status
        resp = await client.post(
            "/api/cluster/tasks/report",
            json={
                "worker": "w1",
                "resource_id": "gpu-cuda-0",
                "caller": "x",
                "duration_s": 1.0,
                "status": "bogus",
            },
        )
        assert resp.status_code == 422

        # Negative duration
        resp = await client.post(
            "/api/cluster/tasks/report",
            json={
                "worker": "w1",
                "resource_id": "gpu-cuda-0",
                "caller": "x",
                "duration_s": -1.0,
                "status": "success",
            },
        )
        assert resp.status_code == 422

    await app.state.trace_registry.close_all()


@pytest.mark.asyncio
async def test_report_task_no_registry(client, app):
    # trace_registry is None -> 503
    app.state.trace_registry = None
    with _mock_auth():
        resp = await client.post(
            "/api/cluster/tasks/report",
            json={
                "worker": "w1",
                "resource_id": "gpu-cuda-0",
                "caller": "x",
                "duration_s": 1.0,
                "status": "success",
            },
        )
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_report_task_strict_write_failure_returns_503(client, app, tmp_data_dir):
    """Both sinks fail with strict=True -> 503, no 'recorded' in response."""
    app.state.trace_registry = TraceStoreRegistry(tmp_data_dir)
    store = await app.state.trace_registry.get("taos-cluster-tasks")

    # Force both sinks to fail
    async def _fail_insert(*args, **kwargs):
        raise RuntimeError("sqlite failed")

    def _fail_fallback(*args, **kwargs):
        return False

    with patch.object(store, "_insert_envelope", side_effect=_fail_insert), \
         patch.object(store, "_append_fallback", side_effect=_fail_fallback), \
         patch("tinyagentos.routes.cluster._require_lease_access", side_effect=_allow_all), \
         patch("tinyagentos.routes.cluster._identify_principal", side_effect=_allow_principal):
        resp = await client.post(
            "/api/cluster/tasks/report",
            json={
                "worker": "w1",
                "resource_id": "gpu-cuda-0",
                "caller": "ext-scheduler",
                "duration_s": 1.0,
                "status": "success",
            },
        )
    assert resp.status_code == 503, resp.text
    data = resp.json()
    assert data["error"] == "trace write failed"
    assert "recorded" not in str(data)

    await app.state.trace_registry.close_all()


@pytest.mark.asyncio
async def test_report_task_worker_hmac_binding(client, app, tmp_data_dir):
    """Worker HMAC caller must report its own name; mismatch -> 403."""
    app.state.trace_registry = TraceStoreRegistry(tmp_data_dir)

    # Mock _identify_principal to return "worker" and set hmac_worker_name on request.state
    async def mock_identify_worker(request):
        request.state.hmac_worker_name = "worker-actual"
        return "worker"

    with patch("tinyagentos.routes.cluster._identify_principal", side_effect=mock_identify_worker), \
         patch("tinyagentos.routes.cluster._require_lease_access", side_effect=_allow_all):
        # Mismatch -> 403
        resp = await client.post(
            "/api/cluster/tasks/report",
            json={
                "worker": "worker-other",
                "resource_id": "gpu-cuda-0",
                "caller": "ext-scheduler",
                "duration_s": 1.0,
                "status": "success",
            },
        )
        assert resp.status_code == 403, resp.text
        assert resp.json()["error"] == "worker mismatch"

        # Match -> 200
        resp = await client.post(
            "/api/cluster/tasks/report",
            json={
                "worker": "worker-actual",
                "resource_id": "gpu-cuda-0",
                "caller": "ext-scheduler",
                "duration_s": 1.0,
                "status": "success",
            },
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "recorded"

    await app.state.trace_registry.close_all()


@pytest.mark.asyncio
async def test_report_task_tokens_validation(client, app, tmp_data_dir):
    """tokens dict validation: 17 keys, non-int value, negative value -> 422."""
    app.state.trace_registry = TraceStoreRegistry(tmp_data_dir)

    with patch("tinyagentos.routes.cluster._require_lease_access", side_effect=_allow_all):
        # 17 keys -> 422
        resp = await client.post(
            "/api/cluster/tasks/report",
            json={
                "worker": "w1",
                "resource_id": "gpu-cuda-0",
                "caller": "ext-scheduler",
                "duration_s": 1.0,
                "status": "success",
                "tokens": {f"k{i}": i for i in range(17)},
            },
        )
        assert resp.status_code == 422, resp.text

        # Non-int value -> 422
        resp = await client.post(
            "/api/cluster/tasks/report",
            json={
                "worker": "w1",
                "resource_id": "gpu-cuda-0",
                "caller": "ext-scheduler",
                "duration_s": 1.0,
                "status": "success",
                "tokens": {"in": "not-an-int"},
            },
        )
        assert resp.status_code == 422, resp.text

        # Negative value -> 422
        resp = await client.post(
            "/api/cluster/tasks/report",
            json={
                "worker": "w1",
                "resource_id": "gpu-cuda-0",
                "caller": "ext-scheduler",
                "duration_s": 1.0,
                "status": "success",
                "tokens": {"out": -1},
            },
        )
        assert resp.status_code == 422, resp.text

        # Valid tokens (16 keys, int values 0..1e9) -> 200
        resp = await client.post(
            "/api/cluster/tasks/report",
            json={
                "worker": "w1",
                "resource_id": "gpu-cuda-0",
                "caller": "ext-scheduler",
                "duration_s": 1.0,
                "status": "success",
                "tokens": {f"k{i}": i for i in range(16)},
            },
        )
        assert resp.status_code == 200, resp.text

    await app.state.trace_registry.close_all()


@pytest.mark.asyncio
async def test_trace_store_record_non_strict_returns_envelope_on_failure(tmp_data_dir):
    """record() WITHOUT strict still returns envelope when both sinks fail."""
    from tinyagentos.trace_store import AgentTraceStore
    store = AgentTraceStore(tmp_data_dir, "test-agent")

    # Force both sinks to fail
    async def _fail_insert(*args, **kwargs):
        raise RuntimeError("sqlite failed")

    def _fail_fallback(*args, **kwargs):
        return False

    with patch.object(store, "_insert_envelope", side_effect=_fail_insert), \
         patch.object(store, "_append_fallback", side_effect=_fail_fallback):
        # Default strict=False should still return envelope
        env = await store.record("message_in", payload={"from": "user", "text": "hello"})
        assert env["kind"] == "message_in"
        assert env["payload"]["text"] == "hello"
        assert "id" in env
        assert "created_at" in env


@pytest.mark.asyncio
async def test_trace_store_record_sealed_bucket_strict_raises(tmp_data_dir):
    """record() with strict=True on sealed bucket raises TraceWriteError when _append_late fails."""
    from tinyagentos.trace_store import AgentTraceStore
    store = AgentTraceStore(tmp_data_dir, "test-agent")

    with patch.object(store, "_is_sealed_bucket", return_value=True), \
         patch.object(store, "_append_late", return_value=False):
        # strict=True should raise TraceWriteError
        with pytest.raises(TraceWriteError, match="late sidecar write failed"):
            await store.record("message_in", strict=True, payload={"from": "user", "text": "hello"})

        # strict=False should still return envelope (current behavior unchanged)
        env = await store.record("message_in", strict=False, payload={"from": "user", "text": "hello"})
        assert env["kind"] == "message_in"
        assert env["payload"]["text"] == "hello"
        assert "id" in env
        assert "created_at" in env


@pytest.mark.asyncio
async def test_trace_store_record_rollback_on_commit_failure(tmp_data_dir):
    """Failed commit triggers rollback before JSONL fallback."""
    from tinyagentos.trace_store import AgentTraceStore
    store = AgentTraceStore(tmp_data_dir, "test-agent")

    # Track if rollback was called
    rollback_called = False
    original_open_bucket = store._open_bucket

    async def mock_open_bucket(bucket):
        conn = await original_open_bucket(bucket)
        original_commit = conn.commit
        original_rollback = conn.rollback

        async def failing_commit():
            raise RuntimeError("commit failed")

        async def tracked_rollback():
            nonlocal rollback_called
            rollback_called = True
            await original_rollback()

        conn.commit = failing_commit
        conn.rollback = tracked_rollback
        return conn

    def fail_fallback(*args, **kwargs):
        return False

    with patch.object(store, "_open_bucket", side_effect=mock_open_bucket), \
         patch.object(store, "_append_fallback", side_effect=fail_fallback):
        # strict=True should raise TraceWriteError after rollback
        with pytest.raises(TraceWriteError, match="both SQLite and JSONL writes failed"):
            await store.record("message_in", strict=True, payload={"from": "user", "text": "hello"})

    assert rollback_called, "rollback should have been called on commit failure"


@pytest.mark.asyncio
async def test_report_task_tokens_strict_int_rejects_strings(client, app, tmp_data_dir):
    """tokens with string numeric values like '5' should be 422, not coerced."""
    app.state.trace_registry = TraceStoreRegistry(tmp_data_dir)

    with patch("tinyagentos.routes.cluster._require_lease_access", side_effect=_allow_all):
        # String "5" should be rejected (StrictInt)
        resp = await client.post(
            "/api/cluster/tasks/report",
            json={
                "worker": "w1",
                "resource_id": "gpu-cuda-0",
                "caller": "ext-scheduler",
                "duration_s": 1.0,
                "status": "success",
                "tokens": {"in": "5"},
            },
        )
        assert resp.status_code == 422, resp.text

    await app.state.trace_registry.close_all()


@pytest.mark.asyncio
async def test_report_task_duration_s_rejects_nan_inf():
    """duration_s with nan/inf should raise ValidationError when model constructed directly."""
    # JSON cannot represent nan/inf, so test model directly
    with pytest.raises(ValidationError):
        TaskReportRequest(
            worker="w1",
            resource_id="gpu-cuda-0",
            caller="ext-scheduler",
            duration_s=float("nan"),
            status="success",
        )

    with pytest.raises(ValidationError):
        TaskReportRequest(
            worker="w1",
            resource_id="gpu-cuda-0",
            caller="ext-scheduler",
            duration_s=float("inf"),
            status="success",
        )

    with pytest.raises(ValidationError):
        TaskReportRequest(
            worker="w1",
            resource_id="gpu-cuda-0",
            caller="ext-scheduler",
            duration_s=float("-inf"),
            status="success",
        )

    # Valid finite values should work
    req = TaskReportRequest(
        worker="w1",
        resource_id="gpu-cuda-0",
        caller="ext-scheduler",
        duration_s=1.5,
        status="success",
    )
    assert req.duration_s == 1.5


@pytest.mark.asyncio
async def test_report_task_kind_refused_from_trace_write(client, app, tmp_data_dir):
    """POST /api/trace with kind='task_report' must return 400 (not accepted from client)."""
    app.state.trace_registry = TraceStoreRegistry(tmp_data_dir)
    
    resp = await client.post(
        "/api/trace",
        json={
            "agent_name": "taos-cluster-tasks",
            "kind": "task_report",
            "payload": {
                "worker": "w1",
                "resource_id": "gpu-cuda-0",
                "caller": "ext-scheduler",
                "duration_s": 1.0,
                "status": "success",
            },
        },
    )
    assert resp.status_code == 400, f"Expected 400 for kind=\"task_report\", but got {resp.status_code}: {resp.text}"
    assert resp.json()["error"] == "kind 'task_report' is written by taOS only"
    
    # Verify nothing was written to the trace store
    store = await app.state.trace_registry.get("taos-cluster-tasks")
    events = await store.list(kind="task_report", limit=10)
    assert len(events) == 0, "No task_report rows should be written via POST /api/trace"


@pytest.mark.asyncio
async def test_report_task_unidentified_caller_returns_403(client, app, tmp_data_dir):
    """report_task with unidentified caller must return 403, no row written."""
    app.state.trace_registry = TraceStoreRegistry(tmp_data_dir)
    
    # Mock _identify_principal to return None (unidentified caller)
    # but _require_lease_access should still allow the call (return None)
    # because report_task needs to do its own check
    from unittest.mock import patch
    
    async def mock_identify_principal_none(request):
        return None
    
    async def mock_require_lease_access_none(request):
        # This mock allows the call (returns None) even if _identify_principal returns None
        # because report_task needs to do its own check
        return None
    
    with patch("tinyagentos.routes.cluster._identify_principal", side_effect=mock_identify_principal_none), \
         patch("tinyagentos.routes.cluster._require_lease_access", side_effect=mock_require_lease_access_none):
        resp = await client.post(
            "/api/cluster/tasks/report",
            json={
                "worker": "w1",
                "resource_id": "gpu-cuda-0",
                "caller": "ext-scheduler",
                "duration_s": 1.0,
                "status": "success",
            },
        )
    
    assert resp.status_code == 403, f"Expected 403 for unidentified caller, but got {resp.status_code}: {resp.text}"
    assert resp.json()["error"] == "unidentified caller"
    
    # Verify no row was written
    store = await app.state.trace_registry.get("taos-cluster-tasks")
    events = await store.list(kind="task_report", limit=10)
    assert len(events) == 0, "No task_report rows should be written for unidentified caller"


@pytest.mark.asyncio
async def test_report_task_lease_id_not_stored(client, app, tmp_data_dir):
    """POST /api/cluster/tasks/report must store lease_ref (hash) not raw lease_id."""
    app.state.trace_registry = TraceStoreRegistry(tmp_data_dir)
    
    with _mock_auth():
        resp = await client.post(
            "/api/cluster/tasks/report",
            json={
                "worker": "w1",
                "resource_id": "gpu-cuda-0",
                "lease_id": "l_abc123",
                "caller": "ext-scheduler",
                "duration_s": 1.0,
                "status": "success",
            },
        )
    
    assert resp.status_code == 200, resp.text
    store = await app.state.trace_registry.get("taos-cluster-tasks")
    events = await store.list(kind="task_report", limit=10)
    assert len(events) == 1
    env = events[0]
    payload = env["payload"]
    
    # Verify lease_ref exists (hashed lease_id, first 16 hex chars of sha256)
    assert "lease_ref" in payload, f"Expected lease_ref in payload, got {list(payload.keys())}"
    assert "lease_id" not in payload, f"Raw lease_id should not be stored, got lease_id: {payload.get('lease_id')}"
    
    # Verify lease_ref is correct hash (first 16 hex chars of sha256('l_abc123'))
    import hashlib
    expected_lease_ref = hashlib.sha256("l_abc123".encode()).hexdigest()[:16]
    assert payload["lease_ref"] == expected_lease_ref, f"Expected lease_ref {expected_lease_ref}, got {payload['lease_ref']}"
    
    await app.state.trace_registry.close_all()


@pytest.mark.asyncio
async def test_report_task_blank_worker_422(client, app, tmp_data_dir):
    app.state.trace_registry = TraceStoreRegistry(tmp_data_dir)
    with _mock_auth():
        resp = await client.post(
            "/api/cluster/tasks/report",
            json={
                "worker": " ",
                "resource_id": "gpu-cuda-0",
                "caller": "ext-scheduler",
                "duration_s": 1.0,
                "status": "success",
            },
        )
    assert resp.status_code == 422, resp.text
    store = await app.state.trace_registry.get("taos-cluster-tasks")
    events = await store.list(kind="task_report", limit=10)
    assert len(events) == 0
    await app.state.trace_registry.close_all()


@pytest.mark.asyncio
async def test_report_task_blank_resource_id_422(client, app, tmp_data_dir):
    app.state.trace_registry = TraceStoreRegistry(tmp_data_dir)
    with _mock_auth():
        resp = await client.post(
            "/api/cluster/tasks/report",
            json={
                "worker": "w1",
                "resource_id": " ",
                "caller": "ext-scheduler",
                "duration_s": 1.0,
                "status": "success",
            },
        )
    assert resp.status_code == 422, resp.text
    await app.state.trace_registry.close_all()


@pytest.mark.asyncio
async def test_report_task_refuses_unauthenticated(app, tmp_data_dir):
    """Unauthenticated request (no cookie, no bearer, no HMAC headers) -> 403."""
    app.state.trace_registry = TraceStoreRegistry(tmp_data_dir)

    from httpx import ASGITransport, AsyncClient
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as anon_client:
        resp = await anon_client.post(
            "/api/cluster/tasks/report",
            json={
                "worker": "w1",
                "resource_id": "gpu-cuda-0",
                "caller": "ext-scheduler",
                "duration_s": 1.0,
                "status": "success",
            },
        )
    assert resp.status_code == 403
    assert resp.json()["error"] == "unidentified caller"

    await app.state.trace_registry.close_all()


@pytest.mark.asyncio
async def test_report_task_refuses_invalid_bearer(app, tmp_data_dir):
    """Request with invalid Bearer token -> 403."""
    app.state.trace_registry = TraceStoreRegistry(tmp_data_dir)

    from httpx import ASGITransport, AsyncClient
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as anon_client:
        resp = await anon_client.post(
            "/api/cluster/tasks/report",
            headers={"Authorization": "Bearer invalid-token"},
            json={
                "worker": "w1",
                "resource_id": "gpu-cuda-0",
                "caller": "ext-scheduler",
                "duration_s": 1.0,
                "status": "success",
            },
        )
    assert resp.status_code == 403
    assert resp.json()["error"] == "unidentified caller"

    await app.state.trace_registry.close_all()


@pytest.mark.asyncio
async def test_report_task_admin_session_works(client, app, tmp_data_dir):
    """Admin session cookie works through the exempt path -> 200."""
    app.state.trace_registry = TraceStoreRegistry(tmp_data_dir)

    with patch("tinyagentos.routes.cluster._require_lease_access", side_effect=_allow_all), \
         patch("tinyagentos.routes.cluster._identify_principal", side_effect=_allow_principal):
        resp = await client.post(
            "/api/cluster/tasks/report",
            json={
                "worker": "w1",
                "resource_id": "gpu-cuda-0",
                "caller": "ext-scheduler",
                "duration_s": 1.0,
                "status": "success",
            },
        )
    assert resp.status_code == 200
    assert resp.json()["status"] == "recorded"

    await app.state.trace_registry.close_all()


# ---------------------------------------------------------------------------
# HMAC verification tests (exercise the real worker HMAC path)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_report_task_bad_signature_403(client, app, tmp_data_dir):
    """Worker HMAC with wrong signature must return 403.

    This test exercises the real HMAC verification path:
    - No patching of _identify_principal or _require_lease_access
    - cluster_pairing is configured with a known signing key for worker w1
    - Request carries x-taos-worker-name, x-taos-timestamp, and a WRONG x-taos-signature
    - No admin or local-token credentials are sent
    - Assert 403 is returned with code bad_signature
    """
    app.state.trace_registry = TraceStoreRegistry(tmp_data_dir)

    # Configure cluster_pairing with a known signing key for worker w1
    signing_key = b"\x42" * 32
    pairing_store = AsyncMock()
    pairing_store.get_signing_key.return_value = signing_key
    app.state.cluster_pairing = pairing_store

    # Wrap require_worker_hmac to capture the _HMACError
    from tinyagentos.cluster.worker_auth import require_worker_hmac as real_require_worker_hmac
    from tinyagentos.cluster.worker_auth import _HMACError

    captured_errors = []

    async def capturing_require_worker_hmac(request):
        try:
            await real_require_worker_hmac(request)
        except _HMACError as e:
            captured_errors.append(e)
            raise

    # Prepare request body and WRONG signature
    body = json.dumps({
        "worker": "w1",
        "resource_id": "gpu-cuda-0",
        "caller": "ext-scheduler",
        "duration_s": 1.0,
        "status": "success",
    }).encode()
    wrong_sig = "deadbeef"  # deliberately wrong
    headers = {
        "X-TAOS-Worker-Name": "w1",
        "X-TAOS-Timestamp": str(int(time.time())),
        "X-TAOS-Signature": wrong_sig,
        "Content-Type": "application/json",
    }

    # Use a client WITHOUT session cookie (the fixture client has one)
    from httpx import ASGITransport, AsyncClient
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as hm_client:
        with patch("tinyagentos.routes.cluster.require_worker_hmac", side_effect=capturing_require_worker_hmac):
            resp = await hm_client.post(
                "/api/cluster/tasks/report",
                content=body,
                headers=headers,
            )

    assert resp.status_code == 403, f"Expected 403 for bad signature, got {resp.status_code}: {resp.text}"
    assert len(captured_errors) == 1, f"Expected exactly one captured error, got {len(captured_errors)}"
    err = captured_errors[0]
    error_body = json.loads(err.response.body)
    assert error_body["code"] == "bad_signature", f"Expected bad_signature code, got {error_body}"

    await app.state.trace_registry.close_all()


@pytest.mark.asyncio
async def test_report_task_valid_worker_signature_verifies_once(client, app, tmp_data_dir, worker_request_signer):
    """Valid worker HMAC must verify once and allow the request.

    Same setup as the bad-signature test but with a CORRECT signature.
    Wraps require_worker_hmac (patched where cluster.py looks it up, with
    wraps= the real function) and asserts the wrapper was called exactly once.
    Response must be 200 with status == "recorded".
    """
    app.state.trace_registry = TraceStoreRegistry(tmp_data_dir)

    # Configure cluster_pairing with a known signing key for worker w1
    signing_key = b"\x42" * 32
    pairing_store = AsyncMock()
    pairing_store.get_signing_key.return_value = signing_key
    app.state.cluster_pairing = pairing_store

    # Wrap require_worker_hmac to count calls (patch where cluster.py imports it)
    from tinyagentos.cluster.worker_auth import require_worker_hmac as real_require_worker_hmac

    # Prepare request body and CORRECT signature
    body = json.dumps({
        "worker": "w1",
        "resource_id": "gpu-cuda-0",
        "caller": "ext-scheduler",
        "duration_s": 1.0,
        "status": "success",
    }).encode()
    headers = worker_request_signer(signing_key, "w1", "POST", "/api/cluster/tasks/report", body)
    headers["Content-Type"] = "application/json"

    # Use a client WITHOUT session cookie
    from httpx import ASGITransport, AsyncClient
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as hm_client:
        with patch("tinyagentos.routes.cluster.require_worker_hmac", wraps=real_require_worker_hmac) as mock_require:
            resp = await hm_client.post(
                "/api/cluster/tasks/report",
                content=body,
                headers=headers,
            )

    # Response must be 200 with status == "recorded"
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    assert resp.json()["status"] == "recorded"
    # require_worker_hmac must have been called exactly once
    assert mock_require.call_count == 1, f"require_worker_hmac called {mock_require.call_count} times, expected 1"

    await app.state.trace_registry.close_all()

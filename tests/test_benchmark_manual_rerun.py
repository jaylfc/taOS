"""Worker benchmark lifecycle: first attach, append-only re-runs, manual re-run (#125).

Two layers are pinned here:

1. **Recording** — a simulated first attach records rows keyed by worker id +
   ``measured_at``, and a later run *appends*: the first-join baseline is never
   overwritten or replaced.
2. **The manual re-run trigger** — ``POST /api/workers/{id}/benchmark`` queues a
   run (409 while one is already queued, ``force`` replaces it), the queue is
   visible on the read API, and the heartbeat response hands the queued run to
   the worker. Posting results clears the queue entry.

The second layer is red on the base commit (the trigger endpoint does not
exist there); the first layer is a regression pin for behaviour that shipped
with the first-attach hook.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json as _json
import time

import pytest
import pytest_asyncio

from tinyagentos.benchmark.store import BenchmarkStore

WORKER = "pi4"


def _sign(key: bytes, name: str, path: str, body: bytes) -> dict:
    """HMAC headers for a worker request.

    Local copy of the conftest helper on purpose: ``tests/`` is not a package
    and a bare ``from conftest import ...`` is banned
    (tests/test_no_bare_conftest_import.py).
    """
    ts = str(int(time.time()))
    message = f"{ts}.POST.{path}.{hashlib.sha256(body).hexdigest()}".encode()
    return {
        "X-TAOS-Worker-Name": name,
        "X-TAOS-Timestamp": ts,
        "X-TAOS-Signature": hmac.new(key, message, hashlib.sha256).hexdigest(),
    }


def _report(
    *,
    worker_id: str = WORKER,
    first_join: bool = False,
    value: float = 42.0,
    measured_at: float = 1_700_000_000.0,
    metric: str = "tokens_per_sec",
    request_id: float | None = None,
) -> dict:
    return {
        "worker_id": worker_id,
        "worker_name": worker_id,
        "platform": "linux-aarch64",
        "suite_name": "short",
        "first_join": first_join,
        "request_id": request_id,
        "results": [
            {
                "task_id": "chat-small",
                "capability": "llm-chat",
                "model": "qwen3-1.7b-q4",
                "metric": metric,
                "value": value,
                "unit": "tok/s",
                "status": "ok",
                "elapsed_seconds": 1.5,
                "error": None,
                "measured_at": measured_at,
                "details": {},
            }
        ],
    }


@pytest_asyncio.fixture
async def bench_store(app, tmp_path):
    """A real (initialised) benchmark store wired into the app, as production has."""
    store = BenchmarkStore(tmp_path / "benchmarks.db")
    await store.init()
    app.state.benchmark_store = store
    yield store
    await store.close()


@pytest.mark.asyncio
async def test_first_attach_records_row_keyed_by_worker_and_timestamp(bench_store, client):
    measured_at = 1_700_000_000.0

    resp = await client.post(
        f"/api/workers/{WORKER}/benchmark/results",
        json=_report(first_join=True, value=42.0, measured_at=measured_at),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"worker_id": WORKER, "recorded": 1, "first_join": True}

    rows = await bench_store.history_by_worker(WORKER)
    assert len(rows) == 1
    row = rows[0]
    assert row["worker_id"] == WORKER
    assert row["measured_at"] == measured_at
    assert row["value"] == 42.0
    assert row["first_join"] is True
    assert await bench_store.has_first_join_run(WORKER) is True
    # A different worker has no baseline yet -- the marker is per worker.
    assert await bench_store.has_first_join_run("other-worker") is False


@pytest.mark.asyncio
async def test_manual_rerun_appends_and_never_clobbers_the_first_recording(bench_store, client):
    first = 1_700_000_000.0
    second = first + 3600.0

    await client.post(
        f"/api/workers/{WORKER}/benchmark/results",
        json=_report(first_join=True, value=42.0, measured_at=first),
    )
    # The re-run arrives claiming first_join=True (a worker that does not know
    # its baseline was recorded). The controller coerces it, and either way the
    # original row must survive.
    rerun = await client.post(
        f"/api/workers/{WORKER}/benchmark/results",
        json=_report(first_join=True, value=55.0, measured_at=second),
    )
    assert rerun.status_code == 200, rerun.text
    assert rerun.json()["first_join"] is False

    rows = await bench_store.history_by_worker(WORKER)
    assert sorted(r["value"] for r in rows) == [42.0, 55.0]
    first_join_rows = [r for r in rows if r["first_join"]]
    assert len(first_join_rows) == 1
    assert first_join_rows[0]["value"] == 42.0
    assert first_join_rows[0]["measured_at"] == first

    body = (await client.get(f"/api/workers/{WORKER}/benchmark")).json()
    assert len(body["history"]) == 2
    assert [r["value"] for r in body["latest"]] == [55.0]


@pytest.mark.asyncio
async def test_manual_trigger_queues_a_run_and_guards_the_queue(
    bench_store, client, app, pair_and_register_worker
):
    reg = await pair_and_register_worker(
        client, app, {"name": WORKER, "url": "http://10.0.0.9:9000", "platform": "linux"}
    )
    assert reg.status_code == 200, reg.text

    queued = await client.post(f"/api/workers/{WORKER}/benchmark", json={})
    assert queued.status_code == 202, queued.text
    body = queued.json()
    assert body["status"] == "queued"
    assert body["worker_id"] == WORKER
    assert body["force"] is False
    assert body["requested_at"] > 0

    # The read API shows the queued run so the UI can say "waiting for heartbeat".
    read = (await client.get(f"/api/workers/{WORKER}/benchmark")).json()
    assert read["pending"]["worker_id"] == WORKER
    assert read["pending"]["requested_at"] == body["requested_at"]

    # A second click while one is queued is refused unless forced.
    again = await client.post(f"/api/workers/{WORKER}/benchmark", json={})
    assert again.status_code == 409
    assert "already queued" in again.json()["error"]

    forced = await client.post(f"/api/workers/{WORKER}/benchmark", json={"force": True})
    assert forced.status_code == 202
    assert forced.json()["force"] is True
    assert forced.json()["requested_at"] >= body["requested_at"]


@pytest.mark.asyncio
async def test_manual_trigger_unknown_worker_is_404(bench_store, client):
    resp = await client.post("/api/workers/ghost/benchmark", json={})
    assert resp.status_code == 404
    assert "not found" in resp.json()["error"]
    assert await bench_store.get_pending_request("ghost") is None


@pytest.mark.asyncio
async def test_manual_trigger_offline_worker_is_409(
    bench_store, client, app, pair_and_register_worker
):
    await pair_and_register_worker(
        client, app, {"name": WORKER, "url": "http://10.0.0.9:9000", "platform": "linux"}
    )
    app.state.cluster_manager.get_worker(WORKER).status = "offline"

    resp = await client.post(f"/api/workers/{WORKER}/benchmark", json={})
    assert resp.status_code == 409
    assert "not online" in resp.json()["error"]
    assert await bench_store.get_pending_request(WORKER) is None


@pytest.mark.asyncio
async def test_manual_trigger_without_store_is_503(client, app):
    app.state.benchmark_store = None

    resp = await client.post(f"/api/workers/{WORKER}/benchmark", json={})
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_manual_trigger_requires_an_admin_session(app):
    """The trigger occupies a worker, so an anonymous caller cannot reach it."""
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as anon:
        resp = await anon.post(f"/api/workers/{WORKER}/benchmark", json={})
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_queued_run_is_delivered_on_heartbeat_and_cleared_by_results(
    bench_store, client, app, pair_and_register_worker
):
    """The worker learns about a queued run from its heartbeat response.

    The agent is a poller with no inbound HTTP surface, so the heartbeat is
    the delivery channel: it must carry the request while the run is queued,
    and stop carrying it once results have landed (otherwise the worker would
    re-run on every heartbeat forever).
    """
    await pair_and_register_worker(
        client, app, {"name": WORKER, "url": "http://10.0.0.9:9000", "platform": "linux"}
    )
    key = await app.state.cluster_pairing.get_signing_key(WORKER)
    assert key is not None

    async def heartbeat() -> dict:
        body = _json.dumps({"name": WORKER, "load": 0.05}).encode()
        resp = await client.post(
            "/api/cluster/heartbeat",
            content=body,
            headers={
                **_sign(key, WORKER, "/api/cluster/heartbeat", body),
                "content-type": "application/json",
            },
        )
        assert resp.status_code == 200, resp.text
        return resp.json()

    assert (await heartbeat())["benchmark_request"] is None

    queued = (await client.post(f"/api/workers/{WORKER}/benchmark", json={})).json()
    delivered = (await heartbeat())["benchmark_request"]
    assert delivered is not None
    assert delivered["worker_id"] == WORKER
    assert delivered["requested_at"] == queued["requested_at"]
    # Delivery is not consumption: the request stays queued until results land,
    # which is what retries a run that died with the worker mid-suite.
    assert (await heartbeat())["benchmark_request"] is not None

    results = await client.post(
        f"/api/workers/{WORKER}/benchmark/results",
        json=_report(
            first_join=False,
            value=61.0,
            measured_at=1_700_000_500.0,
            request_id=queued["requested_at"],
        ),
    )
    assert results.status_code == 200
    assert (await heartbeat())["benchmark_request"] is None
    assert await bench_store.get_pending_request(WORKER) is None


@pytest.mark.asyncio
async def test_a_click_made_mid_run_survives_the_in_flight_runs_report(
    bench_store, client, app, pair_and_register_worker
):
    """Two clicks in quick succession must produce two runs.

    The worker ignores a re-delivery while a run is in flight, so the second
    click stays queued until the first run reports. That report names the FIRST
    request, so it must clear that one only -- clearing by worker id would
    silently swallow the second click.
    """
    await pair_and_register_worker(
        client, app, {"name": WORKER, "url": "http://10.0.0.9:9000", "platform": "linux"}
    )

    first = (await client.post(f"/api/workers/{WORKER}/benchmark", json={})).json()
    await asyncio.sleep(0.01)
    second = (
        await client.post(f"/api/workers/{WORKER}/benchmark", json={"force": True})
    ).json()
    assert second["requested_at"] > first["requested_at"]

    report = await client.post(
        f"/api/workers/{WORKER}/benchmark/results",
        json=_report(
            value=48.0,
            measured_at=1_700_000_600.0,
            request_id=first["requested_at"],
        ),
    )
    assert report.status_code == 200
    assert report.json()["recorded"] == 1

    pending = await bench_store.get_pending_request(WORKER)
    assert pending is not None
    assert pending["requested_at"] == second["requested_at"]

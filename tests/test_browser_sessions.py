from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from pathlib import Path

from tinyagentos.browser_sessions import (
    BrowserSessionManager,
    BrowserWorkerError,
    list_browser_nodes,
    pick_browser_node,
    host_is_browser_capable,
    resolve_browser_target,
)

HOST_MIN_RAM_MB = 6144  # floor for running the browser locally; 8GB+ hosts pass, 4GB-class hosts are tier-gated to a cluster device


def test_host_capable_when_ram_meets_floor():
    assert host_is_browser_capable({"ram_mb": 8192}) is True
    assert host_is_browser_capable({"ram_mb": 16384}) is True


def test_host_not_capable_below_floor():
    assert host_is_browser_capable({"ram_mb": 4096}) is False
    assert host_is_browser_capable({}) is False
    assert host_is_browser_capable(None) is False


def test_target_prefers_explicit_then_host_then_worker():
    cap_host = {"ram_mb": 8192}
    # explicit worker wins when capable
    assert resolve_browser_target(_FakeCluster([]), cap_host, explicit_node=None) == ("host", None)
    # no capable host -> falls through to worker selection (None here, no workers)
    assert resolve_browser_target(_FakeCluster([]), {"ram_mb": 4096}, explicit_node=None) is None


@pytest.mark.asyncio
async def test_migrate_agent_browsers_idempotent(mgr):
    rows = [
        {"agent_name": "agent-A", "profile_name": "default", "node": "host", "status": "stopped", "container_id": None},
        {"agent_name": "agent-A", "profile_name": "work", "node": "host", "status": "stopped", "container_id": None},
    ]
    n1 = await mgr.migrate_agent_browsers(rows)
    n2 = await mgr.migrate_agent_browsers(rows)   # second run is a no-op
    assert n1 == 2
    assert n2 == 0
    sessions = await mgr.list_sessions("agent", "agent-A")
    assert {s["profile_name"] for s in sessions} == {"default", "work"}
    assert all(s["status"] == "stopped" for s in sessions)


@pytest.mark.asyncio
async def test_list_visible_sessions(mgr):
    await mgr.get_or_create_mine("user-1", url="https://mine")
    await mgr.create_session("agent", "agent-A", "https://a")
    await mgr.create_session("agent", "agent-B", "https://b")
    # user-1 owns agent-A only
    visible = await mgr.list_visible_sessions("user-1", owned_agent_ids={"agent-A"})
    kinds = {(s["owner_type"], s["owner_id"]) for s in visible}
    assert ("user", "user-1") in kinds
    assert ("agent", "agent-A") in kinds
    assert ("agent", "agent-B") not in kinds


@pytest.mark.asyncio
async def test_reap_idle_skips_user_sessions(mgr):
    u = await mgr.create_session("user", "user-1", "https://u")
    a = await mgr.create_session("agent", "agent-1", "https://a")
    for sid in (u["id"], a["id"]):
        await mgr.mark_running(sid, node="host", container_id="c", neko_url="n", cdp_url=None)
    # force both well past the timeout
    reaped = await mgr.reap_idle(now=10**12)
    assert a["id"] in reaped
    assert u["id"] not in reaped
    assert (await mgr.get_session(u["id"]))["status"] == "running"


@pytest.mark.asyncio
async def test_get_or_create_mine_is_idempotent(mgr):
    a = await mgr.get_or_create_mine("user-1", url="https://start.page")
    b = await mgr.get_or_create_mine("user-1", url="https://other.page")
    assert a["id"] == b["id"]          # one session per user
    assert a["owner_type"] == "user"
    # a different user gets a different session
    c = await mgr.get_or_create_mine("user-2", url="https://start.page")
    assert c["id"] != a["id"]


@pytest.mark.asyncio
async def test_get_or_create_mine_recreates_after_stop(mgr):
    a = await mgr.get_or_create_mine("user-1", url="https://x")
    await mgr.terminate_session(a["id"])           # status -> stopped
    b = await mgr.get_or_create_mine("user-1", url="https://x")
    assert b["id"] != a["id"]                       # stopped one not reused


@pytest.mark.asyncio
async def test_start_on_host_marks_running(mgr):
    session = await mgr.create_session("user", "user-1", "https://example.com")
    sid = session["id"]
    runner = MagicMock()
    runner.start = AsyncMock(return_value={
        "container_id": "c-1", "neko_url": "http://host:8800/?usr=neko&pwd=x",
        "cdp_url": None, "http_port": 8800, "epr_lo": 59000, "epr_hi": 59009,
    })
    out = await mgr.start_on_host(sid, profile_volume="taos-browser-%s" % sid, runner=runner)
    assert out["status"] == "running"
    assert out["container_id"] == "c-1"
    assert out["node"] == "host"
    runner.start.assert_awaited_once()


@pytest.mark.asyncio
async def test_start_on_host_marks_error_on_failure(mgr):
    session = await mgr.create_session("user", "user-1", "https://example.com")
    sid = session["id"]
    runner = MagicMock()
    runner.start = AsyncMock(side_effect=RuntimeError("docker down"))
    with pytest.raises(BrowserWorkerError):
        await mgr.start_on_host(sid, profile_volume="v", runner=runner)
    assert (await mgr.get_session(sid))["status"] == "error"


@pytest_asyncio.fixture
async def mgr(tmp_path):
    m = BrowserSessionManager(db_path=tmp_path / "browser_sessions.db", mock=True)
    await m.init()
    yield m
    await m.close()


@pytest.mark.asyncio
async def test_create_and_get_session_roundtrip(mgr):
    session = await mgr.create_session(
        owner_type="user",
        owner_id="user-1",
        url="https://example.com",
        profile_name="default",
    )
    assert session["owner_type"] == "user"
    assert session["owner_id"] == "user-1"
    assert session["url"] == "https://example.com"
    assert session["profile_name"] == "default"
    assert session["status"] == "pending"
    assert "id" in session
    assert "created_at" in session
    assert "updated_at" in session
    assert "last_active" in session

    fetched = await mgr.get_session(session["id"])
    assert fetched == session


@pytest.mark.asyncio
async def test_list_sessions_filters_by_owner(mgr):
    await mgr.create_session("user", "user-1", "https://a.com")
    await mgr.create_session("user", "user-1", "https://b.com")
    await mgr.create_session("agent", "agent-42", "https://c.com")

    user1_sessions = await mgr.list_sessions("user", "user-1")
    assert len(user1_sessions) == 2
    assert all(s["owner_type"] == "user" and s["owner_id"] == "user-1" for s in user1_sessions)

    agent_sessions = await mgr.list_sessions("agent", "agent-42")
    assert len(agent_sessions) == 1
    assert agent_sessions[0]["owner_type"] == "agent"

    nobody = await mgr.list_sessions("user", "nobody")
    assert nobody == []


@pytest.mark.asyncio
async def test_mark_running_sets_fields(mgr):
    session = await mgr.create_session("agent", "agent-1", "https://work.com")
    sid = session["id"]

    await mgr.mark_running(
        sid,
        node="node-1",
        container_id="ctr-abc",
        neko_url="http://neko:8080",
        cdp_url="ws://cdp:9222",
    )

    updated = await mgr.get_session(sid)
    assert updated["status"] == "running"
    assert updated["node"] == "node-1"
    assert updated["container_id"] == "ctr-abc"
    assert updated["neko_url"] == "http://neko:8080"
    assert updated["cdp_url"] == "ws://cdp:9222"


@pytest.mark.asyncio
async def test_touch_active_updates_last_active(mgr):
    t0 = 1_000_000.0
    session = await mgr.create_session("user", "user-2", "https://touch.com", now=t0)
    sid = session["id"]

    assert session["last_active"] == t0

    t1 = t0 + 60.0
    await mgr.touch_active(sid, now=t1)

    updated = await mgr.get_session(sid)
    assert updated["last_active"] == t1
    assert updated["updated_at"] == t1


@pytest.mark.asyncio
async def test_terminate_session(mgr):
    session = await mgr.create_session("user", "user-3", "https://stop.com")
    sid = session["id"]

    result = await mgr.terminate_session(sid)
    assert result is True

    stopped = await mgr.get_session(sid)
    assert stopped["status"] == "stopped"

    # Unknown id returns False
    result2 = await mgr.terminate_session("does-not-exist")
    assert result2 is False


# ---------------------------------------------------------------------------
# pick_browser_node tests
# ---------------------------------------------------------------------------

def _hw(ram_mb: int = 8192, cores: int = 8, cuda: bool = False, vram_mb: int = 0) -> dict:
    """Build a minimal hardware dict matching the HardwareProfile asdict shape."""
    return {
        "ram_mb": ram_mb,
        "cpu": {"cores": cores},
        "gpu": {"cuda": cuda, "vram_mb": vram_mb},
    }


class _FakeWorker:
    def __init__(
        self,
        name: str,
        status: str,
        hardware: dict,
        load: float = 0.0,
        capabilities: list[str] | None = None,
    ) -> None:
        self.name = name
        self.status = status
        self.hardware = hardware
        self.load = load
        self.capabilities = capabilities if capabilities is not None else ["browser"]


class _FakeCluster:
    def __init__(self, workers: list) -> None:
        self._workers = workers

    def get_workers(self) -> list:
        return self._workers


class TestPickBrowserNode:
    def test_no_workers_returns_none(self):
        cluster = _FakeCluster([])
        assert pick_browser_node(cluster) is None

    def test_under_spec_ram_returns_none(self):
        cluster = _FakeCluster([
            _FakeWorker("w1", "online", _hw(ram_mb=2048, cores=8)),
        ])
        assert pick_browser_node(cluster) is None

    def test_offline_capable_node_returns_none(self):
        cluster = _FakeCluster([
            _FakeWorker("w1", "offline", _hw(ram_mb=8192, cores=8)),
        ])
        assert pick_browser_node(cluster) is None

    def test_single_capable_node_returned(self):
        cluster = _FakeCluster([
            _FakeWorker("w1", "online", _hw(ram_mb=8192, cores=8)),
        ])
        assert pick_browser_node(cluster) == "w1"

    def test_prefers_gpu_capable_node(self):
        cluster = _FakeCluster([
            _FakeWorker("cpu-node", "online", _hw(ram_mb=8192, cores=8, cuda=False), load=0.1),
            _FakeWorker("gpu-node", "online", _hw(ram_mb=8192, cores=8, cuda=True, vram_mb=8192), load=0.5),
        ])
        assert pick_browser_node(cluster) == "gpu-node"

    def test_same_gpu_status_prefers_lower_load(self):
        cluster = _FakeCluster([
            _FakeWorker("heavy", "online", _hw(ram_mb=8192, cores=8), load=0.8),
            _FakeWorker("light", "online", _hw(ram_mb=8192, cores=8), load=0.2),
        ])
        assert pick_browser_node(cluster) == "light"

    def test_under_spec_cores_returns_none(self):
        cluster = _FakeCluster([
            _FakeWorker("w1", "online", _hw(ram_mb=8192, cores=2)),
        ])
        assert pick_browser_node(cluster) is None

    def test_missing_hardware_keys_treated_as_zero(self):
        cluster = _FakeCluster([
            _FakeWorker("w1", "online", {}),
        ])
        assert pick_browser_node(cluster) is None

    def test_exact_min_spec_qualifies(self):
        cluster = _FakeCluster([
            _FakeWorker("w1", "online", _hw(ram_mb=4096, cores=4)),
        ])
        assert pick_browser_node(cluster) == "w1"

    def test_capable_node_without_browser_capability_returns_none(self):
        cluster = _FakeCluster([
            _FakeWorker("w1", "online", _hw(ram_mb=8192, cores=8), capabilities=["embed"]),
        ])
        assert pick_browser_node(cluster) is None


# ---------------------------------------------------------------------------
# reap_idle tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reap_idle_returns_only_stale_session(mgr):
    base = 1_000_000.0
    fresh_session = await mgr.create_session("user", "user-1", "https://fresh.com", now=base)
    stale_session = await mgr.create_session("agent", "agent-1", "https://stale.com", now=base)

    await mgr.mark_running(
        fresh_session["id"],
        node="n1", container_id="ctr-1", neko_url="http://neko:8080", cdp_url="ws://cdp:9222",
        now=base,
    )
    await mgr.mark_running(
        stale_session["id"],
        node="n2", container_id="ctr-2", neko_url="http://neko:8081", cdp_url="ws://cdp:9223",
        now=base,
    )

    # Fresh session touched recently; stale session last touched 1000s ago
    now = base + 2000.0
    await mgr.touch_active(fresh_session["id"], now=now - 10)
    await mgr.touch_active(stale_session["id"], now=base + 100)  # 1900s ago relative to now

    reaped = await mgr.reap_idle(now=now)

    assert reaped == [stale_session["id"]]

    stale_row = await mgr.get_session(stale_session["id"])
    assert stale_row["status"] == "idle"

    fresh_row = await mgr.get_session(fresh_session["id"])
    assert fresh_row["status"] == "running"


@pytest.mark.asyncio
async def test_reap_idle_skips_pending_session(mgr):
    base = 1_000_000.0
    pending_session = await mgr.create_session("user", "user-1", "https://pending.com", now=base)
    # pending session has an old last_active but should NOT be reaped (only running sessions are)

    now = base + 2000.0
    reaped = await mgr.reap_idle(now=now)

    assert pending_session["id"] not in reaped
    row = await mgr.get_session(pending_session["id"])
    assert row["status"] == "pending"


@pytest.mark.asyncio
async def test_reap_idle_nothing_stale_returns_empty(mgr):
    base = 1_000_000.0
    session = await mgr.create_session("user", "user-1", "https://active.com", now=base)
    await mgr.mark_running(
        session["id"],
        node="n1", container_id="ctr-1", neko_url="http://neko:8080", cdp_url="ws://cdp:9222",
        now=base,
    )
    # Touch it fresh relative to now
    now = base + 100.0
    await mgr.touch_active(session["id"], now=now - 10)

    reaped = await mgr.reap_idle(now=now)
    assert reaped == []


# ---------------------------------------------------------------------------
# start_on_worker / stop_on_worker / mark_error tests
# ---------------------------------------------------------------------------

def _make_mock_response(status_code: int, json_body: dict):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_body
    resp.text = str(json_body)
    return resp


@pytest.mark.asyncio
async def test_start_on_worker_success(mgr):
    session = await mgr.create_session("user", "u1", "https://example.com")
    sid = session["id"]

    worker_resp = _make_mock_response(200, {
        "container_id": "ctr-xyz",
        "neko_url": "http://10.0.0.5:8800/?usr=neko&pwd=abc",
        "cdp_url": None,
        "http_port": 8800,
        "epr_lo": 59000,
        "epr_hi": 59009,
    })

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=worker_resp)

    with patch("tinyagentos.browser_sessions.httpx.AsyncClient", return_value=mock_client):
        result = await mgr.start_on_worker(
            sid,
            node="node-1",
            worker_url="http://worker.example:7080",
            profile_volume=f"taos-browser-{sid}",
        )

    assert result["status"] == "running"
    assert result["container_id"] == "ctr-xyz"
    assert result["neko_url"] == "http://10.0.0.5:8800/?usr=neko&pwd=abc"
    assert result["node"] == "node-1"

    # Confirm DB was updated
    fetched = await mgr.get_session(sid)
    assert fetched["status"] == "running"
    assert fetched["container_id"] == "ctr-xyz"


@pytest.mark.asyncio
async def test_start_on_worker_non_200_marks_error_and_raises(mgr):
    session = await mgr.create_session("user", "u2", "https://example.com")
    sid = session["id"]

    worker_resp = _make_mock_response(500, {"error": "docker failed"})

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=worker_resp)

    with patch("tinyagentos.browser_sessions.httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(BrowserWorkerError):
            await mgr.start_on_worker(
                sid,
                node="node-1",
                worker_url="http://worker.example:7080",
                profile_volume=f"taos-browser-{sid}",
            )

    fetched = await mgr.get_session(sid)
    assert fetched["status"] == "error"


@pytest.mark.asyncio
async def test_start_on_worker_exception_marks_error_and_raises(mgr):
    session = await mgr.create_session("user", "u3", "https://example.com")
    sid = session["id"]

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(side_effect=Exception("connection refused"))

    with patch("tinyagentos.browser_sessions.httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(BrowserWorkerError):
            await mgr.start_on_worker(
                sid,
                node="node-1",
                worker_url="http://worker.example:7080",
                profile_volume=f"taos-browser-{sid}",
            )

    fetched = await mgr.get_session(sid)
    assert fetched["status"] == "error"


@pytest.mark.asyncio
async def test_stop_on_worker_failure_does_not_raise(mgr):
    session = await mgr.create_session("user", "u4", "https://example.com")
    sid = session["id"]
    await mgr.mark_running(
        sid, node="node-1", container_id="ctr-1",
        neko_url="http://neko:8080", cdp_url=None,
    )

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(side_effect=Exception("network error"))

    with patch("tinyagentos.browser_sessions.httpx.AsyncClient", return_value=mock_client):
        # Must not raise
        await mgr.stop_on_worker(
            sid, worker_url="http://worker.example:7080",
            container_id="ctr-1",
        )

    fetched = await mgr.get_session(sid)
    assert fetched["status"] == "stopped"


@pytest.mark.asyncio
async def test_stop_on_worker_set_status_none_leaves_status_unchanged(mgr):
    session = await mgr.create_session("user", "u5", "https://example.com")
    sid = session["id"]
    await mgr.mark_running(
        sid, node="node-1", container_id="ctr-2",
        neko_url="http://neko:8081", cdp_url=None,
    )
    # Simulate reap already flipped to idle
    db = mgr._assert_db()
    await db.execute("UPDATE browser_sessions SET status='idle' WHERE id=?", (sid,))
    await db.commit()

    worker_resp = _make_mock_response(200, {"ok": True})
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=worker_resp)

    with patch("tinyagentos.browser_sessions.httpx.AsyncClient", return_value=mock_client):
        await mgr.stop_on_worker(
            sid, worker_url="http://worker.example:7080",
            container_id="ctr-2", set_status=None,
        )

    fetched = await mgr.get_session(sid)
    assert fetched["status"] == "idle"


# ---------------------------------------------------------------------------
# list_browser_nodes tests
# ---------------------------------------------------------------------------

class TestListBrowserNodes:
    def test_returns_only_capable_nodes(self):
        cluster = _FakeCluster([
            _FakeWorker("capable", "online", _hw(ram_mb=8192, cores=8)),
            _FakeWorker("no-cap", "online", _hw(ram_mb=8192, cores=8), capabilities=["embed"]),
            _FakeWorker("offline", "offline", _hw(ram_mb=8192, cores=8)),
        ])
        nodes = list_browser_nodes(cluster)
        assert len(nodes) == 1
        assert nodes[0]["name"] == "capable"

    def test_node_dict_shape(self):
        cluster = _FakeCluster([
            _FakeWorker("w1", "online", _hw(ram_mb=8192, cores=8, cuda=True, vram_mb=4096), load=0.3),
        ])
        nodes = list_browser_nodes(cluster)
        assert len(nodes) == 1
        n = nodes[0]
        assert n["name"] == "w1"
        assert n["gpu"] is True
        assert n["ram_mb"] == 8192
        assert n["cores"] == 8
        assert n["load"] == 0.3

    def test_gpu_first_ordering(self):
        cluster = _FakeCluster([
            _FakeWorker("cpu-node", "online", _hw(ram_mb=8192, cores=8, cuda=False), load=0.1),
            _FakeWorker("gpu-node", "online", _hw(ram_mb=8192, cores=8, cuda=True, vram_mb=8192), load=0.5),
        ])
        nodes = list_browser_nodes(cluster)
        assert nodes[0]["name"] == "gpu-node"
        assert nodes[1]["name"] == "cpu-node"

    def test_same_gpu_tier_sorted_by_load(self):
        cluster = _FakeCluster([
            _FakeWorker("heavy", "online", _hw(ram_mb=8192, cores=8), load=0.8),
            _FakeWorker("light", "online", _hw(ram_mb=8192, cores=8), load=0.2),
        ])
        nodes = list_browser_nodes(cluster)
        assert nodes[0]["name"] == "light"
        assert nodes[1]["name"] == "heavy"

    def test_empty_cluster_returns_empty_list(self):
        cluster = _FakeCluster([])
        assert list_browser_nodes(cluster) == []

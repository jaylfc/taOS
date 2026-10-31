"""Tests for GET /api/cluster/resources --- the read-only resource inventory.

The "see it" half of #897 slice S9: one flat aggregate over the cluster
manager's own state (no second source of truth) that answers "what is taOS
running, on what, and is it healthy". Covers agents joined to their incus
containers, backends per worker, installed apps, and the controller -- with
an error-isolation guarantee: a failing source records its name in `errors`
rather than 500-ing the whole list.

Coverage:
- a cookie-less caller and a signed-in non-admin are both refused
- an admin sees one row per agent, per worker backend, per installed app,
  plus exactly one controller row
- a backend on an offline worker reports stopped
- list_containers raising gives errors containing "containers" and agent rows
  with state "unknown" and a 200 response
- every row has exactly the documented key set
"""
from __future__ import annotations

import time

import pytest

from tinyagentos.cluster.worker_protocol import WorkerInfo
from tinyagentos.containers.backend import ContainerInfo


def _put_worker(app, worker: WorkerInfo) -> WorkerInfo:
    """Register a worker on the app's cluster manager (mirrors cluster_map tests)."""
    app.state.cluster_manager._workers[worker.name] = worker
    return worker


def _stub_installed_apps(app, apps):
    """Replace installed_apps.list_installed and get_runtime_location on the instance.

    ``apps`` is a list of ``(app_id, runtime_location)`` tuples where
    ``runtime_location`` is ``None`` or a dict carrying ``runtime_host`` /
    ``runtime_port``.  Setting the attributes on the instance shadows the
    class methods without touching the class or other instances.
    """
    async def _list_installed():
        return [
            {
                "app_id": aid,
                "installed_at": 1234567890.0,
                "version": "1.0.0",
                "metadata": {},
            }
            for aid, _loc in apps
        ]

    async def _get_runtime_location(app_id):
        for aid, loc in apps:
            if aid == app_id:
                return loc
        return None

    app.state.installed_apps.list_installed = _list_installed
    app.state.installed_apps.get_runtime_location = _get_runtime_location


@pytest.mark.asyncio
async def test_resources_requires_an_admin_session(app):
    """A cookie-less caller is refused before the handler runs."""
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as anon:
        resp = await anon.get("/api/cluster/resources")
    assert resp.status_code in (401, 403)
    assert "resources" not in resp.text


@pytest.mark.asyncio
async def test_resources_rejects_a_signed_in_non_admin(client, app, monkeypatch):
    """A signed-in non-admin gets the route's own 403, not just the middleware's."""
    monkeypatch.setattr(
        app.state.auth, "session_user",
        lambda token, user_agent=None: {"is_admin": False},
    )

    resp = await client.get("/api/cluster/resources")
    assert resp.status_code == 403, resp.text
    assert "resources" not in resp.text


@pytest.mark.asyncio
async def test_resources_aggregates_agents_backends_apps_and_controller(
    client, app, monkeypatch,
):
    """Admin sees one row per agent, per backend, per app, plus one controller."""
    # Mock list_containers so the test-agent gets a Running container.
    async def _list_containers(prefix="taos-agent-"):
        return [
            ContainerInfo(
                name="taos-agent-test-agent",
                status="Running",
                ip="10.0.0.1",
                memory_mb=512,
                cpu_cores=1,
            ),
        ]
    monkeypatch.setattr(
        "tinyagentos.routes.cluster_resources.list_containers", _list_containers,
    )

    now = time.time()
    _put_worker(
        app,
        WorkerInfo(
            name="gpu-box",
            url="http://10.0.0.7:9000",
            platform="linux",
            status="online",
            last_heartbeat=now,
            hardware={
                "ram_mb": 32768,
                "cpu": {"arch": "x86_64", "cores": 8},
                "gpu": {"type": "nvidia", "cuda": True, "vram_mb": 12288},
            },
            backends=[
                {
                    "name": "vllm:8000",
                    "type": "vllm",
                    "status": "ok",
                    "capabilities": ["llm-chat"],
                },
            ],
            capabilities=["chat"],
        ),
    )

    _stub_installed_apps(app, [("gitea-lxc", {
        "runtime_host": "localhost",
        "runtime_port": 3000,
        "backend": "lxc",
        "ui_path": "/",
    })])

    resp = await client.get("/api/cluster/resources")
    assert resp.status_code == 200, resp.text
    data = resp.json()

    by_type: dict[str, list[dict]] = {}
    for r in data["resources"]:
        by_type.setdefault(r["type"], []).append(r)

    # One agent row (test-agent from conftest config) with a Running container.
    assert len(by_type.get("agent", [])) == 1
    agent_row = by_type["agent"][0]
    assert agent_row["name"] == "test-agent"
    assert agent_row["worker"] == "local"
    assert agent_row["state"] == "running"
    assert agent_row["cpu_cores"] == 1
    assert agent_row["memory_mb"] == 512
    assert agent_row["id"] == "agent:test-agent"

    # One backend row from the offline-capable worker.
    assert len(by_type.get("backend", [])) == 1
    backend_row = by_type["backend"][0]
    assert backend_row["name"] == "vllm:8000"
    assert backend_row["worker"] == "gpu-box"
    assert backend_row["state"] == "running"
    assert backend_row["id"] == "backend:gpu-box:vllm:8000"

    # One app row with a runtime location.
    assert len(by_type.get("app", [])) == 1
    app_row = by_type["app"][0]
    assert app_row["name"] == "gitea-lxc"
    assert app_row["worker"] == "local"
    assert app_row["state"] == "running"
    assert app_row["id"] == "app:gitea-lxc"

    # Exactly one controller row, always running.
    assert len(by_type.get("controller", [])) == 1
    ctrl_row = by_type["controller"][0]
    assert ctrl_row["name"] == "local"
    assert ctrl_row["state"] == "running"
    assert ctrl_row["id"] == "controller:local"

    assert data["errors"] == []
    assert isinstance(data["generated_at"], float)


@pytest.mark.asyncio
async def test_resources_backend_on_offline_worker_is_stopped(client, app, monkeypatch):
    """A backend whose worker is offline reports stopped even if status is ok."""
    async def _list_containers(prefix="taos-agent-"):
        return []
    monkeypatch.setattr(
        "tinyagentos.routes.cluster_resources.list_containers", _list_containers,
    )
    _stub_installed_apps(app, [])

    _put_worker(
        app,
        WorkerInfo(
            name="offline-box",
            url="http://10.0.0.99:9000",
            platform="linux",
            status="offline",
            last_heartbeat=time.time() - 3600,  # well past the 300 s stale window
            hardware={"ram_mb": 16384, "cpu": {"arch": "x86_64"}, "gpu": {}},
            backends=[
                {
                    "name": "vllm:8000",
                    "type": "vllm",
                    "status": "ok",
                    "capabilities": ["llm-chat"],
                },
            ],
        ),
    )

    resp = await client.get("/api/cluster/resources")
    assert resp.status_code == 200, resp.text
    data = resp.json()

    backend_rows = [r for r in data["resources"] if r["type"] == "backend"]
    assert len(backend_rows) == 1
    assert backend_rows[0]["state"] == "stopped"
    assert "worker offline" in backend_rows[0]["warnings"]


@pytest.mark.asyncio
async def test_resources_list_containers_failure_yields_unknown_agents(
    client, app, monkeypatch,
):
    """When list_containers raises, errors contains "containers" and agent rows
    are listed with state "unknown"; the rest of the inventory still ships."""
    async def _raise(*args, **kwargs):
        raise RuntimeError("incus is down")

    monkeypatch.setattr(
        "tinyagentos.routes.cluster_resources.list_containers", _raise,
    )
    _stub_installed_apps(app, [])

    # One offline worker so backends still appear.
    _put_worker(
        app,
        WorkerInfo(
            name="gpu-box",
            url="http://10.0.0.7:9000",
            platform="linux",
            status="online",
            last_heartbeat=time.time(),
            backends=[
                {"name": "vllm:8000", "type": "vllm", "status": "ok", "capabilities": ["llm-chat"]},
            ],
        ),
    )

    resp = await client.get("/api/cluster/resources")
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert "containers" in data["errors"]

    agent_rows = [r for r in data["resources"] if r["type"] == "agent"]
    # test-agent comes from the conftest config.
    assert len(agent_rows) == 1
    assert agent_rows[0]["state"] == "unknown"
    assert agent_rows[0]["cpu_cores"] is None
    assert agent_rows[0]["memory_mb"] is None
    assert "container not found" in agent_rows[0]["warnings"]

    # Controller and backends still present.
    ctrl_rows = [r for r in data["resources"] if r["type"] == "controller"]
    assert len(ctrl_rows) == 1
    backend_rows = [r for r in data["resources"] if r["type"] == "backend"]
    assert len(backend_rows) == 1


@pytest.mark.asyncio
async def test_resources_row_keys_are_exact(client, app, monkeypatch):
    """Every resource row has exactly the documented key set."""
    async def _list_containers(prefix="taos-agent-"):
        return [
            ContainerInfo(
                name="taos-agent-test-agent",
                status="Stopped",
                ip=None,
                memory_mb=0,
                cpu_cores=0,
            ),
        ]
    monkeypatch.setattr(
        "tinyagentos.routes.cluster_resources.list_containers", _list_containers,
    )
    _stub_installed_apps(app, [("gitea-lxc", None)])

    _put_worker(
        app,
        WorkerInfo(
            name="gpu-box",
            url="http://10.0.0.7:9000",
            platform="linux",
            status="online",
            last_heartbeat=time.time(),
            backends=[
                {"name": "sd-cpp", "type": "sd-cpp", "status": "stopped", "capabilities": ["image-generation"]},
            ],
        ),
    )

    resp = await client.get("/api/cluster/resources")
    assert resp.status_code == 200, resp.text
    data = resp.json()

    expected_keys = {
        "id", "type", "name", "worker", "project",
        "state", "cpu_cores", "memory_mb", "warnings",
    }
    assert all(set(r.keys()) == expected_keys for r in data["resources"])
    assert isinstance(data["errors"], list)
    assert isinstance(data["generated_at"], float)

    # Statuses map correctly: incus "Stopped" -> "stopped".
    agent_row = next(r for r in data["resources"] if r["type"] == "agent")
    assert agent_row["state"] == "stopped"
    # App without runtime location -> "unknown".
    app_row = next(r for r in data["resources"] if r["type"] == "app")
    assert app_row["state"] == "unknown"
    # Backend stopped status -> "stopped".
    backend_row = next(r for r in data["resources"] if r["type"] == "backend")
    assert backend_row["state"] == "stopped"


@pytest.mark.asyncio
async def test_resources_sorted_by_type_worker_name(client, app, monkeypatch):
    """Rows are sorted by (type, worker, name)."""
    async def _list_containers(prefix="taos-agent-"):
        return []
    monkeypatch.setattr(
        "tinyagentos.routes.cluster_resources.list_containers", _list_containers,
    )
    _stub_installed_apps(app, [])

    _put_worker(
        app,
        WorkerInfo(
            name="z-box",
            url="http://10.0.0.99:9000",
            platform="linux",
            status="online",
            last_heartbeat=time.time(),
            backends=[
                {"name": "aaa", "type": "vllm", "status": "ok", "capabilities": []},
                {"name": "bbb", "type": "vllm", "status": "ok", "capabilities": []},
            ],
        ),
    )
    _put_worker(
        app,
        WorkerInfo(
            name="a-box",
            url="http://10.0.0.1:9000",
            platform="linux",
            status="online",
            last_heartbeat=time.time(),
            backends=[
                {"name": "aaa", "type": "sd-cpp", "status": "ok", "capabilities": []},
            ],
        ),
    )

    resp = await client.get("/api/cluster/resources")
    assert resp.status_code == 200, resp.text
    data = resp.json()

    keys = [(r["type"], r["worker"], r["name"]) for r in data["resources"]]
    assert keys == sorted(keys)

@pytest.mark.asyncio
async def test_backend_ids_unique_when_same_name_across_workers(client, app, monkeypatch):
    """Two workers reporting the same backend name must produce different ids."""
    async def _list_containers(prefix="taos-agent-"):
        return []
    monkeypatch.setattr(
        "tinyagentos.routes.cluster_resources.list_containers", _list_containers,
    )
    _stub_installed_apps(app, [])

    _put_worker(
        app,
        WorkerInfo(
            name="worker-a",
            url="http://10.0.0.1:9000",
            platform="linux",
            status="online",
            last_heartbeat=time.time(),
            backends=[
                {"name": "aaa", "type": "vllm", "status": "ok", "capabilities": []},
            ],
        ),
    )
    _put_worker(
        app,
        WorkerInfo(
            name="worker-b",
            url="http://10.0.0.2:9000",
            platform="linux",
            status="online",
            last_heartbeat=time.time(),
            backends=[
                {"name": "aaa", "type": "vllm", "status": "ok", "capabilities": []},
            ],
        ),
    )

    resp = await client.get("/api/cluster/resources")
    assert resp.status_code == 200, resp.text
    data = resp.json()

    backend_rows = [r for r in data["resources"] if r["type"] == "backend"]
    assert len(backend_rows) == 2
    ids = [r["id"] for r in backend_rows]
    assert ids[0] != ids[1]

@pytest.mark.asyncio
async def test_app_runtime_lookup_failure_does_not_drop_later_apps(client, app, monkeypatch):
    """If get_runtime_location raises for one app, later apps still appear."""
    async def _list_containers(prefix="taos-agent-"):
        return []
    monkeypatch.setattr(
        "tinyagentos.routes.cluster_resources.list_containers", _list_containers,
    )

    # List three apps; runtime lookup raises for the second.
    apps = [
        ("app1", None),
        ("app2", None),
        ("app3", None),
    ]

    async def _list_installed():
        return [
            {
                "app_id": aid,
                "installed_at": 1234567890.0,
                "version": "1.0.0",
                "metadata": {},
            }
            for aid, _loc in apps
        ]

    async def _get_runtime_location(app_id):
        if app_id == "app2":
            raise RuntimeError("lookup failed")
        for aid, loc in apps:
            if aid == app_id:
                return loc
        return None

    app.state.installed_apps.list_installed = _list_installed
    app.state.installed_apps.get_runtime_location = _get_runtime_location

    resp = await client.get("/api/cluster/resources")
    assert resp.status_code == 200, resp.text
    data = resp.json()

    app_rows = [r for r in data["resources"] if r["type"] == "app"]
    assert len(app_rows) == 3
    by_id = {r["name"]: r for r in app_rows}
    assert by_id["app1"]["state"] == "unknown"
    assert "runtime lookup failed" not in by_id["app1"].get("warnings", [])
    assert by_id["app2"]["state"] == "unknown"
    assert "runtime lookup failed" in by_id["app2"].get("warnings", [])
    assert by_id["app3"]["state"] == "unknown"
    assert "installed_apps" not in data["errors"]

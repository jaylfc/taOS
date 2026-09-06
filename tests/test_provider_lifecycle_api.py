from __future__ import annotations
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient
from tinyagentos.routes.providers import router
from fastapi import FastAPI

app = FastAPI()


# This bare app has no AuthMiddleware, so simulate an already-authenticated
# admin request (request.state.is_admin) -- these tests exercise the provider
# handlers themselves, not the admin-or-local-token authz gate the mutating
# routes now enforce (see tests/test_global_routers_authz.py for that).
@app.middleware("http")
async def _fake_admin_auth(request, call_next):
    request.state.is_admin = True
    request.state.via = "session"
    return await call_next(request)


app.include_router(router)


def _make_app_state(backends: list[dict], lifecycle_states: dict):
    config = MagicMock()
    config.backends = backends
    config.config_path = Path("/tmp/test-config.yaml")

    catalog = MagicMock()
    catalog.backends = lambda: [
        MagicMock(
            name=b["name"], type=b["type"], url=b["url"],
            status="ok", response_ms=5, models=[],
            lifecycle_state=lifecycle_states.get(b["name"], "running"),
            auto_manage=b.get("auto_manage", False),
            keep_alive_minutes=b.get("keep_alive_minutes", 10),
            enabled=b.get("enabled", True),
            to_dict=lambda b=b, ls=lifecycle_states: {
                **b, "status": "ok", "response_ms": 5, "models": [],
                "lifecycle_state": ls.get(b["name"], "running"),
            },
        )
        for b in backends
    ]
    catalog.get_lifecycle_state = lambda name: lifecycle_states.get(name, "running")

    lifecycle = AsyncMock()
    return config, catalog, lifecycle


def test_patch_provider_updates_lifecycle():
    config, catalog, lifecycle = _make_app_state(
        [{"name": "b1", "type": "sd-cpp", "url": "http://b1", "priority": 99,
          "auto_manage": False, "keep_alive_minutes": 10, "enabled": True}],
        {},
    )
    with patch("tinyagentos.routes.providers.save_config_locked", new=AsyncMock()):
        with TestClient(app) as client:
            client.app.state.config = config
            client.app.state.backend_catalog = catalog
            client.app.state.lifecycle_manager = lifecycle
            resp = client.patch("/api/providers/b1", json={"auto_manage": True, "keep_alive_minutes": 5})
    assert resp.status_code == 200
    assert config.backends[0]["auto_manage"] is True
    assert config.backends[0]["keep_alive_minutes"] == 5


def test_patch_provider_not_found():
    config, catalog, lifecycle = _make_app_state([], {})
    with TestClient(app) as client:
        client.app.state.config = config
        client.app.state.backend_catalog = catalog
        client.app.state.lifecycle_manager = lifecycle
        resp = client.patch("/api/providers/nonexistent", json={"enabled": False})
    assert resp.status_code == 404


def test_start_provider_calls_lifecycle_manager():
    config, catalog, lifecycle = _make_app_state(
        [{"name": "b1", "type": "sd-cpp", "url": "http://b1", "priority": 99,
          "auto_manage": True, "keep_alive_minutes": 10, "enabled": True}],
        {"b1": "stopped"},
    )
    with TestClient(app) as client:
        client.app.state.config = config
        client.app.state.backend_catalog = catalog
        client.app.state.lifecycle_manager = lifecycle
        resp = client.post("/api/providers/b1/start")
    assert resp.status_code == 200
    lifecycle.start.assert_called_once_with("b1")


def test_stop_provider_graceful():
    config, catalog, lifecycle = _make_app_state(
        [{"name": "b1", "type": "sd-cpp", "url": "http://b1", "priority": 99,
          "auto_manage": True, "keep_alive_minutes": 10, "enabled": True}],
        {"b1": "running"},
    )
    with TestClient(app) as client:
        client.app.state.config = config
        client.app.state.backend_catalog = catalog
        client.app.state.lifecycle_manager = lifecycle
        resp = client.post("/api/providers/b1/stop", json={"force": False})
    assert resp.status_code == 200
    lifecycle.drain_and_stop.assert_called_once_with("b1", force=False)


def test_stop_provider_force():
    config, catalog, lifecycle = _make_app_state(
        [{"name": "b1", "type": "sd-cpp", "url": "http://b1", "priority": 99,
          "auto_manage": True, "keep_alive_minutes": 10, "enabled": True}],
        {"b1": "running"},
    )
    with TestClient(app) as client:
        client.app.state.config = config
        client.app.state.backend_catalog = catalog
        client.app.state.lifecycle_manager = lifecycle
        resp = client.post("/api/providers/b1/stop", json={"force": True})
    assert resp.status_code == 200
    lifecycle.drain_and_stop.assert_called_once_with("b1", force=True)

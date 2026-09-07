"""Red-first tests: secret-bearing routes must set Cache-Control: no-store.

Each test drives the route through a bare FastAPI app (no SecurityHeadersMiddleware)
so the assertion fails on origin/dev and only passes once the route itself applies
the header.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

# Ensure tinyagentos is importable when running pytest from repo root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class TestProvidersNoStore:
    """GET /api/providers returns inline api_key for admin callers."""

    def test_list_providers_sets_no_store(self):
        from tinyagentos.routes.providers import router

        app = FastAPI()

        @app.middleware("http")
        async def _fake_admin_auth(request, call_next):
            request.state.is_admin = True
            request.state.via = "session"
            return await call_next(request)

        app.include_router(router)

        config = MagicMock()
        config.backends = [
            {"name": "b1", "type": "openai", "url": "http://b1", "api_key": "sk-secret"}
        ]
        config.config_path = Path("/tmp/test-config.yaml")

        catalog = MagicMock()
        catalog.backends = lambda: []
        catalog.get_lifecycle_state = lambda name: "running"

        with TestClient(app) as client:
            client.app.state.config = config
            client.app.state.backend_catalog = catalog
            client.app.state.http_client = MagicMock()
            resp = client.get("/api/providers")
        assert resp.status_code == 200
        assert resp.headers.get("cache-control") == "no-store"


class TestSecretsNoStore:
    """Secret-bearing routes must set Cache-Control: no-store."""

    def test_get_secret_sets_no_store(self):
        from tinyagentos.routes.secrets import router

        app = FastAPI()

        @app.middleware("http")
        async def _fake_admin_auth(request, call_next):
            request.state.is_admin = True
            return await call_next(request)

        app.include_router(router)

        store = AsyncMock()
        store.get.return_value = {"name": "TEST_KEY", "value": "secret-value"}
        app.state.secrets = store

        with TestClient(app) as client:
            resp = client.get("/api/secrets/TEST_KEY")
        assert resp.status_code == 200
        assert resp.headers.get("cache-control") == "no-store"

    def test_get_agent_secrets_sets_no_store(self):
        from tinyagentos.routes.secrets import router

        app = FastAPI()

        @app.middleware("http")
        async def _fake_admin_auth(request, call_next):
            request.state.user_id = "user-1"
            request.state.is_admin = True
            request.state.via = "session"
            return await call_next(request)

        app.include_router(router)

        store = AsyncMock()
        store.get_agent_secrets.return_value = [
            {"name": "AGENT_SEC", "value": "secret-value"}
        ]
        app.state.secrets = store

        registry = MagicMock()
        registry.get_by_handle.return_value = {"user_id": "user-1"}
        app.state.agent_registry = registry

        with TestClient(app) as client:
            resp = client.get("/api/secrets/agent/test-agent")
        assert resp.status_code == 200
        assert resp.headers.get("cache-control") == "no-store"

import pytest
from fastapi import FastAPI

from tinyagentos.app import create_app
from tinyagentos.routes import register_all_routers


def test_create_app_registers_routers(tmp_data_dir):
    app = create_app(data_dir=tmp_data_dir)
    assert len(app.routes) > 50, f"Expected >50 routes, got {len(app.routes)}"


def test_memory_management_included_once(tmp_data_dir):
    app = create_app(data_dir=tmp_data_dir)
    all_paths = [route.path for route in app.routes if hasattr(route, "path")]
    memory_stats_paths = [p for p in all_paths if p == "/api/memory/stats"]
    assert len(memory_stats_paths) >= 1, "Expected /api/memory/stats route to be present"
    assert len(memory_stats_paths) == 1, (
        f"memory_management router registered more than once: "
        f"/api/memory/stats appears {len(memory_stats_paths)} times"
    )


def test_prefetch_endpoint_still_registered(tmp_data_dir):
    # register_prefetch_endpoint(app) is a non-router registration that lives
    # alongside register_all_routers in create_app; guard against it being
    # dropped when the router block is refactored.
    app = create_app(data_dir=tmp_data_dir)
    all_paths = [route.path for route in app.routes if hasattr(route, "path")]
    assert "/api/agent-image/status" in all_paths, (
        "agent-image prefetch status endpoint missing — "
        "register_prefetch_endpoint(app) was dropped"
    )


def test_register_all_routers_idempotent_shape(tmp_data_dir):
    app = FastAPI()
    # Should not raise
    register_all_routers(app)
    assert len(app.routes) > 50, f"Expected >50 routes, got {len(app.routes)}"

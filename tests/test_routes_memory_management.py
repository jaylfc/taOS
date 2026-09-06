"""Endpoint tests for tinyagentos/routes/memory_management.py."""

from __future__ import annotations

from unittest.mock import AsyncMock, PropertyMock, patch

import pytest

from taosmd import TaOSmdBackend


# ---------------------------------------------------------------------------
# Shared mock helpers
# ---------------------------------------------------------------------------

def _make_backend(**overrides):
    mock = AsyncMock(spec=TaOSmdBackend)
    mock.get_stats.return_value = overrides.get("get_stats", {"kg": {"total": 10}})
    mock.get_settings.return_value = overrides.get(
        "get_settings", {"default_strategy": "thorough"}
    )
    mock.update_settings.return_value = overrides.get(
        "update_settings", {"default_strategy": "fast"}
    )
    mock.get_settings_schema.return_value = overrides.get(
        "get_settings_schema",
        {"type": "object", "properties": {"default_strategy": {"type": "string"}}},
    )
    mock.get_agent_config.return_value = overrides.get(
        "get_agent_config",
        {"strategy": "thorough", "layers": []},
    )
    mock.update_agent_config.return_value = overrides.get(
        "update_agent_config",
        {"strategy": "fast", "layers": []},
    )
    mock.get_recipe_schema.return_value = overrides.get(
        "get_recipe_schema",
        {"type": "object", "properties": {"name": {"type": "string"}}},
    )
    mock.list_recipes.return_value = overrides.get(
        "list_recipes",
        [{"id": "default", "name": "Default"}],
    )
    mock.get_recipe.return_value = overrides.get(
        "get_recipe",
        {"id": "default", "name": "Default"},
    )
    mock.apply_recipe.return_value = overrides.get(
        "apply_recipe",
        {"applied_recipe_id": "default", "recipe": {"id": "default", "name": "Default"}},
    )
    mock.recommend.return_value = overrides.get(
        "recommend",
        [{"id": "default", "rationale": "best fit"}],
    )
    mock.create_recipe.side_effect = overrides.get(
        "create_recipe_side_effect",
        NotImplementedError("custom recipes not yet implemented"),
    )
    return mock


def _patch_backend(mock_backend):
    return patch(
        "tinyagentos.routes.memory_management._backend",
        return_value=mock_backend,
    )


def _patch_import_backend(mock_backend):
    """Patch taosmd.TaOSmdBackend where it is imported inside the function."""
    return patch(
        "taosmd.TaOSmdBackend",
        mock_backend,
    )


# ---------------------------------------------------------------------------
# GET /api/memory/stats
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_memory_stats_returns_200(client):
    mock = _make_backend()
    with _patch_backend(mock):
        resp = await client.get("/api/memory/stats")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_memory_stats_happy_path(client):
    mock = _make_backend()
    with _patch_backend(mock):
        resp = await client.get("/api/memory/stats")
    data = resp.json()
    assert isinstance(data, dict)
    assert "kg" in data


@pytest.mark.asyncio
async def test_memory_stats_backend_error(client):
    mock = _make_backend()
    mock.get_stats.side_effect = RuntimeError("backend down")
    with _patch_backend(mock):
        resp = await client.get("/api/memory/stats")
    assert resp.status_code == 500
    assert "error" in resp.json()


# ---------------------------------------------------------------------------
# GET /api/memory/settings
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_memory_settings_get_returns_200(client):
    mock = _make_backend()
    with _patch_backend(mock):
        resp = await client.get("/api/memory/settings")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_memory_settings_get_has_strategy(client):
    mock = _make_backend()
    with _patch_backend(mock):
        resp = await client.get("/api/memory/settings")
    data = resp.json()
    assert isinstance(data, dict)
    assert "default_strategy" in data


@pytest.mark.asyncio
async def test_memory_settings_get_error(client):
    mock = _make_backend()
    mock.get_settings.side_effect = RuntimeError("db fail")
    with _patch_backend(mock):
        resp = await client.get("/api/memory/settings")
    assert resp.status_code == 500
    assert "error" in resp.json()


# ---------------------------------------------------------------------------
# PUT /api/memory/settings
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_memory_settings_put_returns_200(client):
    mock = _make_backend()
    with _patch_backend(mock):
        resp = await client.put(
            "/api/memory/settings",
            json={"default_strategy": "fast"},
        )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_memory_settings_put_returns_merged_settings(client):
    mock = _make_backend()
    with _patch_backend(mock):
        resp = await client.put(
            "/api/memory/settings",
            json={"default_strategy": "fast"},
        )
    data = resp.json()
    assert isinstance(data, dict)
    assert "default_strategy" in data


@pytest.mark.asyncio
async def test_memory_settings_put_invalid_json(client):
    mock = _make_backend()
    with _patch_backend(mock):
        resp = await client.put(
            "/api/memory/settings",
            content=b"not-json",
            headers={"content-type": "application/json"},
        )
    assert resp.status_code == 400
    assert resp.json()["error"] == "Invalid JSON body"


@pytest.mark.asyncio
async def test_memory_settings_put_backend_error(client):
    mock = _make_backend()
    mock.update_settings.side_effect = RuntimeError("update fail")
    with _patch_backend(mock):
        resp = await client.put(
            "/api/memory/settings",
            json={"default_strategy": "fast"},
        )
    assert resp.status_code == 500
    assert "error" in resp.json()


# ---------------------------------------------------------------------------
# GET /api/memory/backend/capabilities
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backend_capabilities_returns_200(client):
    mock_cls = AsyncMock(spec=TaOSmdBackend)
    mock_cls.name = "taosmd"
    mock_cls.version = "0.4.0"
    mock_cls.capabilities = ["kg", "vector", "archive"]
    with _patch_import_backend(mock_cls):
        resp = await client.get("/api/memory/backend/capabilities")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_backend_capabilities_shape(client):
    mock_cls = AsyncMock(spec=TaOSmdBackend)
    mock_cls.name = "taosmd"
    mock_cls.version = "0.4.0"
    mock_cls.capabilities = ["kg", "vector", "archive"]
    with _patch_import_backend(mock_cls):
        resp = await client.get("/api/memory/backend/capabilities")
    data = resp.json()
    assert isinstance(data, dict)
    assert "name" in data
    assert "version" in data
    assert "capabilities" in data
    assert isinstance(data["capabilities"], list)


@pytest.mark.asyncio
async def test_backend_capabilities_error(client):
    """When TaOSmdBackend import/attribute access fails, route returns 500."""
    mock_cls = AsyncMock(spec=TaOSmdBackend)
    # Simulate failure when accessing class attributes (name/version/capabilities)
    type(mock_cls).name = PropertyMock(side_effect=RuntimeError("import fail"))
    with _patch_import_backend(mock_cls):
        resp = await client.get("/api/memory/backend/capabilities")
    assert resp.status_code == 500
    assert "error" in resp.json()


# ---------------------------------------------------------------------------
# GET /api/memory/backend/settings-schema
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_settings_schema_returns_200(client):
    mock = _make_backend()
    with _patch_backend(mock):
        resp = await client.get("/api/memory/backend/settings-schema")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_settings_schema_has_type_and_properties(client):
    mock = _make_backend()
    with _patch_backend(mock):
        resp = await client.get("/api/memory/backend/settings-schema")
    data = resp.json()
    assert isinstance(data, dict)
    assert data["type"] == "object"
    assert "properties" in data


@pytest.mark.asyncio
async def test_settings_schema_backend_error(client):
    mock = _make_backend()
    mock.get_settings_schema.side_effect = RuntimeError("schema fail")
    with _patch_backend(mock):
        resp = await client.get("/api/memory/backend/settings-schema")
    assert resp.status_code == 500
    assert "error" in resp.json()


# ---------------------------------------------------------------------------
# GET /api/agents/{name}/memory-config
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_memory_config_get_returns_200(client):
    mock = _make_backend()
    with _patch_backend(mock):
        resp = await client.get("/api/agents/test-agent/memory-config")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_agent_memory_config_get_has_strategy(client):
    mock = _make_backend()
    with _patch_backend(mock):
        resp = await client.get("/api/agents/test-agent/memory-config")
    data = resp.json()
    assert isinstance(data, dict)
    assert "strategy" in data


@pytest.mark.asyncio
async def test_agent_memory_config_get_error(client):
    mock = _make_backend()
    mock.get_agent_config.side_effect = RuntimeError("config fail")
    with _patch_backend(mock):
        resp = await client.get("/api/agents/test-agent/memory-config")
    assert resp.status_code == 500
    assert "error" in resp.json()


# ---------------------------------------------------------------------------
# PUT /api/agents/{name}/memory-config
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_memory_config_put_returns_200(client):
    mock = _make_backend()
    with _patch_backend(mock):
        resp = await client.put(
            "/api/agents/test-agent/memory-config",
            json={"strategy": "fast"},
        )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_agent_memory_config_put_returns_config(client):
    mock = _make_backend()
    with _patch_backend(mock):
        resp = await client.put(
            "/api/agents/test-agent/memory-config",
            json={"strategy": "fast"},
        )
    data = resp.json()
    assert isinstance(data, dict)
    assert "strategy" in data


@pytest.mark.asyncio
async def test_agent_memory_config_put_invalid_json(client):
    mock = _make_backend()
    with _patch_backend(mock):
        resp = await client.put(
            "/api/agents/test-agent/memory-config",
            content=b"not-json",
            headers={"content-type": "application/json"},
        )
    assert resp.status_code == 400
    assert resp.json()["error"] == "Invalid JSON body"


@pytest.mark.asyncio
async def test_agent_memory_config_put_error(client):
    mock = _make_backend()
    mock.update_agent_config.side_effect = RuntimeError("update fail")
    with _patch_backend(mock):
        resp = await client.put(
            "/api/agents/test-agent/memory-config",
            json={"strategy": "fast"},
        )
    assert resp.status_code == 500
    assert "error" in resp.json()


# ---------------------------------------------------------------------------
# GET /api/memory/recipes/schema
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recipes_schema_returns_200(client):
    mock = _make_backend()
    with _patch_backend(mock):
        resp = await client.get("/api/memory/recipes/schema")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_recipes_schema_is_json_schema(client):
    mock = _make_backend()
    with _patch_backend(mock):
        resp = await client.get("/api/memory/recipes/schema")
    data = resp.json()
    assert isinstance(data, dict)


@pytest.mark.asyncio
async def test_recipes_schema_error(client):
    mock = _make_backend()
    mock.get_recipe_schema.side_effect = RuntimeError("schema fail")
    with _patch_backend(mock):
        resp = await client.get("/api/memory/recipes/schema")
    assert resp.status_code == 500
    assert "error" in resp.json()


# ---------------------------------------------------------------------------
# GET /api/memory/recipes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recipes_list_returns_200(client):
    mock = _make_backend()
    with _patch_backend(mock):
        resp = await client.get("/api/memory/recipes")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_recipes_list_is_list(client):
    mock = _make_backend()
    with _patch_backend(mock):
        resp = await client.get("/api/memory/recipes")
    data = resp.json()
    assert isinstance(data, list)


@pytest.mark.asyncio
async def test_recipes_list_error(client):
    mock = _make_backend()
    mock.list_recipes.side_effect = RuntimeError("list fail")
    with _patch_backend(mock):
        resp = await client.get("/api/memory/recipes")
    assert resp.status_code == 500
    assert "error" in resp.json()


# ---------------------------------------------------------------------------
# GET /api/memory/recipes/{recipe_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recipe_get_returns_200(client):
    mock = _make_backend()
    with _patch_backend(mock):
        resp = await client.get("/api/memory/recipes/default")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_recipe_get_has_id(client):
    mock = _make_backend()
    with _patch_backend(mock):
        resp = await client.get("/api/memory/recipes/default")
    data = resp.json()
    assert isinstance(data, dict)
    assert "id" in data


@pytest.mark.asyncio
async def test_recipe_get_not_found(client):
    mock = _make_backend()
    mock.get_recipe.return_value = None
    with _patch_backend(mock):
        resp = await client.get("/api/memory/recipes/nonexistent")
    assert resp.status_code == 404
    assert "error" in resp.json()


@pytest.mark.asyncio
async def test_recipe_get_error(client):
    mock = _make_backend()
    mock.get_recipe.side_effect = RuntimeError("get fail")
    with _patch_backend(mock):
        resp = await client.get("/api/memory/recipes/default")
    assert resp.status_code == 500
    assert "error" in resp.json()


# ---------------------------------------------------------------------------
# POST /api/memory/recipes/{recipe_id}/apply
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recipe_apply_returns_200(client):
    mock = _make_backend()
    with _patch_backend(mock):
        resp = await client.post("/api/memory/recipes/default/apply", json={})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_recipe_apply_returns_applied_id(client):
    mock = _make_backend()
    with _patch_backend(mock):
        resp = await client.post("/api/memory/recipes/default/apply", json={})
    data = resp.json()
    assert isinstance(data, dict)
    assert "applied_recipe_id" in data


@pytest.mark.asyncio
async def test_recipe_apply_with_agent(client):
    mock = _make_backend()
    with _patch_backend(mock):
        resp = await client.post(
            "/api/memory/recipes/default/apply",
            json={"agent": "test-agent"},
        )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_recipe_apply_not_found(client):
    mock = _make_backend()
    mock.apply_recipe.side_effect = ValueError("Recipe 'bad-id' not found")
    with _patch_backend(mock):
        resp = await client.post("/api/memory/recipes/bad-id/apply", json={})
    assert resp.status_code == 404
    assert "error" in resp.json()


@pytest.mark.asyncio
async def test_recipe_apply_error(client):
    mock = _make_backend()
    mock.apply_recipe.side_effect = RuntimeError("apply fail")
    with _patch_backend(mock):
        resp = await client.post("/api/memory/recipes/default/apply", json={})
    assert resp.status_code == 500
    assert "error" in resp.json()


# ---------------------------------------------------------------------------
# POST /api/memory/recipes/recommend
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recipes_recommend_returns_200(client):
    mock = _make_backend()
    with _patch_backend(mock):
        resp = await client.post("/api/memory/recipes/recommend", json={})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_recipes_recommend_is_list(client):
    mock = _make_backend()
    with _patch_backend(mock):
        resp = await client.post("/api/memory/recipes/recommend", json={})
    data = resp.json()
    assert isinstance(data, list)


@pytest.mark.asyncio
async def test_recipes_recommend_with_device_info(client):
    mock = _make_backend()
    with _patch_backend(mock):
        resp = await client.post(
            "/api/memory/recipes/recommend",
            json={"device_info": {"host": {}, "cluster": {"online_workers": 0, "workers": [], "aggregate": {}}}},
        )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_recipes_recommend_error(client):
    mock = _make_backend()
    mock.recommend.side_effect = RuntimeError("recommend fail")
    with _patch_backend(mock):
        resp = await client.post("/api/memory/recipes/recommend", json={})
    assert resp.status_code == 500
    assert "error" in resp.json()


# ---------------------------------------------------------------------------
# POST /api/memory/recipes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_recipe_create_returns_501(client):
    mock = _make_backend()
    with _patch_backend(mock):
        resp = await client.post(
            "/api/memory/recipes",
            json={"name": "custom"},
        )
    assert resp.status_code == 501


@pytest.mark.asyncio
async def test_recipe_create_invalid_json(client):
    mock = _make_backend()
    with _patch_backend(mock):
        resp = await client.post(
            "/api/memory/recipes",
            content=b"not-json",
            headers={"content-type": "application/json"},
        )
    assert resp.status_code == 400
    assert resp.json()["error"] == "Invalid JSON body"


@pytest.mark.asyncio
async def test_recipe_create_error(client):
    mock = _make_backend()
    mock.create_recipe.side_effect = RuntimeError("create fail")
    with _patch_backend(mock):
        resp = await client.post(
            "/api/memory/recipes",
            json={"name": "custom"},
        )
    assert resp.status_code == 500
    assert "error" in resp.json()

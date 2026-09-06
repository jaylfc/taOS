"""Endpoint tests for tinyagentos/routes/admin_prompts.py."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_list_prompts_returns_200(client):
    resp = await client.get("/api/admin-prompts")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_list_prompts_has_prompts_key(client):
    data = (await client.get("/api/admin-prompts")).json()
    assert "prompts" in data
    assert isinstance(data["prompts"], list)


@pytest.mark.asyncio
async def test_list_prompts_each_entry_has_required_keys(client):
    data = (await client.get("/api/admin-prompts")).json()
    for entry in data["prompts"]:
        assert "name" in entry
        assert "summary" in entry
        assert "version" in entry
        assert "required_variables" in entry


@pytest.mark.asyncio
async def test_list_prompts_does_not_include_body(client):
    data = (await client.get("/api/admin-prompts")).json()
    for entry in data["prompts"]:
        assert "body" not in entry


@pytest.mark.asyncio
async def test_get_prompt_returns_200(client):
    resp = await client.get("/api/admin-prompts/health-report")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_get_prompt_shape(client):
    data = (await client.get("/api/admin-prompts/health-report")).json()
    for key in ("name", "summary", "version", "required_variables", "body"):
        assert key in data, f"missing key: {key}"


@pytest.mark.asyncio
async def test_get_prompt_body_is_non_empty_string(client):
    data = (await client.get("/api/admin-prompts/health-report")).json()
    assert isinstance(data["body"], str)
    assert len(data["body"]) > 0


@pytest.mark.asyncio
async def test_get_prompt_name_matches_meta(client):
    data = (await client.get("/api/admin-prompts/health-report")).json()
    assert data["name"] == "health-report"


@pytest.mark.asyncio
async def test_get_prompt_not_found(client):
    resp = await client.get("/api/admin-prompts/does-not-exist")
    assert resp.status_code == 404
    data = resp.json()
    assert "error" in data


@pytest.mark.asyncio
async def test_get_prompt_rejects_dotdot(client):
    """A name containing '..' is rejected with 400."""
    resp = await client.get("/api/admin-prompts/..etc")
    assert resp.status_code == 400
    data = resp.json()
    assert "error" in data


@pytest.mark.asyncio
async def test_get_prompt_rejects_slash(client):
    """A name containing '/' does not match the single-segment path param."""
    resp = await client.get("/api/admin-prompts/foo" + "/" + "bar")
    assert resp.status_code == 404

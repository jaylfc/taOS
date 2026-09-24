"""The LLM gateway is not mounted unless TAOS_LLM_GATEWAY is exactly "1".

Uses a signed-in client: without credentials the auth middleware answers 401
for every unknown path (anti-enumeration), which would hide whether the route
exists. With a session, an unmounted route is a plain 404.
"""
from __future__ import annotations

import pytest

from tinyagentos.app import create_app


@pytest.fixture(params=[None, "0", "", "true", "yes"], ids=["unset", "0", "empty", "true", "yes"])
def app(request, tmp_data_dir, monkeypatch):
    if request.param is None:
        monkeypatch.delenv("TAOS_LLM_GATEWAY", raising=False)
    else:
        monkeypatch.setenv("TAOS_LLM_GATEWAY", request.param)
    return create_app(data_dir=tmp_data_dir)


@pytest.mark.asyncio
async def test_gateway_routes_are_404_when_the_flag_is_off(client):
    models = await client.get("/api/llm/v1/models")
    chat = await client.post(
        "/api/llm/v1/chat/completions",
        json={"model": "taos-default", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert models.status_code == 404, models.text
    assert chat.status_code == 404, chat.text
    paths = {getattr(r, "path", "") for r in client._transport.app.routes}
    assert not any(p.startswith("/api/llm/") for p in paths)


@pytest.mark.asyncio
async def test_flag_on_mounts_the_routes(tmp_data_dir, monkeypatch):
    """Control for the test above: the same probe sees the routes when on."""
    monkeypatch.setenv("TAOS_LLM_GATEWAY", "1")
    app = create_app(data_dir=tmp_data_dir)
    paths = {getattr(r, "path", "") for r in app.routes}
    assert {"/api/llm/v1/models", "/api/llm/v1/chat/completions"} <= paths

"""The LLM gateway is not mounted unless TAOS_LLM_GATEWAY is exactly "1".

Mounting is decided at create_app, so each flag value needs its own app; no
other state is shared, so these tests are order-free by construction. The
probe authenticates with the host local token: without credentials the auth
middleware answers 401 for every unknown path (anti-enumeration), which would
hide whether the route exists. Authenticated, an unmounted route is a 404.
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from tinyagentos.app import create_app


def _build(tmp_data_dir, monkeypatch, flag):
    if flag is None:
        monkeypatch.delenv("TAOS_LLM_GATEWAY", raising=False)
    else:
        monkeypatch.setenv("TAOS_LLM_GATEWAY", flag)
    app = create_app(data_dir=tmp_data_dir)
    app.state._startup_complete = True
    return app


@pytest.mark.asyncio
@pytest.mark.parametrize("flag", [None, "0", "", "true", "yes"],
                         ids=["unset", "0", "empty", "true", "yes"])
async def test_gateway_routes_are_404_when_the_flag_is_off(tmp_data_dir, monkeypatch, flag):
    app = _build(tmp_data_dir, monkeypatch, flag)
    token = app.state.auth.get_local_token()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                           headers={"Authorization": f"Bearer {token}"}) as c:
        models = await c.get("/api/llm/v1/models")
        chat = await c.post(
            "/api/llm/v1/chat/completions",
            json={"model": "taos-default", "messages": [{"role": "user", "content": "hi"}]},
        )
    assert models.status_code == 404, models.text
    assert chat.status_code == 404, chat.text
    paths = {getattr(r, "path", "") for r in app.routes}
    assert not any(p.startswith("/api/llm/") for p in paths)


@pytest.mark.asyncio
async def test_flag_on_mounts_the_routes(tmp_data_dir, monkeypatch):
    """Control for the test above: the same probe sees the routes when on."""
    app = _build(tmp_data_dir, monkeypatch, "1")
    paths = {getattr(r, "path", "") for r in app.routes}
    assert {"/api/llm/v1/models", "/api/llm/v1/chat/completions"} <= paths
    token = app.state.auth.get_local_token()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                           headers={"Authorization": f"Bearer {token}"}) as c:
        models = await c.get("/api/llm/v1/models")
    assert models.status_code != 404, models.text

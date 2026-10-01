"""The LLM gateway is ALWAYS mounted: TAOS_LLM_GATEWAY=0/false/no/off is a logged no-op.

It used to unmount the gateway and roll agents back to LiteLLM. LiteLLM is gone
(removal stage 2b-2a), so there is nothing to roll back to.

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
@pytest.mark.parametrize("flag", ["0", "false", "no", "off", " OFF "],
                         ids=["0", "false", "no", "off", "OFF-padded"])
async def test_off_flag_is_a_logged_no_op(tmp_data_dir, monkeypatch, caplog, flag):
    import tinyagentos.llm_gateway as llm_gateway

    monkeypatch.setattr(llm_gateway, "_off_warned", False)
    with caplog.at_level("WARNING", logger="tinyagentos.llm_gateway"):
        app = _build(tmp_data_dir, monkeypatch, flag)
    paths = {getattr(r, "path", "") for r in app.routes}
    assert {"/api/llm/v1/models", "/api/llm/v1/chat/completions"} <= paths
    token = app.state.auth.get_local_token()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                           headers={"Authorization": f"Bearer {token}"}) as c:
        models = await c.get("/api/llm/v1/models")
    assert models.status_code != 404, models.text
    assert llm_gateway.enabled() is True
    assert any("is ignored" in r.getMessage() for r in caplog.records), caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("flag", [None, "1", "", "true", "yes"], ids=["unset", "1", "empty", "true", "yes"])
async def test_flag_on_mounts_the_routes(tmp_data_dir, monkeypatch, flag):
    """The same probe with the flag unset or truthy: mounted as well."""
    app = _build(tmp_data_dir, monkeypatch, flag)
    paths = {getattr(r, "path", "") for r in app.routes}
    assert {"/api/llm/v1/models", "/api/llm/v1/chat/completions"} <= paths
    token = app.state.auth.get_local_token()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test",
                           headers={"Authorization": f"Bearer {token}"}) as c:
        models = await c.get("/api/llm/v1/models")
    assert models.status_code != 404, models.text

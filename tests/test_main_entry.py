"""Verify python -m tinyagentos respects TAOS_HOST/TAOS_PORT env vars."""
from __future__ import annotations

from unittest.mock import patch


def test_main_uses_env_host_port(monkeypatch):
    monkeypatch.setenv("TAOS_HOST", "127.0.0.1")
    monkeypatch.setenv("TAOS_PORT", "7117")
    # Disable dual-port: these tests only exercise host/port env-var resolution.
    monkeypatch.setenv("TAOS_BROWSER_PROXY_PORT", "0")
    monkeypatch.setenv("TAOS_LLM_GATEWAY_PORT", "0")  # and the gateway agent listener
    from tinyagentos import __main__ as m

    captured = {}

    def fake_run(app, host, port, **kwargs):
        captured["host"] = host
        captured["port"] = port

    with patch("uvicorn.run", side_effect=fake_run), \
         patch.object(m, "create_app", return_value=object()):
        m.main()

    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 7117


def test_main_falls_back_to_config_when_env_unset(monkeypatch):
    monkeypatch.delenv("TAOS_HOST", raising=False)
    monkeypatch.delenv("TAOS_PORT", raising=False)
    # Disable dual-port: these tests only exercise host/port env-var resolution.
    monkeypatch.setenv("TAOS_BROWSER_PROXY_PORT", "0")
    monkeypatch.setenv("TAOS_LLM_GATEWAY_PORT", "0")  # and the gateway agent listener
    from tinyagentos import __main__ as m

    captured = {}

    def fake_run(app, host, port, **kwargs):
        captured["host"] = host
        captured["port"] = port

    with patch("uvicorn.run", side_effect=fake_run), \
         patch.object(m, "create_app", return_value=object()):
        m.main()

    assert captured["host"] == "0.0.0.0"
    assert captured["port"] == 6969


def _run_main_capturing_serve(monkeypatch, gateway_flag):
    from types import SimpleNamespace

    monkeypatch.setenv("TAOS_HOST", "127.0.0.1")
    monkeypatch.setenv("TAOS_PORT", "7117")
    monkeypatch.setenv("TAOS_BROWSER_PROXY_PORT", "0")
    monkeypatch.delenv("TAOS_LLM_GATEWAY_PORT", raising=False)
    if gateway_flag is None:
        monkeypatch.delenv("TAOS_LLM_GATEWAY", raising=False)
    else:
        monkeypatch.setenv("TAOS_LLM_GATEWAY", gateway_flag)
    from tinyagentos import __main__ as m

    app = SimpleNamespace(state=SimpleNamespace())
    served = {}
    with patch("uvicorn.run", side_effect=lambda *a, **k: served.setdefault("uvicorn_run", k)), \
         patch.object(m, "_serve_dual_port", side_effect=lambda *a, **k: served.setdefault("multi", k)), \
         patch.object(m, "create_app", return_value=app):
        m.main()
    return app, served


def test_main_serves_the_gateway_agent_listener_by_default(monkeypatch):
    app, served = _run_main_capturing_serve(monkeypatch, None)
    assert "uvicorn_run" not in served
    assert served["multi"]["gateway_port"] == 7838
    assert served["multi"]["proxy_port"] == 0
    assert app.state.llm_gateway_agent_port == 7838


def test_gateway_listener_binds_loopback_only_without_lifespan(monkeypatch):
    from types import SimpleNamespace

    from tinyagentos import __main__ as m

    configs = []

    class _Cfg:
        def __init__(self, app, **kw):
            configs.append(kw)

    def fake_asyncio_run(coro):
        coro.close()
        return True

    app = SimpleNamespace(state=SimpleNamespace(llm_proxy=SimpleNamespace(port=7834)))
    with patch("uvicorn.Config", _Cfg), patch("uvicorn.Server.__init__", lambda self, config: None), \
         patch("asyncio.run", fake_asyncio_run):
        m._serve_dual_port(app, host="0.0.0.0", port=6969, proxy_port=0, gateway_port=7838)
    gw = [c for c in configs if c.get("port") == 7838]
    assert len(gw) == 1 and gw[0]["host"] == "127.0.0.1" and gw[0]["lifespan"] == "off"
    assert [c["port"] for c in configs] == [6969, 7838]  # no browser-proxy server


def test_main_gateway_off_records_the_port_for_rollback_but_serves_no_listener(monkeypatch):
    app, served = _run_main_capturing_serve(monkeypatch, "0")
    assert "multi" not in served and "uvicorn_run" in served
    # Recorded so the startup reconcile can point agents back at LiteLLM.
    assert app.state.llm_gateway_agent_port == 7838

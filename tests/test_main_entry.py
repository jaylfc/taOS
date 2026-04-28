"""Verify python -m tinyagentos respects TAOS_HOST/TAOS_PORT env vars."""
from __future__ import annotations

from unittest.mock import patch


def test_main_uses_env_host_port(monkeypatch):
    monkeypatch.setenv("TAOS_HOST", "127.0.0.1")
    monkeypatch.setenv("TAOS_PORT", "7117")
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

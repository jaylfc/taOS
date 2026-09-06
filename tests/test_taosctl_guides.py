"""Tests for taosctl guides command group: dispatch, arg parsing, and exit codes."""
from __future__ import annotations

import pytest

from tinyagentos.cli.taosctl import __main__ as cli_main


class _FakeClient:
    def __init__(self, *a, **k):
        self.calls = []
        self.base_url = "http://x"
        self.token = "t"
        self._raise = None

    def get(self, path, params=None):
        self.calls.append(("GET", path))
        if self._raise:
            raise self._raise
        if "/api/guides/recommendations" in path:
            return {"hardware": params["hardware"], "use_case": params["use_case"], "recommendations": []}
        if "/api/guides/tiers" in path:
            return {"tiers": {"pi-16gb": {"label": "Raspberry Pi 16 GB"}}}
        if "/api/guides/use-cases" in path:
            return {"use_cases": {"chat": {"label": "Chat"}}}
        return {}

    def post(self, path, body=None, params=None):
        self.calls.append(("POST", path))

    def patch(self, path, body=None):
        self.calls.append(("PATCH", path))

    def delete(self, path, params=None):
        self.calls.append(("DELETE", path))


def _run(monkeypatch, argv, fake):
    monkeypatch.setattr(cli_main, "TaosClient", lambda **k: fake)
    return cli_main.main(argv)


def test_guides_recommendations_calls_endpoint(monkeypatch, capsys):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["guides", "recommendations", "nvidia-12gb", "coding"], fake)
    assert rc == 0
    assert ("GET", "/api/guides/recommendations") in fake.calls
    out = capsys.readouterr().out
    assert "nvidia-12gb" in out


def test_guides_tiers_calls_endpoint(monkeypatch, capsys):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["--json", "guides", "tiers"], fake)
    assert rc == 0
    assert ("GET", "/api/guides/tiers") in fake.calls
    out = capsys.readouterr().out
    assert "pi-16gb" in out


def test_guides_use_cases_calls_endpoint(monkeypatch, capsys):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["--json", "guides", "use-cases"], fake)
    assert rc == 0
    assert ("GET", "/api/guides/use-cases") in fake.calls
    out = capsys.readouterr().out
    assert "chat" in out


def test_guides_is_discovered():
    from tinyagentos.cli.taosctl.commands import iter_noun_modules

    nouns = {m.NOUN for m in iter_noun_modules()}
    assert "guides" in nouns

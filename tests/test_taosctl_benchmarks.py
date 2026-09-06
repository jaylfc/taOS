"""Tests for the taosctl benchmarks command group: each verb hits the right path."""
from __future__ import annotations

from tinyagentos.cli.taosctl import __main__ as cli_main


class _FakeClient:
    def __init__(self, *a, **k):
        self.calls = []
        self.base_url = "http://x"
        self.token = "t"

    def get(self, path, params=None):
        self.calls.append(("GET", path, params))
        return {"items": []}


def _run(monkeypatch, argv, fake):
    monkeypatch.setattr(cli_main, "TaosClient", lambda **k: fake)
    return cli_main.main(argv)


def test_worker_passes_limit(monkeypatch):
    fake = _FakeClient()
    assert _run(monkeypatch, ["benchmarks", "worker", "w1", "--limit", "10"], fake) == 0
    method, path, params = fake.calls[0]
    assert (method, path) == ("GET", "/api/workers/w1/benchmark")
    assert params == {"limit": 10}


def test_worker_default_limit(monkeypatch):
    fake = _FakeClient()
    assert _run(monkeypatch, ["benchmarks", "worker", "w1"], fake) == 0
    method, path, params = fake.calls[0]
    assert (method, path) == ("GET", "/api/workers/w1/benchmark")
    assert params == {"limit": 100}


def test_leaderboard_no_metric(monkeypatch):
    fake = _FakeClient()
    assert _run(monkeypatch, ["benchmarks", "leaderboard", "chat"], fake) == 0
    method, path, params = fake.calls[0]
    assert (method, path) == ("GET", "/api/benchmarks/capability/chat")
    assert params is None


def test_leaderboard_with_metric(monkeypatch):
    fake = _FakeClient()
    assert _run(monkeypatch, ["benchmarks", "leaderboard", "chat", "--metric", "tps"], fake) == 0
    method, path, params = fake.calls[0]
    assert (method, path) == ("GET", "/api/benchmarks/capability/chat")
    assert params == {"metric": "tps"}

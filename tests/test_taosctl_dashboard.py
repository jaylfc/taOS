"""Tests for the taosctl dashboard command group: each verb hits the right path."""
from __future__ import annotations

import pytest

from tinyagentos.cli.taosctl import __main__ as cli_main


class _FakeClient:
    def __init__(self, *a, **k):
        self.calls = []
        self.base_url = "http://x"
        self.token = "t"

    def get(self, path, params=None):
        self.calls.append(("GET", path, params))
        return {"ok": True}


def _run(monkeypatch, argv, fake):
    monkeypatch.setattr(cli_main, "TaosClient", lambda **k: fake)
    return cli_main.main(argv)


def _paths(fake):
    return [(m, p) for (m, p, *_rest) in fake.calls]


def test_capabilities(monkeypatch):
    fake = _FakeClient()
    assert _run(monkeypatch, ["dashboard", "capabilities"], fake) == 0
    assert ("GET", "/api/capabilities") in _paths(fake)


def test_health_plain(monkeypatch):
    fake = _FakeClient()
    assert _run(monkeypatch, ["dashboard", "health"], fake) == 0
    method, path, params = fake.calls[0]
    assert (method, path) == ("GET", "/api/health")
    assert params is None


def test_health_detailed(monkeypatch):
    fake = _FakeClient()
    assert _run(monkeypatch, ["dashboard", "health", "--detailed"], fake) == 0
    method, path, params = fake.calls[0]
    assert (method, path) == ("GET", "/api/health")
    assert params == {"detailed": True}


def test_version(monkeypatch):
    fake = _FakeClient()
    assert _run(monkeypatch, ["dashboard", "version"], fake) == 0
    assert ("GET", "/api/version") in _paths(fake)


def test_system(monkeypatch):
    fake = _FakeClient()
    assert _run(monkeypatch, ["dashboard", "system"], fake) == 0
    assert ("GET", "/api/system") in _paths(fake)


def test_cluster_summary(monkeypatch):
    fake = _FakeClient()
    assert _run(monkeypatch, ["dashboard", "cluster-summary"], fake) == 0
    assert ("GET", "/api/dashboard/cluster-summary") in _paths(fake)


def test_backends(monkeypatch):
    fake = _FakeClient()
    assert _run(monkeypatch, ["dashboard", "backends"], fake) == 0
    assert ("GET", "/api/backends") in _paths(fake)


def test_activity_default_limit(monkeypatch):
    fake = _FakeClient()
    assert _run(monkeypatch, ["dashboard", "activity"], fake) == 0
    method, path, params = fake.calls[0]
    assert (method, path) == ("GET", "/api/dashboard/activity")
    assert params == {"limit": 15}


def test_activity_custom_limit(monkeypatch):
    fake = _FakeClient()
    assert _run(monkeypatch, ["dashboard", "activity", "--limit", "5"], fake) == 0
    method, path, params = fake.calls[0]
    assert params == {"limit": 5}


def test_metrics_passes_name_and_range(monkeypatch):
    fake = _FakeClient()
    assert _run(monkeypatch, ["dashboard", "metrics", "cpu", "--range", "7d"], fake) == 0
    method, path, params = fake.calls[0]
    assert (method, path) == ("GET", "/api/metrics/cpu")
    assert params == {"range": "7d"}


def test_metrics_default_range(monkeypatch):
    fake = _FakeClient()
    assert _run(monkeypatch, ["dashboard", "metrics", "cpu"], fake) == 0
    method, path, params = fake.calls[0]
    assert params == {"range": "24h"}


def test_activity_rejects_non_positive_limit(monkeypatch):
    fake = _FakeClient()
    # argparse rejects 0/negative at parse time (exit 2) rather than forwarding
    # an invalid limit to the server.
    with pytest.raises(SystemExit):
        _run(monkeypatch, ["dashboard", "activity", "--limit", "0"], fake)
    assert fake.calls == []

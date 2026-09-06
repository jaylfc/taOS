"""Tests for the taosctl scheduler command group."""
from __future__ import annotations

import json

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


def test_scheduler_stats_hits_stats_endpoint(monkeypatch, capsys):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["--json", "scheduler", "stats"], fake)
    assert rc == 0
    assert ("GET", "/api/scheduler/stats", None) in fake.calls


def test_scheduler_tasks_hits_tasks_endpoint(monkeypatch, capsys):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["--json", "scheduler", "tasks"], fake)
    assert rc == 0
    assert ("GET", "/api/scheduler/tasks", None) in fake.calls


def test_scheduler_tasks_passes_limit_param(monkeypatch, capsys):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["--json", "scheduler", "tasks", "--limit", "50"], fake)
    assert rc == 0
    calls_for_tasks = [c for c in fake.calls if c[1] == "/api/scheduler/tasks"]
    assert len(calls_for_tasks) == 1
    assert calls_for_tasks[0][2] == {"limit": 50}


def test_scheduler_backends_hits_backends_endpoint(monkeypatch, capsys):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["--json", "scheduler", "backends"], fake)
    assert rc == 0
    assert ("GET", "/api/scheduler/backends", None) in fake.calls


def test_scheduler_verbs_return_data(monkeypatch, capsys):
    fake = _FakeClient()
    rc = _run(monkeypatch, ["--json", "scheduler", "stats"], fake)
    data = json.loads(capsys.readouterr().out)
    assert data == {"ok": True}

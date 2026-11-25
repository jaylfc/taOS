"""taosctl storage: the real parser and dispatcher against a recording client."""
from __future__ import annotations

import pytest

from tinyagentos.cli.taosctl import __main__ as cli_main

DISK = "/dev/disk/by-id/ata-FAKE_1"


class FakeClient:
    def __init__(self):
        self.calls = []

    def get(self, path, params=None):
        self.calls.append(("GET", path, params))
        return {"ok": True}

    def post(self, path, body=None, params=None, json=None):
        self.calls.append(("POST", path, body))
        return {"ok": True}

    def put(self, path, body=None, json=None):
        self.calls.append(("PUT", path, body if body is not None else json))
        return {"ok": True}


def run(monkeypatch, argv):
    fake = FakeClient()
    monkeypatch.setattr(cli_main, "TaosClient", lambda **k: fake)
    return cli_main.main(argv), fake.calls


def test_disks_and_areas_and_status(monkeypatch):
    for verb, path in (("disks", "/api/storage/disks"), ("areas", "/api/storage/areas"), ("status", "/api/storage/status")):
        rc, calls = run(monkeypatch, ["storage", verb])
        assert rc == 0 and calls == [("GET", path, None)]


def test_add_sends_exactly_what_was_typed(monkeypatch):
    rc, calls = run(monkeypatch, ["storage", "add", "--disk", DISK, "--name", "photos", "--kind", "shared",
                                  "--confirm", "FAKE 1 SER"])
    assert rc == 0
    assert calls == [("POST", "/api/storage/areas", {"disk": DISK, "name": "photos", "kind": "shared", "confirm": "FAKE 1 SER"})]


def test_add_defaults_to_private_and_requires_confirm(monkeypatch, capsys):
    rc, calls = run(monkeypatch, ["storage", "add", "--disk", DISK, "--name", "p1", "--confirm", "x"])
    assert calls[0][2]["kind"] == "private"
    with pytest.raises(SystemExit):
        run(monkeypatch, ["storage", "add", "--disk", DISK, "--name", "p1"])
    with pytest.raises(SystemExit):
        run(monkeypatch, ["storage", "add", "--disk", DISK, "--name", "p1", "--kind", "ext4", "--confirm", "x"])


def test_set_primary_and_primary(monkeypatch):
    rc, calls = run(monkeypatch, ["storage", "set-primary", "--person", "u1", "--area", "photos"])
    assert calls == [("PUT", "/api/storage/primary", {"person": "u1", "area": "photos"})]
    rc, calls = run(monkeypatch, ["storage", "primary", "--person", "u1"])
    assert calls == [("GET", "/api/storage/primary", {"person": "u1"})]
    rc, calls = run(monkeypatch, ["storage", "primary"])
    assert calls == [("GET", "/api/storage/primary", None)]


def test_agent_verbs(monkeypatch):
    rc, calls = run(monkeypatch, ["storage", "propose", "add", "--disk", DISK, "--name", "photos"])
    assert calls == [("POST", "/api/storage/proposals", {"action": "add", "params": {"disk": DISK, "name": "photos", "kind": "private"}})]
    rc, calls = run(monkeypatch, ["storage", "propose", "set-primary", "--person", "u1", "--area", "photos"])
    assert calls[0][2] == {"action": "set-primary", "params": {"person": "u1", "area": "photos"}}
    rc, calls = run(monkeypatch, ["storage", "approve", "sp-1", "--confirm", "A B"])
    assert calls == [("POST", "/api/storage/proposals/sp-1/approve", {"confirm": "A B"})]
    rc, calls = run(monkeypatch, ["storage", "reject", "sp-1"])
    assert calls[0][1] == "/api/storage/proposals/sp-1/reject"
    rc, calls = run(monkeypatch, ["storage", "proposals"])
    assert calls == [("GET", "/api/storage/proposals", None)]

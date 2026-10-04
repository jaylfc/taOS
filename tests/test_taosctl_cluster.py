"""Tests for `taosctl cluster`: each verb hits the right method + endpoint,
with the expected JSON body for writes."""
from __future__ import annotations

import pytest

from tinyagentos.cli.taosctl import __main__ as cli_main
from tinyagentos.cli.taosctl.commands import iter_noun_modules


class _FakeClient:
    def __init__(self):
        self.calls = []
        self.base_url = "http://x"
        self.token = "t"

    def get(self, path, params=None):
        self.calls.append(("GET", path, params))
        return {"ok": True}

    def post(self, path, body=None, params=None, json=None):
        self.calls.append(("POST", path, body))
        return {"ok": True}

    def patch(self, path, body=None, json=None):
        self.calls.append(("PATCH", path, body))
        return {"ok": True}

    def delete(self, path, params=None):
        self.calls.append(("DELETE", path, params))
        return {"ok": True}


def _run(monkeypatch, argv):
    fake = _FakeClient()
    seen = {}

    def _factory(**k):
        seen.update(k)
        return fake

    monkeypatch.setattr(cli_main, "TaosClient", _factory)
    rc = cli_main.main(["--json", *argv])
    return rc, fake, seen


def test_cluster_noun_is_discovered():
    assert "cluster" in {m.NOUN for m in iter_noun_modules()}


CASES = [
    # workers
    (["workers"], ("GET", "/api/cluster/workers", None)),
    (["remove", "w 1"], ("DELETE", "/api/cluster/workers/w%201", None)),
    (["revoke", "w1"], ("POST", "/api/cluster/workers/w1/revoke", None)),
    (["block", "w1"], ("POST", "/api/cluster/workers/w1/block", None)),
    (["unblock", "w1"], ("POST", "/api/cluster/workers/w1/unblock", None)),
    (["drain", "w1"], ("POST", "/api/cluster/workers/w1/drain", {"graceful": True})),
    (["drain", "w1", "--force"],
     ("POST", "/api/cluster/workers/w1/drain", {"graceful": False})),
    (["cancel-drain", "w1"], ("POST", "/api/cluster/workers/w1/cancel-drain", None)),
    (["update-worker", "w1"], ("POST", "/api/cluster/workers/w1/update", None)),
    (["update-all"], ("POST", "/api/cluster/workers/update-all", None)),
    (["update-status", "job9"], ("GET", "/api/cluster/workers/update-all/job9", None)),
    (["deploy", "w1", "--command", "install-ollama"],
     ("POST", "/api/cluster/workers/w1/deploy", {"command": "install-ollama"})),
    (["remote-command", "w1", "--command", "uptime", "--timeout", "5"],
     ("POST", "/api/cluster/workers/w1/remote", {"command": "uptime", "timeout": 5})),
    (["remote-command", "w1", "--command", "uptime"],
     ("POST", "/api/cluster/workers/w1/remote", {"command": "uptime"})),
    # read-only views
    (["capabilities"], ("GET", "/api/cluster/capabilities", None)),
    (["kv-quant-options"], ("GET", "/api/cluster/kv-quant-options", None)),
    (["backends"], ("GET", "/api/cluster/backends", None)),
    (["optimise"], ("GET", "/api/cluster/optimise", None)),
    (["install-targets"], ("GET", "/api/cluster/install-targets", None)),
    (["map"], ("GET", "/api/cluster/map", None)),
    (["move", "--item", "qwen", "--to", "w2"],
     ("POST", "/api/cluster/move", {"item": "qwen", "to_worker": "w2"})),
    (["move", "--item", "qwen", "--to", "w2", "--from", "w1"],
     ("POST", "/api/cluster/move", {"item": "qwen", "to_worker": "w2", "from_worker": "w1"})),
    (["promote-archived"], ("POST", "/api/cluster/promote-archived", None)),
    # pairing
    (["pairing-pending"], ("GET", "/api/cluster/pairing/pending", None)),
    (["pairing-confirm", "--name", "w1", "--code", "123456"],
     ("POST", "/api/cluster/pairing/confirm", {"name": "w1", "code": "123456"})),
    (["pairing-manual", "--url", "http://w1:9000", "--code", "ABC"],
     ("POST", "/api/cluster/pairing/manual", {"url": "http://w1:9000", "code": "ABC"})),
    # leases
    (["leases"], ("GET", "/api/cluster/leases", None)),
    (["lease-claim", "--resource-id", "gpu0"],
     ("POST", "/api/cluster/leases/claim", {"resource_id": "gpu0"})),
    (["lease-claim", "--resource-id", "gpu0", "--ttl", "12", "--caller", "me",
      "--vram-mb", "2048"],
     ("POST", "/api/cluster/leases/claim",
      {"resource_id": "gpu0", "ttl_seconds": 12.0, "caller": "me", "required_vram_mb": 2048})),
    (["lease-release", "L1"], ("POST", "/api/cluster/leases/release", {"lease_id": "L1"})),
    (["lease-renew", "L1", "--ttl", "20"],
     ("POST", "/api/cluster/leases/renew", {"lease_id": "L1", "ttl_seconds": 20.0})),
    # capability registry
    (["capability-list"], ("GET", "/api/cluster/capability", {"status": None})),
    (["capability-list", "--status", "online"],
     ("GET", "/api/cluster/capability", {"status": "online"})),
    (["capability-status", "n1", "draining"],
     ("POST", "/api/cluster/capability/n1/status", {"status": "draining"})),
    (["capability-prune"], ("POST", "/api/cluster/capability/prune", {})),
    (["capability-prune", "--older-than", "60"],
     ("POST", "/api/cluster/capability/prune", {"older_than_s": 60})),
    (["capability-sweep", "--older-than", "30"],
     ("POST", "/api/cluster/capability/sweep", {"older_than_s": 30})),
    # incus remotes + migration
    (["remotes"], ("GET", "/api/cluster/remotes", None)),
    (["remote-add", "--name", "r1", "--url", "https://r1:8443", "--token", "tok"],
     ("POST", "/api/cluster/remotes", {"name": "r1", "url": "https://r1:8443", "token": "tok"})),
    (["remote-token", "--client-name", "c1", "--projects", "a, b", "--restricted"],
     ("POST", "/api/cluster/remotes/token",
      {"client_name": "c1", "restricted": True, "projects": ["a", "b"]})),
    (["remote-remove", "r1"], ("DELETE", "/api/cluster/remotes/r1", None)),
    (["migrate", "--container", "c1", "--target-remote", "r1"],
     ("POST", "/api/cluster/migrate",
      {"container": "c1", "target_remote": "r1", "keep_source": False, "stateless": True})),
    (["migrate", "--container", "c1", "--target-remote", "r1", "--new-name", "c2",
      "--keep-source", "--stateful", "--timeout", "90"],
     ("POST", "/api/cluster/migrate",
      {"container": "c1", "target_remote": "r1", "keep_source": True, "stateless": False,
       "new_name": "c2", "timeout": 90})),
    (["migrate-service", "--app-id", "gitea", "--target-remote", "r1",
      "--source-remote", "local", "--keep-source"],
     ("POST", "/api/cluster/migrate-service",
      {"app_id": "gitea", "target_remote": "r1", "keep_source": True,
       "source_remote": "local"})),
    # BLE pairing
    (["ble-scan", "--seconds", "3"], ("GET", "/api/cluster/ble/scan", {"seconds": 3.0})),
    (["ble-pair-start", "AA:BB"], ("POST", "/api/cluster/ble/pair/start", {"address": "AA:BB"})),
    (["ble-pair-confirm", "s1"], ("POST", "/api/cluster/ble/pair/confirm", {"session": "s1"})),
    (["ble-pair-cancel", "s1"], ("POST", "/api/cluster/ble/pair/cancel", {"session": "s1"})),
]


@pytest.mark.parametrize("argv,expected", CASES, ids=[" ".join(c[0]) for c in CASES])
def test_cluster_verb_hits_endpoint(monkeypatch, capsys, argv, expected):
    rc, fake, _ = _run(monkeypatch, ["cluster", *argv])
    assert rc == 0
    assert fake.calls == [expected]


def test_verb_url_and_token_options_do_not_clobber_global_flags(monkeypatch, capsys):
    rc, fake, seen = _run(monkeypatch, [
        "--url", "http://server:6969", "--token", "apitok",
        "cluster", "remote-add", "--name", "r1", "--url", "https://r1:8443", "--token", "trust",
    ])
    assert rc == 0
    assert seen == {"url": "http://server:6969", "token": "apitok"}
    assert fake.calls[0][2] == {"name": "r1", "url": "https://r1:8443", "token": "trust"}


def test_pairing_manual_url_does_not_clobber_server_url(monkeypatch, capsys):
    rc, fake, seen = _run(monkeypatch, [
        "--url", "http://server:6969",
        "cluster", "pairing-manual", "--url", "http://w1:9000", "--code", "ABC",
    ])
    assert rc == 0
    assert seen["url"] == "http://server:6969"
    assert fake.calls[0][2] == {"url": "http://w1:9000", "code": "ABC"}

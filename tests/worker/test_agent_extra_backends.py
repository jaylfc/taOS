"""Extra probe candidates per worker (#3232): TAOS_EXTRA_BACKENDS and the
worker manifest's health_url/port. Bad entries are skipped with a warning."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from urllib.parse import urlsplit

import pytest

from tinyagentos.worker import agent as agent_mod
from tinyagentos.worker.agent import (
    WorkerAgent,
    _DEFAULT_PROBE_CANDIDATES,
    _PROBEABLE_TYPES,
    _dedupe_candidates,
    _manifest_entry_url,
    _normalize_probe_url,
    _parse_extra_backends,
)

_DEFAULT_ORIGINS = list(dict.fromkeys(u for _, u in _DEFAULT_PROBE_CANDIDATES))


@pytest.fixture(autouse=True)
def _fresh_warning_cache():
    agent_mod._warned_probe_entries.clear()
    yield
    agent_mod._warned_probe_entries.clear()


class _Resp:
    def __init__(self, body):
        self.status_code = 200
        self._body = body

    def json(self):
        return self._body

    def raise_for_status(self):
        return None


class _FakeHttpx:
    """Stands in for httpx.AsyncClient: an origin answers only if live. Each
    client records its timeout; all share one log of (timeout, url)."""

    def __init__(self, live, delay=0.0, stall=()):
        self.live, self.delay, self.requested = set(live), delay, []
        self.stall, self.posted = set(stall), []

    def __call__(self, timeout=None, **_):
        fake = self

        class _Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def get(self, url):
                fake.requested.append((timeout, url))
                parts = urlsplit(url)
                origin = f"{parts.scheme}://{parts.netloc}"
                if origin in fake.stall:  # answers, but never finishes the body
                    await asyncio.sleep(3600)
                if origin not in fake.live:
                    await asyncio.sleep(fake.delay)
                    raise ConnectionError("connection refused")
                if parts.path in ("/api/tags", "/api/ps"):
                    return _Resp({"models": [{"model": "m:latest", "size": 0}]})
                return _Resp({"data": [{"id": "m"}]})

            async def post(self, url, content=None, headers=None):
                fake.posted.append((url, json.loads(content)))
                return _Resp({"generation": 1})

        return _Client()

    def origins(self):
        return list(dict.fromkeys(
            "{0.scheme}://{0.netloc}".format(urlsplit(u)) for _, u in self.requested
        ))


async def _detect(monkeypatch, live=(), *, extra=None, models=(), delay=0.0, stall=()):
    if extra is None:
        monkeypatch.delenv("TAOS_EXTRA_BACKENDS", raising=False)
    else:
        monkeypatch.setenv("TAOS_EXTRA_BACKENDS", extra)
    manifest = {"resource_id": "", "models": list(models)}
    monkeypatch.setattr("tinyagentos.worker.worker_manifest.load_manifest", lambda: manifest)
    fake = _FakeHttpx(live, delay, stall)
    monkeypatch.setattr("tinyagentos.worker.agent.httpx.AsyncClient", fake)
    return await WorkerAgent("http://localhost:6969").detect_backends(), fake


def _summary(backends):
    return [(b["name"], b["url"], b["status"]) for b in backends]


# --- parsing and validation -------------------------------------------------


def test_parses_pairs_normalising_case_space_and_trailing_slash():
    raw = " Ollama=HTTP://192.168.1.20:11434/ ,, llama-cpp=http://127.0.0.1:9000"
    assert _parse_extra_backends(raw) == [
        ("ollama", "http://192.168.1.20:11434"),
        ("llama-cpp", "http://127.0.0.1:9000"),
    ]
    assert _parse_extra_backends("") == []


@pytest.mark.parametrize("entry", [
    "ollama", "=http://127.0.0.1:1", "ollama=", "ollama=http://127.0.0.1:abc",
    "openai=http://127.0.0.1:1234",    # cloud type, not a local probeable server
    "comfyui=http://127.0.0.1:8188",   # serves none of the probed paths
    "lmstudio=http://127.0.0.1:1234",  # not in the catalog
])
def test_bad_entry_skipped_and_warned_once(entry, caplog):
    caplog.set_level(logging.WARNING)
    for _ in range(3):  # re-read on every heartbeat
        assert _parse_extra_backends(f"{entry},ollama=http://10.0.0.5:11434") == [
            ("ollama", "http://10.0.0.5:11434"),
        ]
    assert len(caplog.records) == 1
    assert "TAOS_EXTRA_BACKENDS" in caplog.records[0].getMessage()


def test_probeable_types_are_local_catalog_types():
    from tinyagentos.providers import CLOUD_TYPES
    from tinyagentos.scheduler.backend_catalog import BACKEND_CAPABILITIES

    assert _PROBEABLE_TYPES <= set(BACKEND_CAPABILITIES)
    assert not _PROBEABLE_TYPES & CLOUD_TYPES


def test_extra_entries_capped(caplog):
    caplog.set_level(logging.WARNING)
    raw = ",".join(f"ollama=http://10.0.0.{i}:11434" for i in range(1, 21))
    assert len(_parse_extra_backends(raw)) == 16
    assert any("only the first 16" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("url, expected", [
    ("http://localhost:1234", "http://localhost:1234"),
    ("http://127.0.0.1:1234", "http://127.0.0.1:1234"),
    ("http://10.1.2.3:8000", "http://10.1.2.3:8000"),
    ("https://192.168.1.10:8443/", "https://192.168.1.10:8443"),
    ("http://[fd12:3456::1]:1234", "http://[fd12:3456::1]:1234"),
])
def test_loopback_and_lan_literals_accepted(url, expected):
    assert _normalize_probe_url(url) == expected


@pytest.mark.guards(
    "tinyagentos.worker.agent:_normalize_probe_url",
    replace=[(
        "if checked in _DENIED_ADDRESSES or not any(checked in n for n in nets):",
        "if False:",
    )],
)
@pytest.mark.parametrize("url", [
    "http://8.8.8.8:1234",
    "http://172.32.0.1:1234",            # just outside 172.16/12
    "http://100.64.0.1:1234",            # CGNAT is not a LAN range here
    "http://192.0.2.1:1234",             # documentation (is_private, not LAN)
    "http://[2001:4860::8888]:1234",
    "http://[::ffff:8.8.8.8]:1234",
    "http://169.254.169.254",            # cloud metadata (link-local)
    "http://[::ffff:169.254.169.254]",
    "http://[fd00:ec2::254]",            # AWS IPv6 metadata, inside fc00::/7
    "http://[fe80::1]:1234",
])
def test_public_metadata_and_link_local_rejected(url):
    with pytest.raises(ValueError, match="not a loopback or LAN"):
        _normalize_probe_url(url)


@pytest.mark.parametrize("url, reason", [
    ("http://lmstudio.local:1234", "literal IP"),     # no DNS
    ("http://[fe80::1%25eth0]:1234", "literal IP"),   # zone id
    ("ftp://127.0.0.1:21", "scheme"),
    ("http://user:pw@127.0.0.1:1234", "base URL"),
    ("http://127.0.0.1:1234/v1", "base URL"),
])
def test_hostnames_zone_ids_and_non_base_urls_rejected(url, reason):
    with pytest.raises(ValueError, match=reason):
        _normalize_probe_url(url)


def test_dedupe_against_defaults():
    extras = [("ollama", "http://127.0.0.1:11434"), ("ollama", "http://[::1]:11434"),
              ("vllm", "http://localhost:8000"), ("ollama", "http://10.0.0.5:11434")]
    assert _dedupe_candidates(list(_DEFAULT_PROBE_CANDIDATES) + extras) == (
        list(_DEFAULT_PROBE_CANDIDATES) + [("ollama", "http://10.0.0.5:11434")]
    )


@pytest.mark.parametrize("entry, expected", [
    ({"port": 9090, "health_url": "http://127.0.0.1:9090/health"}, "http://127.0.0.1:9090"),
    # A health_url on another port than the declared one falls back to the port.
    ({"port": 9090, "health_url": "http://127.0.0.1:9091/health"}, "http://localhost:9090"),
    ({"port": 9090}, "http://localhost:9090"),
    ({"port": 0}, None),
    ({"port": "9090"}, None),
    ({"port": 70000}, None),
    # A rejected health_url (LAN, hostname) falls back to the port.
    ({"port": 9090, "health_url": "http://192.168.1.9:9090/health"}, "http://localhost:9090"),
    ({"port": 9090, "health_url": "http://myhost:9090/health"}, "http://localhost:9090"),
    ({"health_url": "http://myhost:9090/health"}, None),
])
def test_manifest_entry_url(entry, expected):
    assert _manifest_entry_url({"model_id": "x", **entry}) == expected


# --- end to end through detect_backends -------------------------------------


@pytest.mark.asyncio
class TestDetectBackends:
    async def test_unset_env_probes_exactly_the_defaults(self, monkeypatch):
        backends, fake = await _detect(monkeypatch)
        assert backends == []
        assert fake.origins() == _DEFAULT_ORIGINS

    async def test_unset_env_live_default_unchanged(self, monkeypatch):
        backends, _ = await _detect(monkeypatch, {"http://localhost:11434"})
        assert _summary(backends) == [("ollama:11434", "http://localhost:11434", "ok")]

    async def test_lan_extra_probed_with_short_connect_timeout(self, monkeypatch):
        backends, fake = await _detect(
            monkeypatch, {"http://192.168.1.50:11434"}, extra="ollama=http://192.168.1.50:11434",
        )
        assert _summary(backends) == [("ollama:11434", "http://192.168.1.50:11434", "ok")]
        lan = [t for t, u in fake.requested if "192.168.1.50" in u]
        assert lan and all(t.connect == 1.0 for t in lan)
        assert all(t == 3 for t, u in fake.requested if "localhost" in u)

    async def test_duplicate_of_default_probed_once(self, monkeypatch):
        backends, fake = await _detect(
            monkeypatch, {"http://localhost:11434"}, extra="ollama=http://127.0.0.1:11434",
        )
        assert [b["name"] for b in backends] == ["ollama:11434"]
        assert fake.origins() == _DEFAULT_ORIGINS

    async def test_invalid_extras_never_contacted(self, monkeypatch, caplog):
        caplog.set_level(logging.WARNING)
        backends, fake = await _detect(
            monkeypatch, {"http://8.8.8.8:1234"},
            extra="ollama=http://8.8.8.8:1234,ollama=http://x.example:1,nope=http://127.0.0.1:1,junk",
        )
        assert backends == [] and fake.origins() == _DEFAULT_ORIGINS
        assert len(caplog.records) == 4

    async def test_dead_extras_probe_concurrently(self, monkeypatch):
        extra = ",".join(f"ollama=http://10.0.0.{i}:11434" for i in range(1, 17))
        start = time.monotonic()
        await _detect(monkeypatch, extra=extra, delay=0.3)
        # 27 dead candidates: serial would take ~8 s; concurrent is one delay.
        assert time.monotonic() - start < 1.5

    async def test_running_manifest_software_reports_live(self, monkeypatch):
        m = {"model_id": "qwen", "software": "llamacpp", "port": 9090,
             "health_url": "http://127.0.0.1:9090/health"}
        backends, _ = await _detect(monkeypatch, {"http://127.0.0.1:9090"}, models=[m])
        assert _summary(backends) == [("llama-cpp:9090", "http://127.0.0.1:9090", "ok")]
        assert [x["model_id"] for x in backends[0]["available_models"]] == ["qwen"]

    async def test_manifest_models_attach_per_port(self, monkeypatch):
        """Entries of one type attach to their own port only; down ones are
        separate stopped entries, not hidden by a live backend of the type. A
        rejected health_url falls back to the entry's port."""
        models = [{"model_id": "qwen", "software": "llamacpp", "port": 9090},
                  {"model_id": "e5", "software": "embed", "port": 9091},
                  {"model_id": "q2", "software": "llamacpp", "port": 9092,
                   "health_url": "http://myhost:9092/health"}]
        backends, fake = await _detect(
            monkeypatch, {"http://localhost:9090", "http://localhost:8000"}, models=models,
        )
        assert _summary(backends) == [("llama-cpp:8000", "http://localhost:8000", "ok"),
                                      ("vllm:8000", "http://localhost:8000", "ok"),
                                      ("llama-cpp:9090", "http://localhost:9090", "ok"),
                                      ("llama-cpp:9091", None, "stopped"),
                                      ("llama-cpp:9092", None, "stopped")]
        ids = [[x["model_id"] for x in b.get("available_models", [])] for b in backends]
        assert ids == [[], [], ["qwen"], ["e5"], ["q2"]]
        requested = [u for _, u in fake.requested]
        assert "http://localhost:9091/v1/models" in requested
        assert "http://localhost:9092/v1/models" in requested

    async def test_one_stopped_entry_per_port(self, monkeypatch):
        models = [{"model_id": "a", "software": "llamacpp",
                   "health_url": "http://127.0.0.1:9090/health"},
                  {"model_id": "b", "software": "llamacpp", "port": 9090}]
        backends, _ = await _detect(monkeypatch, models=models)
        assert _summary(backends) == [("llama-cpp:9090", None, "stopped")]
        assert [x["model_id"] for x in backends[0]["available_models"]] == ["a", "b"]


    @pytest.mark.guards(
        "tinyagentos.worker.agent:WorkerAgent.detect_backends",
        replace=[("_PROBE_DEADLINE_S)", "None)")],
    )
    async def test_trickling_lan_server_cut_off_at_deadline(self, monkeypatch):
        monkeypatch.setattr(agent_mod, "_PROBE_DEADLINE_S", 0.2)
        # Without the deadline the stalled probe never returns; fail, not hang.
        backends, _ = await asyncio.wait_for(_detect(
            monkeypatch, {"http://localhost:11434"}, stall={"http://10.0.0.5:11434"},
            extra="ollama=http://10.0.0.5:11434",
        ), 5)
        assert _summary(backends) == [("ollama:11434", "http://localhost:11434", "ok")]

    async def test_manifest_health_port_mismatch_reported_on_declared_port(self, monkeypatch):
        m = {"model_id": "qwen", "software": "llamacpp", "port": 9090,
             "health_url": "http://127.0.0.1:9091/health"}
        backends, _ = await _detect(monkeypatch, models=[m])
        assert _summary(backends) == [("llama-cpp:9090", None, "stopped")]
        assert backends[0]["available_models"][0]["port"] == 9090


# --- the worker's own advertised URL ------------------------------------------


@pytest.fixture
def _paired(monkeypatch):
    monkeypatch.delenv("TAOS_ADVERTISE_IP", raising=False)
    monkeypatch.setattr("tinyagentos.worker.pairing.load_signing_key", lambda d: b"k" * 32)
    monkeypatch.setattr(agent_mod, "_detect_lan_ip", lambda url: "192.168.1.7")
    monkeypatch.setattr(WorkerAgent, "get_worker_url", lambda self: "http://192.168.1.7:8765")


@pytest.mark.asyncio
@pytest.mark.guards(
    "tinyagentos.worker.agent:WorkerAgent._advertised_url",
    replace=[('if url and _candidate_key(b.get("type", ""), url)[2] == "loopback":', "if url:")],
)
async def test_lan_extra_never_becomes_the_worker_url(monkeypatch, _paired):
    """A live LAN extra with no live local backend: register and heartbeat
    advertise this worker's own address, not the LAN box's."""
    _, fake = await _detect(monkeypatch, {"http://192.168.1.5:11434"},
                            extra="ollama=http://192.168.1.5:11434")
    agent = WorkerAgent("http://192.168.1.2:6969", name="w1")
    assert await agent.register() is True
    await agent.heartbeat()
    urls = {u.rsplit("/", 1)[-1]: body["url"] for u, body in fake.posted}
    assert urls == {"workers": "http://192.168.1.7:8765", "heartbeat": "http://192.168.1.7:8765"}
    backends = [body["backends"] for _, body in fake.posted]
    assert all([b["url"] for b in bs] == ["http://192.168.1.5:11434"] for bs in backends)


@pytest.mark.asyncio
async def test_live_local_backend_still_used_as_worker_url(monkeypatch, _paired):
    """Unchanged legacy fallback: a co-located worker without advertise
    settings is reached through its first live loopback backend."""
    _, fake = await _detect(monkeypatch, {"http://localhost:11434", "http://192.168.1.5:11434"},
                            extra="ollama=http://192.168.1.5:11434")
    agent = WorkerAgent("http://192.168.1.2:6969", name="w1")
    assert await agent.register() is True
    await agent.heartbeat()
    assert [body["url"] for _, body in fake.posted] == ["http://localhost:11434"] * 2

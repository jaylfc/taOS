"""Task 22: Tests for extended WorkerInfo fields + ClusterManager.get_worker."""
from __future__ import annotations

import asyncio
from dataclasses import asdict

import pytest

from tinyagentos.cluster.worker_protocol import WorkerInfo
from tinyagentos.cluster.manager import ClusterManager


class TestWorkerInfoNewFields:
    """WorkerInfo should carry worker_url, signing_key, and tls_cert_provider."""

    def test_worker_url_defaults_to_none(self):
        w = WorkerInfo(name="w", url="http://localhost:9000")
        assert w.worker_url is None

    def test_signing_key_defaults_to_empty_bytes(self):
        w = WorkerInfo(name="w", url="http://localhost:9000")
        assert isinstance(w.signing_key, bytes)
        assert w.signing_key == b""

    def test_tls_cert_provider_defaults_to_none(self):
        w = WorkerInfo(name="w", url="http://localhost:9000")
        assert w.tls_cert_provider is None

    def test_can_set_worker_url(self):
        w = WorkerInfo(name="w", url="http://localhost:9000", worker_url="http://10.0.0.1:6969")
        assert w.worker_url == "http://10.0.0.1:6969"

    def test_can_set_signing_key(self):
        key = b"\x01" * 32
        w = WorkerInfo(name="w", url="http://localhost:9000", signing_key=key)
        assert w.signing_key == key

    def test_can_set_tls_cert_provider(self):
        w = WorkerInfo(name="w", url="http://localhost:9000", tls_cert_provider="letsencrypt")
        assert w.tls_cert_provider == "letsencrypt"

    def test_new_fields_serialise_via_asdict(self):
        key = b"\xab" * 32
        w = WorkerInfo(
            name="w",
            url="http://localhost:9000",
            worker_url="http://10.0.0.1:6969",
            signing_key=key,
            tls_cert_provider="letsencrypt",
        )
        d = asdict(w)
        assert d["worker_url"] == "http://10.0.0.1:6969"
        assert d["signing_key"] == key
        assert d["tls_cert_provider"] == "letsencrypt"


class TestClusterManagerGetWorker:
    """ClusterManager.get_worker should return the named worker or None."""

    def test_get_worker_returns_none_when_empty(self):
        mgr = ClusterManager()
        assert mgr.get_worker("nonexistent") is None

    def test_get_worker_returns_worker_after_register(self):
        mgr = ClusterManager()
        w = WorkerInfo(name="myworker", url="http://localhost:9001")
        asyncio.run(mgr.register_worker(w))
        result = mgr.get_worker("myworker")
        assert result is not None
        assert result.name == "myworker"

    def test_get_worker_returns_correct_instance(self):
        mgr = ClusterManager()
        w1 = WorkerInfo(name="alpha", url="http://localhost:9001")
        w2 = WorkerInfo(name="beta", url="http://localhost:9002")
        asyncio.run(mgr.register_worker(w1))
        asyncio.run(mgr.register_worker(w2))
        assert mgr.get_worker("alpha").name == "alpha"
        assert mgr.get_worker("beta").name == "beta"
        assert mgr.get_worker("gamma") is None

    def test_get_worker_sees_signing_key(self):
        key = b"\x42" * 32
        mgr = ClusterManager()
        w = WorkerInfo(name="local", url="http://127.0.0.1:6969", signing_key=key)
        asyncio.run(mgr.register_worker(w))
        found = mgr.get_worker("local")
        assert found.signing_key == key

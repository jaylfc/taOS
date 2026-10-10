from __future__ import annotations

import asyncio

import httpx
import pytest

from tinyagentos.cluster.failure_tracker import FailureTracker
from tinyagentos.cluster.placement import rank
from tinyagentos.cluster.router import TaskRouter
from tinyagentos.cluster.worker_protocol import WorkerInfo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class StubClusterManager:
    def __init__(self, workers, failure_tracker=None):
        self._workers = {w.name: w for w in workers}
        self.failure_tracker = failure_tracker

    def get_workers_for_capability(self, capability: str):
        return [w for w in self._workers.values() if capability in w.capabilities]


def _make_worker(name: str, load: float, url: str = "http://localhost:8000") -> WorkerInfo:
    return WorkerInfo(
        name=name,
        url=url,
        capabilities=["chat"],
        load=load,
        status="online",
        last_heartbeat=0.0,
        registered_at=0.0,
    )


def _make_transport():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200, json={"ok": True})

    return handler, calls


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAffinityRouting:
    def test_affinity_key_hits_rendezvous_winner(self):
        """affinity_key='qwen3-8b' routes to bravo (rendezvous #1), not alpha (load #1)."""
        alpha = _make_worker("alpha", 0.1, "http://alpha:8000")
        bravo = _make_worker("bravo", 0.5, "http://bravo:8000")
        charlie = _make_worker("charlie", 0.2, "http://charlie:8000")
        # LOAD order: alpha (0.1), charlie (0.2), bravo (0.5)
        workers = [alpha, charlie, bravo]

        assert rank("qwen3-8b", ["alpha", "bravo", "charlie"])[0] == "bravo"

        handler, calls = _make_transport()
        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)

        cluster = StubClusterManager(workers=workers)
        router = TaskRouter(cluster=cluster, http_client=client)

        async def run():
            return await router.route_request(
                "chat",
                "POST",
                "/v1/chat/completions",
                {"messages": [{"role": "user", "content": "hi"}]},
                affinity_key="qwen3-8b",
            )

        data, name = asyncio.run(run())
        assert name == "bravo", f"Expected bravo, got {name}; calls={calls}"
        assert calls == ["http://bravo:8000/v1/chat/completions"]

    def test_bravo_circuit_tripped_falls_to_charlie(self):
        """bravo circuit-tripped -> request goes to charlie (rendezvous #2)."""
        alpha = _make_worker("alpha", 0.1, "http://alpha:8000")
        bravo = _make_worker("bravo", 0.5, "http://bravo:8000")
        charlie = _make_worker("charlie", 0.2, "http://charlie:8000")
        # LOAD order: alpha (0.1), charlie (0.2), bravo (0.5)
        workers = [alpha, charlie, bravo]

        ft = FailureTracker(failure_threshold=1, window_seconds=60.0)
        ft.record_failure("bravo")
        assert ft.is_tripped("bravo")

        handler, calls = _make_transport()
        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)

        cluster = StubClusterManager(workers=workers, failure_tracker=ft)
        router = TaskRouter(cluster=cluster, http_client=client)

        async def run():
            return await router.route_request(
                "chat",
                "POST",
                "/v1/chat/completions",
                {"messages": [{"role": "user", "content": "hi"}]},
                affinity_key="qwen3-8b",
            )

        data, name = asyncio.run(run())
        assert name == "charlie", f"Expected charlie, got {name}; calls={calls}"
        assert calls == ["http://charlie:8000/v1/chat/completions"]

    def test_bravo_overloaded_routes_to_charlie(self):
        """bravo load 0.95 -> request goes to charlie."""
        alpha = _make_worker("alpha", 0.1, "http://alpha:8000")
        bravo = _make_worker("bravo", 0.95, "http://bravo:8000")
        charlie = _make_worker("charlie", 0.2, "http://charlie:8000")
        # LOAD order: alpha (0.1), charlie (0.2), bravo (0.95)
        workers = [alpha, charlie, bravo]

        handler, calls = _make_transport()
        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)

        cluster = StubClusterManager(workers=workers)
        router = TaskRouter(cluster=cluster, http_client=client)

        async def run():
            return await router.route_request(
                "chat",
                "POST",
                "/v1/chat/completions",
                {"messages": [{"role": "user", "content": "hi"}]},
                affinity_key="qwen3-8b",
            )

        data, name = asyncio.run(run())
        assert name == "charlie", f"Expected charlie, got {name}; calls={calls}"
        assert calls == ["http://charlie:8000/v1/chat/completions"]

    def test_all_overloaded_uses_rendezvous_order(self):
        """All three >= 0.9 -> bravo (rendezvous #1 among overloaded workers)."""
        alpha = _make_worker("alpha", 0.95, "http://alpha:8000")
        bravo = _make_worker("bravo", 0.95, "http://bravo:8000")
        charlie = _make_worker("charlie", 0.95, "http://charlie:8000")
        # All same load, any order is fine
        workers = [alpha, bravo, charlie]

        handler, calls = _make_transport()
        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)

        cluster = StubClusterManager(workers=workers)
        router = TaskRouter(cluster=cluster, http_client=client)

        async def run():
            return await router.route_request(
                "chat",
                "POST",
                "/v1/chat/completions",
                {"messages": [{"role": "user", "content": "hi"}]},
                affinity_key="qwen3-8b",
            )

        data, name = asyncio.run(run())
        assert name == "bravo", f"Expected bravo, got {name}; calls={calls}"
        assert calls == ["http://bravo:8000/v1/chat/completions"]

    def test_affinity_key_none_uses_load_order(self):
        """affinity_key=None -> alpha (lowest load, unchanged behavior)."""
        alpha = _make_worker("alpha", 0.1, "http://alpha:8000")
        bravo = _make_worker("bravo", 0.5, "http://bravo:8000")
        charlie = _make_worker("charlie", 0.2, "http://charlie:8000")
        # LOAD order: alpha (0.1), charlie (0.2), bravo (0.5)
        workers = [alpha, charlie, bravo]

        handler, calls = _make_transport()
        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)

        cluster = StubClusterManager(workers=workers)
        router = TaskRouter(cluster=cluster, http_client=client)

        async def run():
            return await router.route_request(
                "chat",
                "POST",
                "/v1/chat/completions",
                {"messages": [{"role": "user", "content": "hi"}]},
            )

        data, name = asyncio.run(run())
        assert name == "alpha", f"Expected alpha, got {name}; calls={calls}"
        assert calls == ["http://alpha:8000/v1/chat/completions"]
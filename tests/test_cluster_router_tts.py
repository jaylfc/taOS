"""route_request() must refuse capability 'tts' up front: kokoro answers
binary audio, so resp.json() would raise and the worker would be counted
as failed against the circuit breaker for a working backend."""
from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi import HTTPException

from tinyagentos.cluster.failure_tracker import FailureTracker
from tinyagentos.cluster.router import TaskRouter
from tinyagentos.cluster.worker_protocol import WorkerInfo


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
        capabilities=["tts"],
        load=load,
        status="online",
        last_heartbeat=0.0,
        registered_at=0.0,
    )


class TestTtsRefusal:
    def test_tts_refused_without_calling_worker_or_touching_breaker(self):
        """route_request('tts', ...) raises HTTPException(503) without
        calling any worker and without recording a breaker failure."""
        worker = _make_worker("kokoro", 0.1, "http://kokoro:8880")

        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            return httpx.Response(200, json={"ok": True})

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)

        ft = FailureTracker()
        cluster = StubClusterManager(workers=[worker], failure_tracker=ft)
        router = TaskRouter(cluster=cluster, http_client=client)

        async def run():
            return await router.route_request(
                "tts",
                "POST",
                "/api/cluster/route",
                {"text": "hello"},
            )

        with pytest.raises(HTTPException) as exc:
            asyncio.run(run())

        assert exc.value.status_code == 503
        assert exc.value.detail == "tts returns binary audio; call the worker directly"
        assert calls == [], f"no worker may be called for tts, got {calls}"
        assert not ft.is_tripped("kokoro"), "breaker must not be touched by the tts refusal"

    def test_other_capabilities_unaffected(self):
        """A non-tts capability still routes normally."""
        worker = _make_worker("kokoro", 0.1, "http://kokoro:8880")
        worker.capabilities = ["chat", "tts"]

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"reply": "hi"})

        transport = httpx.MockTransport(handler)
        client = httpx.AsyncClient(transport=transport)

        cluster = StubClusterManager(workers=[worker])
        router = TaskRouter(cluster=cluster, http_client=client)

        async def run():
            return await router.route_request(
                "chat",
                "POST",
                "/v1/chat/completions",
                {"messages": [{"role": "user", "content": "hi"}]},
            )

        data, name = asyncio.run(run())
        assert name == "kokoro"
        assert data == {"reply": "hi"}

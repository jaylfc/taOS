"""Benchmark runner — executes a SuiteTask against available backends.

Runs on the worker, not the controller. The worker discovers its own
live backends via the existing BackendCatalog, then for each SuiteTask
in the suite asks the catalog "is there a backend serving this capability
with this model?" If yes, run the task and measure. If no, skip the task
(with status="skipped" and a human-readable reason).

Results are POSTed to the controller via ``POST /api/workers/{id}/benchmark/results``.
The runner itself is agnostic about whether this is a first-join run or a
manual rerun — that flag is passed through.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import platform
import random
import socket
import statistics
import string
import time
from typing import Any, Awaitable, Callable, Optional

import httpx

from tinyagentos.benchmark.suite import BenchmarkSuite, Metric, SuiteResult, SuiteTask

logger = logging.getLogger(__name__)

DEFAULT_CONTEXT = "The quick brown fox jumps over the lazy dog."


class BenchmarkRunner:
    """Runs a BenchmarkSuite against the worker's local backends.

    Construction takes a resolver callable that, given a capability +
    model id, returns ``(backend_url, backend_type)`` if that model is
    loaded on a local backend right now, or ``None`` if not.

    This is the same contract as BackendCatalog.find_backend_for_model(),
    which is what the production path uses. Unit tests can pass a stub.
    """

    def __init__(
        self,
        suite: BenchmarkSuite,
        resolver: Callable[[str, str], Optional[tuple[str, str]]],
        http_timeout: float = 300.0,
    ):
        self.suite = suite
        self.resolver = resolver
        self.http_timeout = http_timeout

    async def run(self) -> list[SuiteResult]:
        results: list[SuiteResult] = []
        for task in self.suite.tasks:
            logger.info("benchmark: starting %s (%s/%s)", task.id, task.capability, task.model)
            start = time.monotonic()
            try:
                result = await asyncio.wait_for(
                    self._run_one(task),
                    timeout=task.timeout_seconds,
                )
            except asyncio.TimeoutError:
                result = SuiteResult(
                    task_id=task.id,
                    capability=task.capability,
                    model=task.model,
                    metric=task.metric,
                    value=None,
                    unit=self._unit_for(task.metric),
                    status="timeout",
                    elapsed_seconds=time.monotonic() - start,
                    error=f"exceeded {task.timeout_seconds}s",
                )
            except BackendNotAvailable as exc:
                result = SuiteResult(
                    task_id=task.id,
                    capability=task.capability,
                    model=task.model,
                    metric=task.metric,
                    value=None,
                    unit=self._unit_for(task.metric),
                    status="skipped",
                    elapsed_seconds=time.monotonic() - start,
                    error=str(exc),
                )
            except Exception as exc:
                status = "skipped" if task.optional else "error"
                result = SuiteResult(
                    task_id=task.id,
                    capability=task.capability,
                    model=task.model,
                    metric=task.metric,
                    value=None,
                    unit=self._unit_for(task.metric),
                    status=status,
                    elapsed_seconds=time.monotonic() - start,
                    error=str(exc),
                )
                logger.exception("benchmark: %s failed", task.id)
            logger.info(
                "benchmark: %s -> %s (%s %s in %.1fs)",
                task.id,
                result.status,
                result.value,
                result.unit,
                result.elapsed_seconds,
            )
            results.append(result)
        return results

    async def _run_one(self, task: SuiteTask) -> SuiteResult:
        lookup = self.resolver(task.capability, task.model)
        if lookup is None:
            raise BackendNotAvailable(
                f"no local backend serves {task.capability}/{task.model}"
            )
        backend_url, backend_type = lookup

        handler = _HANDLERS.get(task.capability)
        if handler is None:
            raise RuntimeError(f"no benchmark handler for capability {task.capability}")

        start = time.monotonic()
        async with httpx.AsyncClient(timeout=self.http_timeout) as client:
            value, details = await handler(
                client=client,
                backend_url=backend_url,
                backend_type=backend_type,
                task=task,
            )
        elapsed = time.monotonic() - start
        return SuiteResult(
            task_id=task.id,
            capability=task.capability,
            model=task.model,
            metric=task.metric,
            value=value,
            unit=self._unit_for(task.metric),
            status="ok",
            elapsed_seconds=elapsed,
            details=details,
        )

    @staticmethod
    def _unit_for(metric: Metric) -> str:
        return {
            Metric.DOCS_PER_SEC: "docs/s",
            Metric.TOKENS_PER_SEC: "tok/s",
            Metric.SECONDS_PER_STEP: "s/step",
            Metric.SECONDS_PER_IMAGE: "s/image",
            Metric.RTF: "realtime",
            Metric.LATENCY_MS_P50: "ms",
            Metric.LATENCY_MS_P95: "ms",
        }[metric]


class BackendNotAvailable(RuntimeError):
    """Raised when the worker has no backend serving the task's capability+model."""


# ---------------------------------------------------------------------------
# Per-capability handlers
# ---------------------------------------------------------------------------


async def _bench_embedding(
    *,
    client: httpx.AsyncClient,
    backend_url: str,
    backend_type: str,
    task: SuiteTask,
) -> tuple[float, dict]:
    num_docs = int(task.workload.get("num_docs", 50))
    avg_tokens = int(task.workload.get("avg_tokens_per_doc", 64))
    docs = [_fake_doc(avg_tokens) for _ in range(num_docs)]

    start = time.monotonic()
    resp = await client.post(
        f"{backend_url.rstrip('/')}/v1/embeddings",
        json={"model": task.model, "input": docs},
    )
    resp.raise_for_status()
    data = resp.json()
    elapsed = time.monotonic() - start

    received = len(data.get("data", [])) if isinstance(data, dict) else 0
    value = received / elapsed if elapsed > 0 else 0.0
    return value, {
        "num_docs": num_docs,
        "avg_tokens_per_doc": avg_tokens,
        "wall_seconds": round(elapsed, 3),
        "received": received,
    }


async def _bench_reranking(
    *,
    client: httpx.AsyncClient,
    backend_url: str,
    backend_type: str,
    task: SuiteTask,
) -> tuple[float, dict]:
    num_queries = int(task.workload.get("num_queries", 10))
    candidates = int(task.workload.get("candidates_per_query", 20))

    latencies_ms: list[float] = []
    for i in range(num_queries):
        query = _fake_doc(16)
        docs = [_fake_doc(64) for _ in range(candidates)]
        start = time.monotonic()
        resp = await client.post(
            f"{backend_url.rstrip('/')}/v1/rerank",
            json={"model": task.model, "query": query, "documents": docs},
        )
        elapsed_ms = (time.monotonic() - start) * 1000
        resp.raise_for_status()
        latencies_ms.append(elapsed_ms)

    latencies_ms.sort()
    p50 = statistics.median(latencies_ms)
    return p50, {
        "num_queries": num_queries,
        "candidates_per_query": candidates,
        "p50_ms": round(p50, 2),
        "p95_ms": round(latencies_ms[min(len(latencies_ms) - 1, int(len(latencies_ms) * 0.95))], 2),
    }


async def _bench_llm_chat(
    *,
    client: httpx.AsyncClient,
    backend_url: str,
    backend_type: str,
    task: SuiteTask,
) -> tuple[float, dict]:
    prompt = task.workload.get("prompt", "Say hello.")
    max_tokens = int(task.workload.get("max_tokens", 128))

    start = time.monotonic()
    resp = await client.post(
        f"{backend_url.rstrip('/')}/v1/chat/completions",
        json={
            "model": task.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": 0.0,
            "stream": False,
        },
    )
    elapsed = time.monotonic() - start
    resp.raise_for_status()
    data = resp.json()

    usage = data.get("usage", {}) if isinstance(data, dict) else {}
    completion_tokens = int(usage.get("completion_tokens") or 0)
    if completion_tokens <= 0:
        # Fall back to rough token estimate from content length
        content = ""
        choices = data.get("choices") if isinstance(data, dict) else None
        if choices:
            content = choices[0].get("message", {}).get("content", "") or ""
        completion_tokens = max(1, len(content.split()))
    value = completion_tokens / elapsed if elapsed > 0 else 0.0
    return value, {
        "prompt": prompt[:80],
        "max_tokens": max_tokens,
        "completion_tokens": completion_tokens,
        "wall_seconds": round(elapsed, 3),
    }


async def _bench_image_generation(
    *,
    client: httpx.AsyncClient,
    backend_url: str,
    backend_type: str,
    task: SuiteTask,
) -> tuple[float, dict]:
    size = task.workload.get("size", "256x256")
    steps = int(task.workload.get("steps", 2))
    prompt = task.workload.get("prompt", "benchmark")

    if backend_type == "sd-cpp":
        width, height = (int(x) for x in size.split("x"))
        payload = {
            "prompt": prompt,
            "steps": steps,
            "width": width,
            "height": height,
            "cfg_scale": 1.0,
            "seed": 42,
            "sampler_name": "euler_a",
        }
        endpoint = f"{backend_url.rstrip('/')}/sdapi/v1/txt2img"
    else:
        payload = {
            "prompt": prompt,
            "model": task.model,
            "size": size,
            "steps": steps,
        }
        endpoint = f"{backend_url.rstrip('/')}/v1/images/generations"

    start = time.monotonic()
    resp = await client.post(endpoint, json=payload)
    elapsed = time.monotonic() - start
    resp.raise_for_status()
    return elapsed, {
        "size": size,
        "steps": steps,
        "wall_seconds": round(elapsed, 3),
    }


async def _bench_whisper(
    *,
    client: httpx.AsyncClient,
    backend_url: str,
    backend_type: str,
    task: SuiteTask,
) -> tuple[float, dict]:
    # Placeholder — whisper benchmarking needs a sample audio file that we
    # don't want to ship in the repo. Worker's whisper backend should
    # expose a self-test endpoint we can hit here. Until then, mark
    # unavailable so the result is 'skipped' by optional=True.
    raise BackendNotAvailable("whisper benchmark requires a sample clip — not shipped in v1")


_HANDLERS: dict[str, Callable[..., Awaitable[tuple[float, dict]]]] = {
    "embedding": _bench_embedding,
    "reranking": _bench_reranking,
    "llm-chat": _bench_llm_chat,
    "image-generation": _bench_image_generation,
    "speech-to-text": _bench_whisper,
}


def _fake_doc(avg_tokens: int) -> str:
    """Generate a random-ish doc of roughly ``avg_tokens`` tokens."""
    words = []
    for _ in range(avg_tokens):
        length = random.randint(3, 8)
        words.append("".join(random.choices(string.ascii_lowercase, k=length)))
    return " ".join(words)


# ---------------------------------------------------------------------------
# CLI — invoked from install-worker.sh on first join
# ---------------------------------------------------------------------------


def _detect_platform() -> str:
    return f"{platform.system().lower()}-{platform.machine()}"


async def _cli_main(args: argparse.Namespace) -> int:
    """Run the default suite on whatever the worker already exposes locally.

    The install script calls this with --first-join so results are stored
    with first_join=True, and subsequent runs from the UI or CLI default
    to first_join=False.
    """
    from tinyagentos.benchmark.suite import BenchmarkSuite

    suite = BenchmarkSuite.default()

    # Local backend discovery — probe the same handful of ports as
    # WorkerAgent.detect_backends so this works before the worker process
    # is fully up.
    candidates = [
        ("rkllama", "http://localhost:8080"),
        ("sd-cpp", "http://localhost:7864"),
        ("ollama", "http://localhost:11434"),
        ("llama-cpp", "http://localhost:8000"),
    ]

    backend_models: dict[tuple[str, str], str] = {}
    async with httpx.AsyncClient(timeout=3) as client:
        for backend_type, url in candidates:
            try:
                if backend_type in ("rkllama", "ollama"):
                    r = await client.get(f"{url}/api/tags")
                    if r.status_code == 200:
                        for m in r.json().get("models", []):
                            backend_models[(m.get("model") or m.get("name"), backend_type)] = url
                elif backend_type == "sd-cpp":
                    r = await client.get(f"{url}/sdapi/v1/sd-models")
                    if r.status_code == 200:
                        for m in r.json():
                            backend_models[(m.get("model_name") or m.get("title") or "", backend_type)] = url
                elif backend_type == "llama-cpp":
                    r = await client.get(f"{url}/v1/models")
                    if r.status_code == 200:
                        for m in r.json().get("data", []):
                            backend_models[(m.get("id", ""), backend_type)] = url
            except Exception:
                continue

    def resolver(capability: str, model: str):
        for (backend_model, backend_type), url in backend_models.items():
            if not backend_model:
                continue
            bm = backend_model.lower()
            mm = model.lower()
            if mm == bm or mm in bm or bm in mm:
                return (url, backend_type)
        return None

    runner = BenchmarkRunner(suite=suite, resolver=resolver)
    results = await runner.run()

    ok = sum(1 for r in results if r.status == "ok")
    skipped = sum(1 for r in results if r.status == "skipped")
    errored = sum(1 for r in results if r.status in ("error", "timeout"))
    print(f"benchmark complete: {ok} ok, {skipped} skipped, {errored} errored")
    for r in results:
        line = f"  {r.capability:16} {r.model:28} {r.metric.value:18}"
        if r.value is not None:
            line += f" {r.value:.3f} {r.unit}"
        else:
            line += f" [{r.status}] {r.error or ''}"
        print(line)

    if args.report_to:
        worker_id = args.worker_id or args.worker_name or socket.gethostname()
        payload = {
            "worker_id": worker_id,
            "worker_name": args.worker_name or socket.gethostname(),
            "platform": _detect_platform(),
            "suite_name": suite.name,
            "first_join": bool(args.first_join),
            "results": [r.to_dict() for r in results],
        }
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                url = f"{args.report_to.rstrip('/')}/api/workers/{worker_id}/benchmark/results"
                r = await client.post(url, json=payload)
                if r.status_code >= 400:
                    print(f"controller rejected benchmark report: {r.status_code} {r.text[:200]}")
                    return 1
        except Exception as exc:
            print(f"failed to POST benchmark results: {exc}")
            return 1

    return 0 if errored == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="TinyAgentOS worker benchmark runner")
    parser.add_argument("--report-to", help="controller URL to POST results to")
    parser.add_argument("--worker-name", default=None)
    parser.add_argument("--worker-id", default=None)
    parser.add_argument("--first-join", action="store_true", help="mark this run as the one-time first-join benchmark")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    return asyncio.run(_cli_main(args))


if __name__ == "__main__":
    raise SystemExit(main())

"""Unit tests for the benchmark runner.

Covers the pure-logic paths in BenchmarkRunner: result aggregation,
unit mapping, error handling, timeout behaviour, and per-capability
handler parsing/scoring. Every external dependency (model calls,
network, filesystem, time) is mocked.
"""
from __future__ import annotations

import asyncio
import statistics
import time
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tinyagentos.benchmark.runner import (
    BackendNotAvailable,
    BenchmarkRunner,
    _bench_embedding,
    _bench_image_generation,
    _bench_llm_chat,
    _bench_reranking,
    _fake_doc,
)
from tinyagentos.benchmark.suite import BenchmarkSuite, Metric, SuiteResult, SuiteTask


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_task(
    *,
    capability: str = "embedding",
    model: str = "test-model",
    metric: Metric = Metric.DOCS_PER_SEC,
    timeout: float = 10.0,
    optional: bool = False,
    workload: dict | None = None,
    task_id: str = "test-task",
) -> SuiteTask:
    return SuiteTask(
        id=task_id,
        capability=capability,
        model=model,
        metric=metric,
        description="test task",
        workload=workload or {},
        timeout_seconds=timeout,
        optional=optional,
    )


def _make_suite(*tasks: SuiteTask) -> BenchmarkSuite:
    return BenchmarkSuite(name="test", description="test suite", tasks=list(tasks))


def _mock_json_response(json_data: dict, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    resp.raise_for_status = MagicMock()
    return resp


# ---------------------------------------------------------------------------
# _unit_for
# ---------------------------------------------------------------------------


class TestUnitFor:
    @pytest.mark.parametrize(
        "metric,expected",
        [
            (Metric.DOCS_PER_SEC, "docs/s"),
            (Metric.TOKENS_PER_SEC, "tok/s"),
            (Metric.SECONDS_PER_STEP, "s/step"),
            (Metric.SECONDS_PER_IMAGE, "s/image"),
            (Metric.RTF, "realtime"),
            (Metric.LATENCY_MS_P50, "ms"),
            (Metric.LATENCY_MS_P95, "ms"),
        ],
    )
    def test_unit_for(self, metric: Metric, expected: str):
        assert BenchmarkRunner._unit_for(metric) == expected


# ---------------------------------------------------------------------------
# _fake_doc
# ---------------------------------------------------------------------------


class TestFakeDoc:
    def test_word_count(self):
        doc = _fake_doc(avg_tokens=20)
        words = doc.split()
        assert len(words) == 20

    def test_non_empty(self):
        doc = _fake_doc(avg_tokens=5)
        assert len(doc) > 0

    def test_deterministic_with_seed(self):
        import random
        random.seed(42)
        a = _fake_doc(10)
        random.seed(42)
        b = _fake_doc(10)
        assert a == b


# ---------------------------------------------------------------------------
# BenchmarkRunner.run() — resolver returns None (skipped)
# ---------------------------------------------------------------------------


class TestRunnerRun:
    @pytest.mark.asyncio
    async def test_resolver_none_skips_task(self):
        task = _make_task(capability="embedding", model="missing-model")
        suite = _make_suite(task)
        runner = BenchmarkRunner(suite=suite, resolver=lambda cap, model: None)

        results = await runner.run()

        assert len(results) == 1
        r = results[0]
        assert r.status == "skipped"
        assert r.value is None
        assert r.task_id == "test-task"
        assert r.capability == "embedding"
        assert r.model == "missing-model"
        assert "no local backend" in r.error

    @pytest.mark.asyncio
    async def test_unknown_capability_error(self):
        task = _make_task(capability="unknown-cap", model="m")
        suite = _make_suite(task)
        runner = BenchmarkRunner(
            suite=suite,
            resolver=lambda cap, model: ("http://localhost:8080", "test"),
        )

        results = await runner.run()

        assert len(results) == 1
        r = results[0]
        assert r.status == "error"
        assert r.value is None
        assert "no benchmark handler" in r.error

    @pytest.mark.asyncio
    async def test_unknown_capability_optional_skips(self):
        task = _make_task(capability="unknown-cap", model="m", optional=True)
        suite = _make_suite(task)
        runner = BenchmarkRunner(
            suite=suite,
            resolver=lambda cap, model: ("http://localhost:8080", "test"),
        )

        results = await runner.run()

        assert len(results) == 1
        r = results[0]
        assert r.status == "skipped"
        assert r.value is None

    @pytest.mark.asyncio
    async def test_multiple_tasks_aggregated(self):
        async def fake_handler(*, client, backend_url, backend_type, task):
            return 1.0, {}

        tasks = [
            _make_task(task_id="t1", capability="embedding", model="m1"),
            _make_task(task_id="t2", capability="embedding", model="m2"),
            _make_task(task_id="t3", capability="embedding", model="m3"),
        ]
        suite = _make_suite(*tasks)
        # Only resolve m2
        runner = BenchmarkRunner(
            suite=suite,
            resolver=lambda cap, model: (
                ("http://localhost:8080", "test") if model == "m2" else None
            ),
        )

        with patch.dict(
            "tinyagentos.benchmark.runner._HANDLERS",
            {"embedding": fake_handler},
            clear=False,
        ):
            results = await runner.run()

        assert len(results) == 3
        assert results[0].status == "skipped"
        assert results[1].status == "ok"
        assert results[2].status == "skipped"

    @pytest.mark.asyncio
    async def test_result_has_correct_unit(self):
        task = _make_task(metric=Metric.TOKENS_PER_SEC)
        suite = _make_suite(task)
        runner = BenchmarkRunner(suite=suite, resolver=lambda cap, model: None)

        results = await runner.run()

        assert results[0].unit == "tok/s"

    @pytest.mark.asyncio
    async def test_elapsed_seconds_populated(self):
        task = _make_task()
        suite = _make_suite(task)
        runner = BenchmarkRunner(suite=suite, resolver=lambda cap, model: None)

        t0 = time.monotonic()
        results = await runner.run()
        t1 = time.monotonic()

        assert results[0].elapsed_seconds >= 0
        assert results[0].elapsed_seconds <= (t1 - t0) + 0.1


# ---------------------------------------------------------------------------
# BenchmarkRunner.run() — timeout
# ---------------------------------------------------------------------------


class TestRunnerTimeout:
    @pytest.mark.asyncio
    async def test_timeout_produces_timeout_result(self):
        task = _make_task(timeout=0.01)
        suite = _make_suite(task)

        async def slow_handler(*, client, backend_url, backend_type, task):
            await asyncio.sleep(10.0)
            return 1.0, {}

        with patch.dict(
            "tinyagentos.benchmark.runner._HANDLERS",
            {"embedding": slow_handler},
            clear=False,
        ):
            runner = BenchmarkRunner(
                suite=suite,
                resolver=lambda cap, model: ("http://localhost:8080", "test"),
            )
            results = await runner.run()

        assert len(results) == 1
        r = results[0]
        assert r.status == "timeout"
        assert r.value is None
        assert "exceeded" in r.error


# ---------------------------------------------------------------------------
# _run_one — BackendNotAvailable
# ---------------------------------------------------------------------------


class TestRunOne:
    @pytest.mark.asyncio
    async def test_backend_not_available_raises(self):
        task = _make_task()
        suite = _make_suite(task)
        runner = BenchmarkRunner(suite=suite, resolver=lambda cap, model: None)

        with pytest.raises(BackendNotAvailable):
            await runner._run_one(task)

    @pytest.mark.asyncio
    async def test_unknown_capability_raises(self):
        task = _make_task(capability="nonexistent")
        suite = _make_suite(task)
        runner = BenchmarkRunner(
            suite=suite,
            resolver=lambda cap, model: ("http://localhost:8080", "test"),
        )

        with pytest.raises(RuntimeError, match="no benchmark handler"):
            await runner._run_one(task)


# ---------------------------------------------------------------------------
# Handler: _bench_embedding
# ---------------------------------------------------------------------------


class TestBenchEmbedding:
    @pytest.mark.asyncio
    async def test_embedding_throughput(self):
        resp = _mock_json_response(
            {"data": [{"embedding": [0.1, 0.2]} for _ in range(50)]}
        )
        client = AsyncMock()
        client.post = AsyncMock(return_value=resp)

        task = _make_task(
            capability="embedding",
            model="test-embed",
            metric=Metric.DOCS_PER_SEC,
            workload={"num_docs": 50, "avg_tokens_per_doc": 8},
        )

        monotonic_vals = [0.0, 1.5]
        with (
            patch("tinyagentos.benchmark.runner._fake_doc", return_value="fake doc"),
            patch("tinyagentos.benchmark.runner.time") as mock_time,
        ):
            mock_time.monotonic = MagicMock(side_effect=monotonic_vals)
            value, details = await _bench_embedding(
                client=client,
                backend_url="http://localhost:8080",
                backend_type="test",
                task=task,
            )

        assert value == pytest.approx(50 / 1.5)
        assert details["num_docs"] == 50
        assert details["received"] == 50
        assert details["wall_seconds"] == 1.5

    @pytest.mark.asyncio
    async def test_embedding_empty_response(self):
        resp = _mock_json_response({"data": []})
        client = AsyncMock()
        client.post = AsyncMock(return_value=resp)

        task = _make_task(
            capability="embedding",
            model="test-embed",
            workload={"num_docs": 10, "avg_tokens_per_doc": 4},
        )

        monotonic_vals = [0.0, 1.0]
        with (
            patch("tinyagentos.benchmark.runner._fake_doc", return_value="fake"),
            patch("tinyagentos.benchmark.runner.time") as mock_time,
        ):
            mock_time.monotonic = MagicMock(side_effect=monotonic_vals)
            value, details = await _bench_embedding(
                client=client,
                backend_url="http://localhost:8080",
                backend_type="test",
                task=task,
            )

        assert value == 0.0
        assert details["received"] == 0

    @pytest.mark.asyncio
    async def test_embedding_non_dict_response(self):
        resp = _mock_json_response(["not", "a", "dict"])
        client = AsyncMock()
        client.post = AsyncMock(return_value=resp)

        task = _make_task(
            capability="embedding",
            model="test-embed",
            workload={"num_docs": 5, "avg_tokens_per_doc": 4},
        )

        monotonic_vals = [0.0, 1.0]
        with (
            patch("tinyagentos.benchmark.runner._fake_doc", return_value="fake"),
            patch("tinyagentos.benchmark.runner.time") as mock_time,
        ):
            mock_time.monotonic = MagicMock(side_effect=monotonic_vals)
            value, details = await _bench_embedding(
                client=client,
                backend_url="http://localhost:8080",
                backend_type="test",
                task=task,
            )

        assert value == 0.0
        assert details["received"] == 0


# ---------------------------------------------------------------------------
# Handler: _bench_reranking
# ---------------------------------------------------------------------------


class TestBenchReranking:
    @pytest.mark.asyncio
    async def test_rerank_p50(self):
        resp = _mock_json_response({"results": [{"index": 0, "score": 0.9}]})
        client = AsyncMock()
        client.post = AsyncMock(return_value=resp)

        task = _make_task(
            capability="reranking",
            model="test-reranker",
            metric=Metric.LATENCY_MS_P50,
            workload={"num_queries": 5, "candidates_per_query": 10},
        )

        # Each query calls monotonic twice (start + elapsed), so 5 queries = 10 calls
        monotonic_vals = [float(i) for i in range(10)]
        with (
            patch("tinyagentos.benchmark.runner._fake_doc", return_value="fake doc"),
            patch("tinyagentos.benchmark.runner.time") as mock_time,
        ):
            mock_time.monotonic = MagicMock(side_effect=monotonic_vals)
            value, details = await _bench_reranking(
                client=client,
                backend_url="http://localhost:8080",
                backend_type="test",
                task=task,
            )

        assert value > 0
        assert details["num_queries"] == 5
        assert details["candidates_per_query"] == 10
        assert "p50_ms" in details
        assert "p95_ms" in details


# ---------------------------------------------------------------------------
# Handler: _bench_llm_chat
# ---------------------------------------------------------------------------


class TestBenchLlmChat:
    @pytest.mark.asyncio
    async def test_llm_chat_tokens_per_sec(self):
        resp = _mock_json_response(
            {
                "choices": [{"message": {"content": "Hello world"}}],
                "usage": {"completion_tokens": 100},
            }
        )
        client = AsyncMock()
        client.post = AsyncMock(return_value=resp)

        task = _make_task(
            capability="llm-chat",
            model="test-llm",
            metric=Metric.TOKENS_PER_SEC,
            workload={"prompt": "Say hello.", "max_tokens": 128},
        )

        monotonic_vals = [0.0, 2.0]
        with patch("tinyagentos.benchmark.runner.time") as mock_time:
            mock_time.monotonic = MagicMock(side_effect=monotonic_vals)
            value, details = await _bench_llm_chat(
                client=client,
                backend_url="http://localhost:8080",
                backend_type="test",
                task=task,
            )

        assert value == pytest.approx(100 / 2.0)
        assert details["completion_tokens"] == 100
        assert details["max_tokens"] == 128
        assert details["wall_seconds"] == 2.0

    @pytest.mark.asyncio
    async def test_llm_chat_fallback_token_estimate(self):
        resp = _mock_json_response(
            {
                "choices": [{"message": {"content": "one two three four five"}}],
                "usage": {},
            }
        )
        client = AsyncMock()
        client.post = AsyncMock(return_value=resp)

        task = _make_task(
            capability="llm-chat",
            model="test-llm",
            workload={"prompt": "test", "max_tokens": 64},
        )

        monotonic_vals = [0.0, 1.0]
        with patch("tinyagentos.benchmark.runner.time") as mock_time:
            mock_time.monotonic = MagicMock(side_effect=monotonic_vals)
            value, details = await _bench_llm_chat(
                client=client,
                backend_url="http://localhost:8080",
                backend_type="test",
                task=task,
            )

        assert details["completion_tokens"] == 5
        assert value == 5.0

    @pytest.mark.asyncio
    async def test_llm_chat_zero_tokens_fallback(self):
        resp = _mock_json_response(
            {
                "choices": [{"message": {"content": ""}}],
                "usage": {"completion_tokens": 0},
            }
        )
        client = AsyncMock()
        client.post = AsyncMock(return_value=resp)

        task = _make_task(
            capability="llm-chat",
            model="test-llm",
            workload={"prompt": "test", "max_tokens": 64},
        )

        monotonic_vals = [0.0, 1.0]
        with patch("tinyagentos.benchmark.runner.time") as mock_time:
            mock_time.monotonic = MagicMock(side_effect=monotonic_vals)
            value, details = await _bench_llm_chat(
                client=client,
                backend_url="http://localhost:8080",
                backend_type="test",
                task=task,
            )

        # Empty content -> fallback: max(1, len("".split())) = max(1, 0) = 1
        assert details["completion_tokens"] == 1
        assert value == 1.0


# ---------------------------------------------------------------------------
# Handler: _bench_image_generation
# ---------------------------------------------------------------------------


class TestBenchImageGeneration:
    @pytest.mark.asyncio
    async def test_image_gen_default_backend(self):
        resp = _mock_json_response({"data": [{"url": "http://img.example/1.png"}]})
        client = AsyncMock()
        client.post = AsyncMock(return_value=resp)

        task = _make_task(
            capability="image-generation",
            model="sd-model",
            metric=Metric.SECONDS_PER_IMAGE,
            workload={"size": "256x256", "steps": 4, "prompt": "benchmark"},
        )

        monotonic_vals = [0.0, 3.5]
        with patch("tinyagentos.benchmark.runner.time") as mock_time:
            mock_time.monotonic = MagicMock(side_effect=monotonic_vals)
            value, details = await _bench_image_generation(
                client=client,
                backend_url="http://localhost:8080",
                backend_type="ollama",
                task=task,
            )

        assert value == pytest.approx(3.5)
        assert details["size"] == "256x256"
        assert details["steps"] == 4

    @pytest.mark.asyncio
    async def test_image_gen_sd_cpp_backend(self):
        resp = _mock_json_response({"images": ["base64data"]})
        client = AsyncMock()
        client.post = AsyncMock(return_value=resp)

        task = _make_task(
            capability="image-generation",
            model="sd-model",
            workload={"size": "512x512", "steps": 8, "prompt": "test"},
        )

        monotonic_vals = [0.0, 5.0]
        with patch("tinyagentos.benchmark.runner.time") as mock_time:
            mock_time.monotonic = MagicMock(side_effect=monotonic_vals)
            value, details = await _bench_image_generation(
                client=client,
                backend_url="http://localhost:7864",
                backend_type="sd-cpp",
                task=task,
            )

        assert value == pytest.approx(5.0)
        assert details["size"] == "512x512"
        assert details["steps"] == 8

        # Verify sd-cpp uses the correct endpoint
        call_args = client.post.call_args
        assert "/sdapi/v1/txt2img" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_image_gen_payload_contains_cfg_and_seed_for_sd_cpp(self):
        resp = _mock_json_response({"images": ["base64data"]})
        client = AsyncMock()
        client.post = AsyncMock(return_value=resp)

        task = _make_task(
            capability="image-generation",
            model="sd-model",
            workload={"size": "256x256", "steps": 2, "prompt": "bench"},
        )

        with patch("tinyagentos.benchmark.runner.time") as mock_time:
            mock_time.monotonic = MagicMock(side_effect=[0.0, 1.0])
            await _bench_image_generation(
                client=client,
                backend_url="http://localhost:7864",
                backend_type="sd-cpp",
                task=task,
            )

        call_kwargs = client.post.call_args
        payload = call_kwargs[1]["json"]
        assert payload["cfg_scale"] == 1.0
        assert payload["seed"] == 42
        assert payload["sampler_name"] == "euler_a"


# ---------------------------------------------------------------------------
# SuiteResult.to_dict
# ---------------------------------------------------------------------------


class TestSuiteResult:
    def test_to_dict(self):
        result = SuiteResult(
            task_id="t1",
            capability="embedding",
            model="m",
            metric=Metric.DOCS_PER_SEC,
            value=42.5,
            unit="docs/s",
            status="ok",
            elapsed_seconds=1.2,
            error=None,
            measured_at=1000.0,
            details={"num_docs": 50},
        )
        d = result.to_dict()
        assert d["task_id"] == "t1"
        assert d["capability"] == "embedding"
        assert d["model"] == "m"
        assert d["metric"] == "docs_per_sec"
        assert d["value"] == 42.5
        assert d["unit"] == "docs/s"
        assert d["status"] == "ok"
        assert d["elapsed_seconds"] == 1.2
        assert d["error"] is None
        assert d["measured_at"] == 1000.0
        assert d["details"] == {"num_docs": 50}

    def test_to_dict_none_value(self):
        result = SuiteResult(
            task_id="t2",
            capability="embedding",
            model="m",
            metric=Metric.DOCS_PER_SEC,
            value=None,
            unit="docs/s",
            status="skipped",
            elapsed_seconds=0.0,
            error="no backend",
        )
        d = result.to_dict()
        assert d["value"] is None
        assert d["status"] == "skipped"
        assert d["error"] == "no backend"


# ---------------------------------------------------------------------------
# BackendNotAvailable
# ---------------------------------------------------------------------------


class TestBackendNotAvailable:
    def test_is_runtime_error(self):
        exc = BackendNotAvailable("test message")
        assert isinstance(exc, RuntimeError)
        assert str(exc) == "test message"

    def test_caught_as_runtime_error(self):
        with pytest.raises(RuntimeError):
            raise BackendNotAvailable("x")

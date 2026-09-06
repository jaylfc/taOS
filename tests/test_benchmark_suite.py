"""Unit tests for the benchmark suite definitions.

Covers Metric enum values, SuiteTask/SuiteResult/BenchmarkSuite dataclass
construction, SuiteResult.to_dict(), and BenchmarkSuite.default().
"""
from __future__ import annotations

import time
from typing import Optional

import pytest

from tinyagentos.benchmark.suite import BenchmarkSuite, Metric, SuiteResult, SuiteTask


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_task(
    *,
    task_id: str = "test-task",
    capability: str = "embedding",
    model: str = "test-model",
    metric: Metric = Metric.DOCS_PER_SEC,
    description: str = "test task",
    workload: dict | None = None,
    timeout_seconds: float = 120.0,
    optional: bool = False,
) -> SuiteTask:
    return SuiteTask(
        id=task_id,
        capability=capability,
        model=model,
        metric=metric,
        description=description,
        workload=workload or {},
        timeout_seconds=timeout_seconds,
        optional=optional,
    )


def _make_result(
    *,
    task_id: str = "test-task",
    capability: str = "embedding",
    model: str = "test-model",
    metric: Metric = Metric.DOCS_PER_SEC,
    value: Optional[float] = 42.5,
    unit: str = "docs/s",
    status: str = "ok",
    elapsed_seconds: float = 1.2,
    error: Optional[str] = None,
    measured_at: float = 1000.0,
    details: dict | None = None,
) -> SuiteResult:
    return SuiteResult(
        task_id=task_id,
        capability=capability,
        model=model,
        metric=metric,
        value=value,
        unit=unit,
        status=status,
        elapsed_seconds=elapsed_seconds,
        error=error,
        measured_at=measured_at,
        details=details or {},
    )


# ---------------------------------------------------------------------------
# Metric enum
# ---------------------------------------------------------------------------


class TestMetric:
    @pytest.mark.parametrize(
        "member,expected",
        [
            (Metric.DOCS_PER_SEC, "docs_per_sec"),
            (Metric.TOKENS_PER_SEC, "tokens_per_sec"),
            (Metric.SECONDS_PER_STEP, "seconds_per_step"),
            (Metric.SECONDS_PER_IMAGE, "seconds_per_image"),
            (Metric.RTF, "realtime_factor"),
            (Metric.LATENCY_MS_P50, "latency_ms_p50"),
            (Metric.LATENCY_MS_P95, "latency_ms_p95"),
        ],
    )
    def test_metric_values(self, member: Metric, expected: str):
        assert member.value == expected

    def test_metric_is_str_subclass(self):
        assert isinstance(Metric.DOCS_PER_SEC, str)
        assert Metric.DOCS_PER_SEC == "docs_per_sec"

    def test_all_metrics_count(self):
        assert len(Metric) == 7


# ---------------------------------------------------------------------------
# SuiteTask
# ---------------------------------------------------------------------------


class TestSuiteTask:
    def test_defaults(self):
        task = _make_task()
        assert task.timeout_seconds == 120.0
        assert task.optional is False
        assert task.workload == {}

    def test_custom_values(self):
        task = _make_task(
            task_id="custom",
            capability="llm-chat",
            model="llama-3",
            metric=Metric.TOKENS_PER_SEC,
            description="custom task",
            workload={"max_tokens": 256},
            timeout_seconds=30.0,
            optional=True,
        )
        assert task.id == "custom"
        assert task.capability == "llm-chat"
        assert task.model == "llama-3"
        assert task.metric == Metric.TOKENS_PER_SEC
        assert task.description == "custom task"
        assert task.workload == {"max_tokens": 256}
        assert task.timeout_seconds == 30.0
        assert task.optional is True

    def test_empty_workload(self):
        task = _make_task(workload={})
        assert task.workload == {}


# ---------------------------------------------------------------------------
# SuiteResult
# ---------------------------------------------------------------------------


class TestSuiteResult:
    def test_defaults(self):
        before = time.time()
        result = SuiteResult(
            task_id="t",
            capability="embedding",
            model="m",
            metric=Metric.DOCS_PER_SEC,
            value=1.0,
            unit="docs/s",
            status="ok",
            elapsed_seconds=0.5,
        )
        assert result.error is None
        assert result.details == {}
        assert result.measured_at >= before

    def test_custom_measured_at(self):
        result = _make_result(measured_at=5000.0)
        assert result.measured_at == 5000.0

    def test_none_value(self):
        result = _make_result(value=None, status="error", error="failed")
        assert result.value is None
        assert result.status == "error"
        assert result.error == "failed"

    def test_all_statuses(self):
        for status in ("ok", "skipped", "error", "timeout"):
            result = _make_result(status=status)
            assert result.status == status


# ---------------------------------------------------------------------------
# SuiteResult.to_dict
# ---------------------------------------------------------------------------


class TestSuiteResultToDict:
    def test_to_dict_full(self):
        result = _make_result(
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
        assert d == {
            "task_id": "t1",
            "capability": "embedding",
            "model": "m",
            "metric": "docs_per_sec",
            "value": 42.5,
            "unit": "docs/s",
            "status": "ok",
            "elapsed_seconds": 1.2,
            "error": None,
            "measured_at": 1000.0,
            "details": {"num_docs": 50},
        }

    def test_to_dict_metric_value_is_string(self):
        result = _make_result(metric=Metric.TOKENS_PER_SEC)
        d = result.to_dict()
        assert d["metric"] == "tokens_per_sec"

    def test_to_dict_none_value(self):
        result = _make_result(value=None, status="skipped", error="no backend")
        d = result.to_dict()
        assert d["value"] is None
        assert d["status"] == "skipped"
        assert d["error"] == "no backend"

    def test_to_dict_empty_details(self):
        result = _make_result(details={})
        d = result.to_dict()
        assert d["details"] == {}

    def test_to_dict_preserves_all_metric_types(self):
        for metric in Metric:
            result = _make_result(metric=metric)
            d = result.to_dict()
            assert d["metric"] == metric.value


# ---------------------------------------------------------------------------
# BenchmarkSuite
# ---------------------------------------------------------------------------


class TestBenchmarkSuite:
    def test_construction(self):
        tasks = [_make_task(task_id="a"), _make_task(task_id="b")]
        suite = BenchmarkSuite(name="my-suite", description="desc", tasks=tasks)
        assert suite.name == "my-suite"
        assert suite.description == "desc"
        assert len(suite.tasks) == 2

    def test_empty_tasks(self):
        suite = BenchmarkSuite(name="empty", description="none", tasks=[])
        assert suite.tasks == []


# ---------------------------------------------------------------------------
# BenchmarkSuite.default
# ---------------------------------------------------------------------------


class TestBenchmarkSuiteDefault:
    def test_returns_benchmark_suite(self):
        suite = BenchmarkSuite.default()
        assert isinstance(suite, BenchmarkSuite)
        assert suite.name == "default"

    def test_has_expected_task_count(self):
        suite = BenchmarkSuite.default()
        assert len(suite.tasks) == 6

    def test_all_tasks_optional(self):
        suite = BenchmarkSuite.default()
        for task in suite.tasks:
            assert task.optional is True

    def test_task_ids(self):
        suite = BenchmarkSuite.default()
        ids = [t.id for t in suite.tasks]
        assert ids == [
            "embed-bge-small",
            "embed-qwen3",
            "rerank-qwen3",
            "llm-tinyllama",
            "imggen-sd15-lcm",
            "whisper-tiny",
        ]

    def test_capabilities(self):
        suite = BenchmarkSuite.default()
        caps = [t.capability for t in suite.tasks]
        assert caps == [
            "embedding",
            "embedding",
            "reranking",
            "llm-chat",
            "image-generation",
            "speech-to-text",
        ]

    def test_metrics(self):
        suite = BenchmarkSuite.default()
        metrics = [t.metric for t in suite.tasks]
        assert metrics == [
            Metric.DOCS_PER_SEC,
            Metric.DOCS_PER_SEC,
            Metric.LATENCY_MS_P50,
            Metric.TOKENS_PER_SEC,
            Metric.SECONDS_PER_IMAGE,
            Metric.RTF,
        ]

    def test_default_timeouts(self):
        suite = BenchmarkSuite.default()
        timeouts = {t.id: t.timeout_seconds for t in suite.tasks}
        assert timeouts["embed-bge-small"] == 60.0
        assert timeouts["embed-qwen3"] == 60.0
        assert timeouts["rerank-qwen3"] == 60.0
        assert timeouts["llm-tinyllama"] == 90.0
        assert timeouts["imggen-sd15-lcm"] == 180.0
        assert timeouts["whisper-tiny"] == 60.0

    def test_workload_keys_present(self):
        suite = BenchmarkSuite.default()
        embed_task = suite.tasks[0]
        assert "num_docs" in embed_task.workload
        assert "avg_tokens_per_doc" in embed_task.workload

    def test_models(self):
        suite = BenchmarkSuite.default()
        models = [t.model for t in suite.tasks]
        assert models == [
            "bge-small-en-v1.5",
            "qwen3-embedding-0.6b",
            "qwen3-reranker-0.6b",
            "tinyllama-1.1b",
            "dreamshaper-8-lcm",
            "whisper-tiny",
        ]

    def test_descriptions_non_empty(self):
        suite = BenchmarkSuite.default()
        for task in suite.tasks:
            assert len(task.description) > 0

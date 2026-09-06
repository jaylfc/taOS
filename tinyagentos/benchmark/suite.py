"""Canonical benchmark suite definitions.

A SuiteTask is a single measurable operation: "embed 100 docs with
bge-small", "generate a 256x256 image in 4 LCM steps", "transcribe a 10s
audio clip". Each task declares the capability it exercises, the target
model, the workload shape, and the metric it reports.

The default suite is intentionally small and cheap — the goal is a few
numbers within a couple of minutes, not an exhaustive profile. Custom
suites can be loaded from YAML or built programmatically for manual runs.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional


class Metric(str, Enum):
    DOCS_PER_SEC = "docs_per_sec"
    TOKENS_PER_SEC = "tokens_per_sec"
    SECONDS_PER_STEP = "seconds_per_step"
    SECONDS_PER_IMAGE = "seconds_per_image"
    RTF = "realtime_factor"
    LATENCY_MS_P50 = "latency_ms_p50"
    LATENCY_MS_P95 = "latency_ms_p95"


@dataclass
class SuiteTask:
    """One benchmark measurement."""
    id: str
    capability: str                       # matches Capability enum values
    model: str                            # target model id (must be loadable by the worker)
    metric: Metric
    description: str
    workload: dict                        # task-specific parameters
    timeout_seconds: float = 120.0
    optional: bool = False                # skip on failure instead of erroring


@dataclass
class SuiteResult:
    """Outcome of one SuiteTask."""
    task_id: str
    capability: str
    model: str
    metric: Metric
    value: Optional[float]                # None on failure
    unit: str
    status: str                           # "ok" | "skipped" | "error" | "timeout"
    elapsed_seconds: float
    error: Optional[str] = None
    measured_at: float = field(default_factory=time.time)
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "capability": self.capability,
            "model": self.model,
            "metric": self.metric.value,
            "value": self.value,
            "unit": self.unit,
            "status": self.status,
            "elapsed_seconds": self.elapsed_seconds,
            "error": self.error,
            "measured_at": self.measured_at,
            "details": self.details,
        }


@dataclass
class BenchmarkSuite:
    """A named collection of SuiteTasks."""
    name: str
    description: str
    tasks: list[SuiteTask]

    @classmethod
    def default(cls) -> "BenchmarkSuite":
        """The small cheap suite run on first worker join.

        Every task is marked ``optional`` so a worker that can't handle a
        given capability (no image-gen backend, no embedding backend)
        simply skips that task rather than failing the whole run.
        """
        return cls(
            name="default",
            description="Small first-join suite: embed, rerank, llm-chat, image-gen, whisper",
            tasks=[
                SuiteTask(
                    id="embed-bge-small",
                    capability="embedding",
                    model="bge-small-en-v1.5",
                    metric=Metric.DOCS_PER_SEC,
                    description="Embedding throughput — 50 short docs",
                    workload={"num_docs": 50, "avg_tokens_per_doc": 64},
                    timeout_seconds=60.0,
                    optional=True,
                ),
                SuiteTask(
                    id="embed-qwen3",
                    capability="embedding",
                    model="qwen3-embedding-0.6b",
                    metric=Metric.DOCS_PER_SEC,
                    description="NPU embedding throughput — 50 short docs",
                    workload={"num_docs": 50, "avg_tokens_per_doc": 64},
                    timeout_seconds=60.0,
                    optional=True,
                ),
                SuiteTask(
                    id="rerank-qwen3",
                    capability="reranking",
                    model="qwen3-reranker-0.6b",
                    metric=Metric.LATENCY_MS_P50,
                    description="Rerank latency — 1 query × 20 candidates, p50",
                    workload={"num_queries": 10, "candidates_per_query": 20},
                    timeout_seconds=60.0,
                    optional=True,
                ),
                SuiteTask(
                    id="llm-tinyllama",
                    capability="llm-chat",
                    model="tinyllama-1.1b",
                    metric=Metric.TOKENS_PER_SEC,
                    description="LLM generation throughput — 1 prompt, 128 output tokens",
                    workload={"max_tokens": 128, "prompt": "Write one short sentence about Orange Pi."},
                    timeout_seconds=90.0,
                    optional=True,
                ),
                SuiteTask(
                    id="imggen-sd15-lcm",
                    capability="image-generation",
                    model="dreamshaper-8-lcm",
                    metric=Metric.SECONDS_PER_IMAGE,
                    description="Image generation — 1 image, 256x256, 4 steps",
                    workload={"size": "256x256", "steps": 2, "prompt": "benchmark"},
                    timeout_seconds=180.0,
                    optional=True,
                ),
                SuiteTask(
                    id="whisper-tiny",
                    capability="speech-to-text",
                    model="whisper-tiny",
                    metric=Metric.RTF,
                    description="Whisper transcription — 10s clip, realtime factor",
                    workload={"clip_seconds": 10.0},
                    timeout_seconds=60.0,
                    optional=True,
                ),
            ],
        )

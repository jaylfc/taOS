"""Scheduler data types, Task, Resource signatures, priority, error classes."""
from __future__ import annotations

import enum
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional


class Capability(str, enum.Enum):
    """Stable capability identifiers for routing."""
    IMAGE_GENERATION = "image-generation"
    EMBEDDING = "embedding"
    RERANKING = "reranking"
    LLM_CHAT = "llm-chat"
    SPEECH_TO_TEXT = "speech-to-text"
    TEXT_TO_SPEECH = "text-to-speech"
    VISION = "vision"


class Priority(enum.IntEnum):
    """Lower integer wins. Matches asyncio.PriorityQueue ordering.

    Within the same priority, FIFO by submission time.
    """
    INTERACTIVE_USER = 10
    INTERACTIVE_AGENT = 20
    BACKGROUND = 30
    BATCH = 40


class TaskStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETE = "complete"
    ERROR = "error"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class ResourceSignature:
    """Identifies a resource's runtime environment precisely enough to refuse
    mismatched work. See resource-scheduler.md §Platform and runtime signature.
    """
    platform: str                # "rk3588" | "cuda-sm_86" | "cpu-aarch64" | ...
    runtime: str                 # "librknnrt" | "cuda" | "vulkan" | "none"
    runtime_version: str = ""    # "2.3.0" | "12.4" | ""

    def matches(self, required: "ResourceSignature") -> bool:
        """True if this signature satisfies the required signature.

        Empty strings on the required side mean "any". Version matching is
        currently exact or prefix ("2.3" matches "2.3.0"); full semver
        constraints are Phase 2.
        """
        if required.platform and required.platform != self.platform:
            return False
        if required.runtime and required.runtime != self.runtime:
            return False
        if required.runtime_version:
            if not self.runtime_version.startswith(required.runtime_version):
                return False
        return True


@dataclass
class ResourceRef:
    """A reference to a resource by name, with optional scheduling hints."""
    name: str
    max_wait_ms: Optional[int] = None  # Phase 1 ignores this, Phase 2 enforces


@dataclass
class Task:
    """A unit of inference work submitted to the scheduler.

    The payload is an async callable that takes the chosen Resource as its
    only argument and returns whatever the caller wants (dict, bytes, etc.).
    The scheduler itself is opaque to the payload shape, it only sees Future.
    """
    capability: Capability
    payload: Callable[["Resource"], Awaitable[Any]]
    preferred_resources: list[ResourceRef]
    priority: Priority = Priority.INTERACTIVE_AGENT
    submitter: str = "unknown"
    estimated_seconds: float = 1.0
    estimated_memory_mb: int = 0
    estimated_vram_mb: int = 0
    required_signatures: list[ResourceSignature] = field(default_factory=list)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    submitted_at: float = field(default_factory=time.time)


@dataclass
class TaskRecord:
    """Observability record, what ran where, when, how long, with what outcome.

    Bounded history kept in memory; surfaced via /api/scheduler/tasks for
    the Activity app.
    """
    task_id: str
    capability: str
    submitter: str
    priority: int
    resource: Optional[str]
    status: TaskStatus
    submitted_at: float
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    elapsed_seconds: Optional[float] = None
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "capability": self.capability,
            "submitter": self.submitter,
            "priority": int(self.priority),
            "resource": self.resource,
            "status": self.status.value,
            "submitted_at": self.submitted_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "elapsed_seconds": self.elapsed_seconds,
            "error": self.error,
        }


class SchedulerError(Exception):
    """Base class for scheduler errors."""


class NoResourceAvailableError(SchedulerError):
    """No registered resource could run the requested task."""

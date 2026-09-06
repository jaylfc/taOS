"""loaded_model.py -- resident-model records for the core-aware scheduler.

A LoadedModel represents a model that is currently resident on a backend
(memory allocated, InferenceSession open). The scheduler maintains a
registry of these records and uses them for both eviction decisions and
resource-hold accounting.

Priority classes determine the order in which the scheduler targets
residents for shrink-reload or eviction when a new load creates pressure.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Priority class constants
# ---------------------------------------------------------------------------

class PriorityClass:
    """Symbolic names for the three resident priority levels.

    Higher value means the scheduler will target this model for
    shrink/eviction before lower values.

      always_resident (0) -- pinned, never shrunk or evicted.
      interactive     (1) -- default for user-facing agents.
      background      (2) -- ingest / batch; shrunk and evicted first.
    """
    ALWAYS_RESIDENT = "always_resident"
    INTERACTIVE = "interactive"
    BACKGROUND = "background"


PRIORITY_CLASSES = [
    PriorityClass.ALWAYS_RESIDENT,
    PriorityClass.INTERACTIVE,
    PriorityClass.BACKGROUND,
]

# Numeric rank used for comparison: higher rank -> evict first.
_PRIORITY_RANK: dict[str, int] = {
    PriorityClass.ALWAYS_RESIDENT: 0,
    PriorityClass.INTERACTIVE: 1,
    PriorityClass.BACKGROUND: 2,
}


def priority_rank(priority: str) -> int:
    """Numeric rank for a priority string. Unknown strings map to 0.

    Used for victim selection: pick the resident with the highest rank
    first (background before interactive; never always_resident).
    """
    return _PRIORITY_RANK.get(priority, 0)


# ---------------------------------------------------------------------------
# LoadedModel record
# ---------------------------------------------------------------------------

@dataclass
class LoadedModel:
    """A model currently resident on a backend.

    Attributes:
        model_id: stable model identifier matching the backend catalog.
        backend: backend type string, e.g. "rkllama", "llama-cpp", "vllm".
        memory_mb_used: memory this model holds on the device in MB.
        resource_holds: subset of the backend's resource shape currently
            claimed by this model. For RK3588 NPU this is
            ``{"cores": [0, 1, 2]}``. For CPU/CUDA backends it is
            ``{}`` (empty -- no discrete allocation to track).
        tp_mode: tensor-parallel mode baked into the InferenceSession at
            load time. "all" means all available cores; "0,1" means cores
            0 and 1; "0" means core 0 only; "" for backends that do not
            use tp_mode.
        loaded_at: monotonic epoch (time.time()) when this model was
            loaded.
        last_used_at: monotonic epoch of the most recent inference
            request on this model. Used for LRU eviction ordering.
        priority: one of PRIORITY_CLASSES.
        pinned: when True the model is treated as always_resident
            regardless of the priority field.
    """

    model_id: str
    backend: str
    memory_mb_used: int
    resource_holds: dict[str, Any] = field(default_factory=dict)
    tp_mode: str = ""
    loaded_at: float = field(default_factory=time.time)
    last_used_at: float = field(default_factory=time.time)
    priority: str = PriorityClass.INTERACTIVE
    pinned: bool = False

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def cores_held(self) -> list[int]:
        """NPU core indices held by this model, or empty list."""
        return list(self.resource_holds.get("cores", []))

    def gpu_ids_held(self) -> list[int]:
        """GPU device IDs held by this model, or empty list."""
        return list(self.resource_holds.get("gpu_ids", []))

    def is_always_resident(self) -> bool:
        return self.pinned or self.priority == PriorityClass.ALWAYS_RESIDENT

    def effective_priority_rank(self) -> int:
        """Numeric eviction priority. Always-resident models return -1
        so they are never selected as victims."""
        if self.is_always_resident():
            return -1
        return priority_rank(self.priority)

    def touch(self) -> None:
        """Update last_used_at to now. Call on every inference request."""
        self.last_used_at = time.time()

    def to_dict(self) -> dict:
        return {
            "model_id": self.model_id,
            "backend": self.backend,
            "memory_mb_used": self.memory_mb_used,
            "resource_holds": self.resource_holds,
            "tp_mode": self.tp_mode,
            "loaded_at": self.loaded_at,
            "last_used_at": self.last_used_at,
            "priority": self.priority,
            "pinned": self.pinned,
        }

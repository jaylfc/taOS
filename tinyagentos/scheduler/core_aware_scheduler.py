"""core_aware_scheduler.py -- Phase 1.5 NPU core-aware load manager.

This module extends the Phase 1 Scheduler with a second scheduling
dimension: NPU core allocation. The existing Scheduler routes tasks
to Resources using memory admission. This module adds a model
residency layer on top: it decides which tp_mode to use for each new
load, handles eviction and shrink-reload when cores are contested, and
maintains the LoadedModel registry.

The Scheduler class itself is unchanged. CoreAwareModelScheduler wraps
it (composition, not subclass) and is the entry point for model-load
decisions. Task dispatch continues through the existing Scheduler.

Phase 1.5 integration point
----------------------------
TODO (#172): Wire load_with_core_awareness() into the sequential
loading path once Phase 1.5 sequential-loading work lands. The call
site belongs in the backend dispatch path where a model needs to be
loaded before inference can begin -- that wiring is tracked as a
separate task and intentionally absent here to keep this diff scoped.

Design notes
------------
- RK3588 NPU: three physical cores (0, 1, 2). tp_mode is a string
  baked into the RKNN InferenceSession at open time. Changing it
  requires close + reopen of the session, which this module models
  as shrink-reload.
- CUDA / CPU backends: no discrete core allocation. The resource-hold
  pressure check degenerates to a no-op beyond the memory budget check.
- Priority: always_resident models are never shrunk or evicted.
  background models are shrunk/evicted before interactive ones.
- max_wait_ms: not enforced in Phase 1.5 (asyncio wait-for deferred
  to Phase 2). The parameter is accepted and stored so callers can
  declare intent; the current implementation acts as if wait=0 and
  goes straight to shrink/evict/reject.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable, Optional

from tinyagentos.scheduler.loaded_model import (
    LoadedModel,
    PriorityClass,
    priority_rank,
)
from tinyagentos.scheduler.resource_shape import (
    BackendResourceShape,
    get_default_shape,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Error type
# ---------------------------------------------------------------------------

class ResourceContention(Exception):
    """Raised when a model load cannot proceed after exhausting all
    strategies (wait, shrink-reload, evict-reload).

    Callers should convert this to an HTTP 503 response.
    """


# ---------------------------------------------------------------------------
# tp_mode helpers for RK3588 NPU
# ---------------------------------------------------------------------------

_ALL_CORES_RK3588 = [0, 1, 2]


def _cores_to_tp_mode(cores: list[int]) -> str:
    """Convert a sorted list of core indices to a tp_mode string.

    Rules:
      [0, 1, 2] -> "all"
      [0, 1]    -> "0,1"
      [2]       -> "2"
      []        -> "" (should not happen in normal operation)
    """
    if not cores:
        return ""
    if sorted(cores) == _ALL_CORES_RK3588:
        return "all"
    return ",".join(str(c) for c in sorted(cores))


def _tp_mode_to_cores(tp_mode: str) -> list[int]:
    """Reverse of _cores_to_tp_mode.

    "all" -> [0, 1, 2]
    "0,1" -> [0, 1]
    "0"   -> [0]
    ""    -> []
    """
    if not tp_mode:
        return []
    if tp_mode == "all":
        return list(_ALL_CORES_RK3588)
    try:
        return [int(c.strip()) for c in tp_mode.split(",")]
    except ValueError:
        logger.warning("Cannot parse tp_mode %r; treating as no-core", tp_mode)
        return []


# ---------------------------------------------------------------------------
# Core-aware model scheduler
# ---------------------------------------------------------------------------

class CoreAwareModelScheduler:
    """Manages resident model records with NPU-core-aware scheduling.

    This class is the Phase 1.5 load-decision layer. It does NOT run
    inference itself -- that continues to flow through Scheduler.submit().
    It answers one question: "given the current residents and the incoming
    request, what tp_mode should the new model use, and what (if anything)
    needs to be evicted first?"

    Args:
        shape_lookup: optional callable that takes a backend_type string
            and returns a BackendResourceShape. Defaults to
            get_default_shape() from resource_shape.py.
        max_wait_ms: maximum time to wait for a resident to idle-evict
            before falling back to shrink/evict. Phase 1.5 does not
            enforce this timeout; it is stored for Phase 2.
        reload_fn: optional async callable(model: LoadedModel, new_tp_mode: str)
            invoked when a resident is shrunk to a smaller mask. If None,
            shrink operations update the registry only (no real reload).
        evict_fn: optional async callable(model: LoadedModel) invoked when
            a resident is evicted. If None, evictions update the registry
            only.
    """

    def __init__(
        self,
        shape_lookup: Optional[Callable[[str], BackendResourceShape]] = None,
        max_wait_ms: int = 500,
        reload_fn: Optional[Callable[[LoadedModel, str], Any]] = None,
        evict_fn: Optional[Callable[[LoadedModel], Any]] = None,
    ):
        self._shape_lookup = shape_lookup or get_default_shape
        self.max_wait_ms = max_wait_ms
        self._reload_fn = reload_fn
        self._evict_fn = evict_fn
        # Registry: model_id -> LoadedModel
        self._residents: dict[str, LoadedModel] = {}
        # Event bus: list of (event_name, dict) tuples appended in order.
        # Tests read this to verify that the right events fired.
        self.events: list[tuple[str, dict]] = []

    # -----------------------------------------------------------------------
    # Public registry API
    # -----------------------------------------------------------------------

    def residents(self) -> list[LoadedModel]:
        """Snapshot of all currently resident models."""
        return list(self._residents.values())

    def get_resident(self, model_id: str) -> Optional[LoadedModel]:
        """Look up a resident by model_id."""
        return self._residents.get(model_id)

    def register_loaded(self, model: LoadedModel) -> None:
        """Add a model to the resident registry.

        Used by tests and by the wiring code (Phase 1.5) to seed the
        registry after a real backend load completes.
        """
        self._residents[model.model_id] = model
        self._emit("model_loaded", {"model_id": model.model_id, "tp_mode": model.tp_mode})

    def mark_unloaded(self, model_id: str) -> Optional[LoadedModel]:
        """Remove a model from the resident registry and return it."""
        model = self._residents.pop(model_id, None)
        if model is not None:
            self._emit("model_unloaded", {"model_id": model_id})
        return model

    # -----------------------------------------------------------------------
    # Core-aware load entry point
    # -----------------------------------------------------------------------

    async def load_with_core_awareness(
        self,
        model_id: str,
        backend_name: str,
        requested_cores: Optional[list[int]] = None,
        priority: str = PriorityClass.INTERACTIVE,
        pinned: bool = False,
        memory_mb: int = 0,
        max_wait_ms: Optional[int] = None,
    ) -> LoadedModel:
        """Decide how to load model_id on backend_name.

        This method resolves resource pressure and returns a LoadedModel
        ready to be handed to the real backend load call. It may:
          - pick a tp_mode and return immediately (common case)
          - shrink-reload a lower-priority resident to free cores
          - evict a lower-priority resident to free cores
          - raise ResourceContention if no viable strategy exists

        Args:
            model_id: identifier for the model to load.
            backend_name: backend type string ("rkllama", "llama-cpp", ...).
            requested_cores: specific NPU core indices the caller wants.
                If None, the scheduler picks based on the default policy.
            priority: priority class for the new model.
            pinned: if True, treat as always_resident regardless of priority.
            memory_mb: estimated memory requirement in MB. 0 means unknown.
            max_wait_ms: per-call override for the wait budget.

        Returns:
            A LoadedModel record with the chosen tp_mode and resource_holds
            filled in. The record is NOT added to the resident registry by
            this method -- the caller should call register_loaded() after
            the real backend load succeeds.

        Raises:
            ResourceContention: no viable strategy to load the model.
        """
        shape = self._shape_lookup(backend_name)

        # Fast path: backend has no discrete cores -- memory-only check.
        if not shape.has_cores():
            return self._make_no_core_model(
                model_id, backend_name, memory_mb, priority, pinned
            )

        # Core-bearing backend (RK3588 NPU path).
        return await self._load_npu_model(
            model_id=model_id,
            backend_name=backend_name,
            shape=shape,
            requested_cores=requested_cores,
            priority=priority,
            pinned=pinned,
            memory_mb=memory_mb,
        )

    # -----------------------------------------------------------------------
    # NPU load path
    # -----------------------------------------------------------------------

    async def _load_npu_model(
        self,
        model_id: str,
        backend_name: str,
        shape: BackendResourceShape,
        requested_cores: Optional[list[int]],
        priority: str,
        pinned: bool,
        memory_mb: int,
    ) -> LoadedModel:
        """Core-aware load path for backends with NPU cores."""

        available_cores = list(shape.cores or [])
        all_npu_residents = self._npu_residents()

        held_cores = self._held_cores(all_npu_residents)
        free_cores = [c for c in available_cores if c not in held_cores]

        if requested_cores:
            wanted = sorted(requested_cores)
        else:
            # Default policy: pick how many cores we want based on resident count.
            # With n residents already loaded, the new model should get:
            #   n==0: all cores
            #   n==1: remaining after shrinking the solo model to 2 cores
            #   n==2: remaining 1 core
            #   n>=3: no free cores -- must evict
            n = len(all_npu_residents)
            if n == 0:
                wanted = list(available_cores)
            elif n == 1:
                # The solo resident holds all cores; we want to give the
                # incoming model 1 core, leaving 2 for the existing one.
                wanted = [available_cores[-1]]  # prefer last core (core 2)
            else:
                # n >= 2: we want exactly 1 free core
                wanted = free_cores[:1] if free_cores else []

        # If wanted is empty all cores are occupied -- need to evict.
        if not wanted:
            return await self._resolve_core_pressure(
                model_id=model_id,
                backend_name=backend_name,
                wanted_cores=available_cores[:1],  # claim any single core after eviction
                occupied_by=self._cores_occupied_by(available_cores, all_npu_residents),
                incoming_priority=priority,
                incoming_pinned=pinned,
                memory_mb=memory_mb,
                available_cores=available_cores,
            )

        # Check if the wanted cores are all free.
        occupied = {c: m for c, m in self._cores_occupied_by(wanted, all_npu_residents).items()}

        if not occupied:
            # All wanted cores are free -- no pressure.
            return self._build_npu_model(
                model_id, backend_name, wanted, memory_mb, priority, pinned
            )

        # Some wanted cores are occupied -- resolve pressure.
        return await self._resolve_core_pressure(
            model_id=model_id,
            backend_name=backend_name,
            wanted_cores=wanted,
            occupied_by=occupied,
            incoming_priority=priority,
            incoming_pinned=pinned,
            memory_mb=memory_mb,
            available_cores=available_cores,
        )

    # -----------------------------------------------------------------------
    # tp_mode policy sync helper (for external callers / tests)
    # -----------------------------------------------------------------------

    def _pick_tp_mode(
        self,
        model_id: str,
        backend_name: str,
        npu_residents: list[LoadedModel],
    ) -> str:
        """Synchronous helper: return the tp_mode string for a new model.

        Uses the default allocation policy. Does not account for contested
        cores. Use load_with_core_awareness for the authoritative decision.
        """
        available = list(self._shape_lookup(backend_name).cores or [])
        if not available:
            return ""
        held = self._held_cores(npu_residents)
        free = [c for c in available if c not in held]
        n = len(npu_residents)
        if n == 0:
            cores = available
        elif n == 1:
            cores = [available[-1]]
        else:
            cores = free[:1] if free else []
        return _cores_to_tp_mode(cores)

    # -----------------------------------------------------------------------
    # Pressure resolution
    # -----------------------------------------------------------------------

    async def _resolve_core_pressure(
        self,
        model_id: str,
        backend_name: str,
        wanted_cores: list[int],
        occupied_by: dict[int, LoadedModel],
        incoming_priority: str,
        incoming_pinned: bool,
        memory_mb: int,
        available_cores: list[int],
    ) -> LoadedModel:
        """Attempt to free cores via shrink-reload, evict-reload, or reject."""

        incoming_is_pinned = (
            incoming_pinned
            or incoming_priority == PriorityClass.ALWAYS_RESIDENT
        )
        incoming_rank = -1 if incoming_is_pinned else priority_rank(incoming_priority)

        # Gather the unique victim models that hold the wanted cores.
        victims: dict[str, LoadedModel] = {}
        for model in occupied_by.values():
            if model.model_id not in victims:
                victims[model.model_id] = model

        # Separate into evictable and non-evictable.
        # A victim is evictable if:
        #   - it is not always_resident / pinned
        #   - its priority rank >= incoming rank (equal or lower priority)
        #     OR incoming is pinned/always_resident (can evict anything)
        evictable: list[LoadedModel] = []
        non_evictable: list[LoadedModel] = []
        for m in victims.values():
            if m.effective_priority_rank() < 0:
                # always_resident -- never evictable
                non_evictable.append(m)
            elif incoming_is_pinned:
                # incoming is pinned: can evict any non-pinned resident
                evictable.append(m)
            elif m.effective_priority_rank() >= incoming_rank:
                # victim has equal or lower priority -- evictable
                evictable.append(m)
            else:
                # victim has strictly higher priority -- cannot evict
                non_evictable.append(m)

        if non_evictable:
            names = [m.model_id for m in non_evictable]
            raise ResourceContention(
                f"Cannot load {model_id!r} on {backend_name!r}: cores "
                f"{wanted_cores} are held by higher- or equal-priority "
                f"(always_resident) resident(s) {names}. "
                f"No lower-priority victim available."
            )

        if not evictable:
            raise ResourceContention(
                f"Cannot load {model_id!r}: no evictable resident holds "
                f"the wanted cores {wanted_cores}."
            )

        # Strategy 1: try shrink-reload. A victim holding more than 1 core
        # can be shrunk to free 1 core for the incoming model.
        for victim in sorted(
            evictable,
            key=lambda m: m.effective_priority_rank(),
            reverse=True,  # prefer to shrink lowest-priority first
        ):
            current_held = victim.cores_held()
            if len(current_held) > 1:
                # Shrink victim: give it one fewer core.
                shrunk_cores = current_held[:-1]
                freed_core = current_held[-1]
                # Make sure the freed core is one we actually want.
                wanted_set = set(wanted_cores)
                # Try to free a core from the wanted set if possible.
                for i, c in reversed(list(enumerate(current_held))):
                    if c in wanted_set:
                        freed_core = c
                        shrunk_cores = current_held[:i] + current_held[i + 1:]
                        break
                await self._shrink_reload(victim, shrunk_cores)
                return self._build_npu_model(
                    model_id, backend_name, [freed_core],
                    memory_mb, incoming_priority, incoming_pinned,
                )

        # Strategy 2: evict the lowest-priority victim entirely.
        victim = max(evictable, key=lambda m: m.effective_priority_rank())
        freed_cores = victim.cores_held()
        await self._evict_reload(victim)

        # Assign one of the freed cores.
        wanted_set = set(wanted_cores)
        assigned = next((c for c in freed_cores if c in wanted_set), freed_cores[0])
        return self._build_npu_model(
            model_id, backend_name, [assigned],
            memory_mb, incoming_priority, incoming_pinned,
        )

    # -----------------------------------------------------------------------
    # Shrink and evict helpers
    # -----------------------------------------------------------------------

    async def _shrink_reload(self, model: LoadedModel, new_cores: list[int]) -> None:
        """Shrink a resident to a smaller core mask.

        Updates the registry and invokes reload_fn if one was provided.
        Emits a 'model_shrunk' event.
        """
        old_tp = model.tp_mode
        new_tp = _cores_to_tp_mode(new_cores)
        logger.info(
            "scheduler: shrink-reload %r from tp_mode=%r to tp_mode=%r",
            model.model_id, old_tp, new_tp,
        )
        model.resource_holds = {"cores": list(new_cores)}
        model.tp_mode = new_tp
        self._emit("model_shrunk", {
            "model_id": model.model_id,
            "old_tp_mode": old_tp,
            "new_tp_mode": new_tp,
        })
        if self._reload_fn is not None:
            if asyncio.iscoroutinefunction(self._reload_fn):
                await self._reload_fn(model, new_tp)
            else:
                self._reload_fn(model, new_tp)

    async def _evict_reload(self, model: LoadedModel) -> None:
        """Evict a resident model from the registry.

        Removes the model record, invokes evict_fn if one was provided,
        and emits a 'model_evicted' event.
        """
        logger.info(
            "scheduler: evicting %r (priority=%s)", model.model_id, model.priority
        )
        self._residents.pop(model.model_id, None)
        self._emit("model_evicted", {
            "model_id": model.model_id,
            "priority": model.priority,
            "tp_mode": model.tp_mode,
        })
        if self._evict_fn is not None:
            if asyncio.iscoroutinefunction(self._evict_fn):
                await self._evict_fn(model)
            else:
                self._evict_fn(model)

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------

    def _npu_residents(self) -> list[LoadedModel]:
        """All residents that hold NPU cores."""
        return [m for m in self._residents.values() if m.cores_held()]

    def _held_cores(self, residents: list[LoadedModel]) -> list[int]:
        """Flat list of all cores currently held by the given residents."""
        out: list[int] = []
        for m in residents:
            out.extend(m.cores_held())
        return out

    def _cores_occupied_by(
        self,
        core_list: list[int],
        residents: list[LoadedModel],
    ) -> dict[int, LoadedModel]:
        """Map core_index -> LoadedModel for each core_list entry that is held."""
        occupancy: dict[int, LoadedModel] = {}
        for m in residents:
            for c in m.cores_held():
                if c in core_list:
                    occupancy[c] = m
        return occupancy

    def _make_no_core_model(
        self,
        model_id: str,
        backend_name: str,
        memory_mb: int,
        priority: str,
        pinned: bool,
    ) -> LoadedModel:
        """Build a LoadedModel for backends without discrete core allocation."""
        return LoadedModel(
            model_id=model_id,
            backend=backend_name,
            memory_mb_used=memory_mb,
            resource_holds={},
            tp_mode="",
            priority=priority,
            pinned=pinned,
        )

    def _build_npu_model(
        self,
        model_id: str,
        backend_name: str,
        cores: list[int],
        memory_mb: int,
        priority: str,
        pinned: bool,
    ) -> LoadedModel:
        """Build a LoadedModel for the RK3588 NPU path."""
        tp = _cores_to_tp_mode(cores)
        return LoadedModel(
            model_id=model_id,
            backend=backend_name,
            memory_mb_used=memory_mb,
            resource_holds={"cores": list(cores)},
            tp_mode=tp,
            priority=priority,
            pinned=pinned,
        )

    def _emit(self, event: str, data: dict) -> None:
        self.events.append((event, data))

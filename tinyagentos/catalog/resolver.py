"""Pure-function resolver for catalog model manifests.

Given a (manifest, variant, device, force) tuple, decides which backend
should serve the model and whether the chain needs an extra install step.
No I/O, no httpx, no cluster lookups — inputs are passed in by the caller.
This module is the single source of truth shared by the install dispatcher
and the frontend's compatibility classification (via /api/store/resolve).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Union


@dataclass(frozen=True)
class DeviceCapability:
    """Snapshot of a single device's resources, supplied by the caller.

    ``total_ram_mb`` / ``total_vram_mb`` are *capacity* (not current free) —
    dynamic unload makes free-now an unreliable signal. Disk stays "free"
    because nothing auto-evicts on disk.
    """
    device_id: str
    targets: tuple[str, ...]
    total_ram_mb: int
    total_vram_mb: int
    free_disk_mb: int
    installed_backends: tuple[str, ...]


@dataclass(frozen=True)
class BackendDep:
    """A single backend candidate listed under variant.requires.backends."""
    id: str
    targets: tuple[str, ...]
    min_ram_mb: int
    min_vram_mb: int = 0


@dataclass(frozen=True)
class ResolveOk:
    """Successful resolve. ``action`` tells the dispatcher whether the
    backend needs installing first."""
    backend_id: str
    variant_id: str
    action: Literal["use", "install_chain"]


@dataclass(frozen=True)
class ResolveErr:
    """Could not resolve. ``near_miss`` and ``suggestions`` feed the UI."""
    reason: str
    near_miss: dict[str, Any] = field(default_factory=dict)
    suggestions: list[str] = field(default_factory=list)


ResolveResult = Union[ResolveOk, ResolveErr]

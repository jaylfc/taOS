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


def _coerce_backends(raw: list[dict]) -> list[BackendDep]:
    """Normalize a YAML-loaded backends list into BackendDep objects."""
    out: list[BackendDep] = []
    for entry in raw or []:
        if not isinstance(entry, dict):
            continue
        try:
            out.append(
                BackendDep(
                    id=str(entry["id"]),
                    targets=tuple(entry.get("targets", []) or []),
                    min_ram_mb=int(entry.get("min_ram_mb", 0) or 0),
                    min_vram_mb=int(entry.get("min_vram_mb", 0) or 0),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _suggestions_for(blocked_by: str) -> list[str]:
    if blocked_by == "ram":
        return [
            "Pick a smaller variant",
            "Install on a device with more RAM",
        ]
    if blocked_by == "vram":
        return [
            "Pick a smaller variant",
            "Install on a device with a larger GPU",
        ]
    if blocked_by == "disk":
        return [
            "Pick a smaller variant",
            "Free up disk on this device",
            "Install on a worker with more disk",
        ]
    if blocked_by == "target":
        return [
            "Install on a device whose hardware can run this model",
            "Use 'Archive anyway' to download for later",
        ]
    return []


def _check_variant(
    variant: dict,
    device: DeviceCapability,
    *,
    force: bool,
) -> ResolveResult:
    """Try to resolve a single variant. Pure function."""
    deps = _coerce_backends(
        ((variant.get("requires") or {}).get("backends")) or []
    )
    if not deps:
        return ResolveErr(
            reason=f"variant {variant.get('id')!r} has no requires.backends",
            near_miss={"variant": variant.get("id"), "blocked_by": "schema"},
            suggestions=["Update the manifest to declare requires.backends"],
        )

    size_mb = int(variant.get("size_mb", 0) or 0)
    if size_mb > 0 and device.free_disk_mb < size_mb:
        # Disk gate runs even with force=True — you can't write to a full disk.
        return ResolveErr(
            reason=(
                f"variant {variant.get('id')!r} needs {size_mb} MB disk, "
                f"device has {device.free_disk_mb} MB free"
            ),
            near_miss={
                "variant": variant.get("id"),
                "blocked_by": "disk",
                "short_by_mb": size_mb - device.free_disk_mb,
            },
            suggestions=[
                "Pick a smaller variant",
                "Free up disk on this device",
                "Install on a worker with more disk",
            ],
        )

    closest_short_mb = -1
    closest_blocked_by = "target"
    closest_variant = variant.get("id")

    device_targets = set(device.targets)
    for dep in deps:
        # Gate 1: target intersection (bypassed by force).
        if not force and not (set(dep.targets) & device_targets):
            if closest_short_mb < 0:
                closest_blocked_by = "target"
            continue

        # Gate 2: total RAM (bypassed by force).
        if not force and device.total_ram_mb < dep.min_ram_mb:
            short = dep.min_ram_mb - device.total_ram_mb
            if closest_short_mb < 0 or short < closest_short_mb:
                closest_short_mb = short
                closest_blocked_by = "ram"
            continue

        # Gate 3: total VRAM if the dep declares a floor (bypassed by force).
        if not force and dep.min_vram_mb > 0 and device.total_vram_mb < dep.min_vram_mb:
            short = dep.min_vram_mb - device.total_vram_mb
            if closest_short_mb < 0 or short < closest_short_mb:
                closest_short_mb = short
                closest_blocked_by = "vram"
            continue

        # All gates passed — pick this dep.
        action: Literal["use", "install_chain"] = (
            "use" if dep.id in device.installed_backends else "install_chain"
        )
        return ResolveOk(
            backend_id=dep.id,
            variant_id=str(variant.get("id", "")),
            action=action,
        )

    return ResolveErr(
        reason=(
            f"no compatible backend for {variant.get('id')!r} on device "
            f"{device.device_id!r}"
        ),
        near_miss={
            "variant": closest_variant,
            "blocked_by": closest_blocked_by,
            "short_by_mb": max(0, closest_short_mb),
        },
        suggestions=_suggestions_for(closest_blocked_by),
    )


def resolve(
    manifest: dict,
    variant_id: str,
    device: DeviceCapability,
    *,
    force: bool = False,
) -> ResolveResult:
    """Pick a backend for (manifest, variant, device).

    Parameters
    ----------
    manifest:
        Loaded model manifest dict (typically from ``AppManifest.from_file``
        round-tripped via ``yaml.safe_load``).
    variant_id:
        The chosen variant's ``id``, or the literal ``"auto"`` to ask the
        resolver to pick the largest-fitting variant.
    device:
        Capacity snapshot of the target device.
    force:
        When ``True``, the target / RAM / VRAM gates are bypassed (used for
        the "Archive anyway" download flow). The disk gate always applies.
    """
    variants = manifest.get("variants") or []
    if not variants:
        return ResolveErr(
            reason="manifest has no variants",
            near_miss={"blocked_by": "schema"},
            suggestions=["Fix the manifest"],
        )

    if variant_id != "auto":
        for v in variants:
            if isinstance(v, dict) and v.get("id") == variant_id:
                return _check_variant(v, device, force=force)
        return ResolveErr(
            reason=f"variant {variant_id!r} not found in manifest",
            near_miss={"variant": variant_id, "blocked_by": "schema"},
            suggestions=[f"Available variants: {[v.get('id') for v in variants]}"],
        )

    # auto: walk variants by size_mb descending, return first Ok.
    sorted_variants = sorted(
        (v for v in variants if isinstance(v, dict)),
        key=lambda v: int(v.get("size_mb", 0) or 0),
        reverse=True,
    )
    last_err: ResolveErr | None = None
    for v in sorted_variants:
        result = _check_variant(v, device, force=force)
        if isinstance(result, ResolveOk):
            return result
        last_err = result

    if last_err is not None:
        return last_err
    return ResolveErr(
        reason="no variants in manifest could be evaluated",
        near_miss={"blocked_by": "schema"},
        suggestions=["Fix the manifest"],
    )


def classify(manifest: dict, device: DeviceCapability) -> Literal["green", "amber", "red"]:
    """Classify a model's compatibility with a device.

    Returns one of:

    - ``"green"`` — at least one variant resolves on a non-``cpu`` target
      (accelerated path available).
    - ``"amber"`` — at least one variant resolves but only on ``cpu``.
    - ``"red"`` — no variant resolves under non-force gates.
    """
    variants = manifest.get("variants") or []
    accelerated = False
    cpu_only = False
    for v in variants:
        if not isinstance(v, dict):
            continue
        result = _check_variant(v, device, force=False)
        if isinstance(result, ResolveOk):
            # Find the dep that won so we know its targets.
            deps = _coerce_backends(
                ((v.get("requires") or {}).get("backends")) or []
            )
            for dep in deps:
                if dep.id != result.backend_id:
                    continue
                if any(t != "cpu" for t in dep.targets if t in device.targets):
                    accelerated = True
                else:
                    cpu_only = True
                break

    if accelerated:
        return "green"
    if cpu_only:
        return "amber"
    return "red"

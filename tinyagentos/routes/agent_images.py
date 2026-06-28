"""Base-image management API for the Agents app.

Prebuilt agent base images (incus images aliased ``taos-<framework>-base``
and the generic ``taos-base``) let fresh deploys skip the ~60-90s cold apt
run. Today they can only be inspected or pruned with the incus CLI on the
host, which violates the no-terminal-for-end-users principle. This router
exposes the same operations over HTTP so the Agents app can manage them:

- ``GET  /api/agent-images``                 list present taos base images
- ``POST /api/agent-images/{alias}/import``  import a known base locally
- ``DELETE /api/agent-images/{alias}``       prune a base to reclaim disk
- ``POST /api/agent-images/prefetch``        toggle the prefetch preference

Every incus call degrades gracefully: a missing/unstarted incus or an
absent image returns an empty list or a clear error, never a 500 crash.
This router does NOT trigger CI image rebuilds (that is the
build-agent-images.yml workflow); ``import`` re-imports the already
published tarball via :func:`ensure_image_present`.
"""
from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from tinyagentos.agent_image import (
    FRAMEWORKS_WITH_DEDICATED_BASE,
    GENERIC_BASE_ALIAS,
    all_base_image_aliases,
    base_image_alias,
    ensure_image_present,
    is_prefetch_enabled,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# Pref file that persists the prefetch toggle when the env var is unset.
# Lives beside the other data_dir state (config.yaml, installed.json, ...).
_PREFETCH_PREF_FILE = "agent_image_prefs.json"


def _known_base_aliases() -> set[str]:
    """Set of valid taos base aliases (dedicated frameworks + generic).

    The single source of truth for what this router may import or prune, so
    a prune can never touch an unrelated incus image.
    """
    return set(all_base_image_aliases())


def _framework_for_alias(alias: str) -> str | None:
    """Return the framework an alias backs, or None for the generic base."""
    if alias == GENERIC_BASE_ALIAS:
        return None
    for framework in sorted(FRAMEWORKS_WITH_DEDICATED_BASE):
        if base_image_alias(framework) == alias:
            return framework
    return None


async def _incus_image_rows() -> list[dict] | None:
    """Return parsed ``incus image list`` rows, or None if incus is unavailable.

    Uses ``incus image list --format=csv -c lasu`` (alias, architecture,
    size, upload date). Returns None on any failure (incus not installed,
    daemon down, non-zero exit) so the caller can degrade to an empty list.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "incus", "image", "list",
            "--format=csv", "-c", "lasu",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
    except (FileNotFoundError, asyncio.TimeoutError):
        return None
    except Exception:  # pragma: no cover - defensive
        return None
    if proc.returncode != 0:
        return None

    rows: list[dict] = []
    import csv as _csv

    text = (stdout or b"").decode()
    for fields in _csv.reader(text.splitlines()):
        if len(fields) < 4 or not fields[0].strip():
            continue
        rows.append({
            "alias": fields[0].strip(),
            "architecture": fields[1].strip(),
            "size": fields[2].strip(),
            "uploaded_at": fields[3].strip(),
        })
    return rows


def _parse_size_bytes(size: str) -> int:
    """Parse an incus size string (e.g. ``412.50MiB``) into bytes.

    Best-effort: returns 0 for an unrecognised value so the aggregate total
    never crashes on an unexpected format.
    """
    units = {
        "B": 1,
        "KIB": 1024,
        "MIB": 1024 ** 2,
        "GIB": 1024 ** 3,
        "TIB": 1024 ** 4,
        "KB": 1000,
        "MB": 1000 ** 2,
        "GB": 1000 ** 3,
        "TB": 1000 ** 4,
    }
    s = size.strip().upper()
    # Longest suffix first so "MIB" matches before the "B" substring.
    for suffix in sorted(units, key=len, reverse=True):
        factor = units[suffix]
        if s.endswith(suffix):
            num = s[: -len(suffix)].strip()
            try:
                return int(float(num) * factor)
            except ValueError:
                return 0
    try:
        return int(float(s))
    except ValueError:
        return 0


async def _incus_image_delete(alias: str) -> tuple[int, str]:
    """Run ``incus image delete <alias>``; return (returncode, output).

    Returns (127, msg) when incus is not installed so the route can map it
    to a clear error instead of crashing.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "incus", "image", "delete", alias,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
    except FileNotFoundError:
        return 127, "incus is not installed on this host"
    except asyncio.TimeoutError:
        return 124, "incus image delete timed out"
    return proc.returncode, (stdout or b"").decode()


@router.get("/api/agent-images")
async def list_agent_images(request: Request):
    """List the taos base images present on the host.

    Only ``taos-*-base`` aliases are reported, never unrelated incus images.
    Each entry carries its alias, architecture, size, upload date, the
    framework it backs (None for the generic base); an aggregate disk-usage
    total is returned alongside the prefetch-enabled flag.

    Degrades gracefully: when incus is unavailable the image list is empty
    (``incus_available: false``) rather than a 500.
    """
    known = _known_base_aliases()
    rows = await _incus_image_rows()
    if rows is None:
        return {
            "images": [],
            "total_size_bytes": 0,
            "prefetch_enabled": is_prefetch_enabled(),
            "incus_available": False,
        }

    images: list[dict] = []
    total = 0
    for row in rows:
        alias = row["alias"]
        if alias not in known:
            continue
        size_bytes = _parse_size_bytes(row["size"])
        total += size_bytes
        images.append({
            "alias": alias,
            "architecture": row["architecture"],
            "size": row["size"],
            "size_bytes": size_bytes,
            "uploaded_at": row["uploaded_at"],
            "framework": _framework_for_alias(alias),
        })

    return {
        "images": images,
        "total_size_bytes": total,
        "prefetch_enabled": is_prefetch_enabled(),
        "incus_available": True,
    }


@router.post("/api/agent-images/{alias}/import")
async def import_agent_image(alias: str):
    """Ensure a known taos base image is present locally.

    Re-imports the already-published tarball via
    :func:`ensure_image_present` (no CI rebuild is triggered). 404 if the
    alias is not a known taos base. Returns whether the image is now present.
    """
    if alias not in _known_base_aliases():
        raise HTTPException(status_code=404, detail=f"unknown base image alias: {alias}")

    ok = await ensure_image_present(alias)
    return {"alias": alias, "present": ok}


@router.delete("/api/agent-images/{alias}")
async def delete_agent_image(alias: str):
    """Prune (hard-delete) a taos base image to reclaim disk.

    GUARD: only aliases in the taos base set (``taos-<framework>-base`` /
    ``taos-base``) are permitted; anything else is refused with 400 so an
    unrelated incus image can never be deleted through this route.

    This is a real hard-delete and is SAFE for base images: they are
    reproducible (re-importable via the import endpoint) and a container's
    rootfs is copied at launch, so pruning never affects a running agent --
    only future cold deploys, which fall back to the slower uncached path.
    """
    if alias not in _known_base_aliases():
        raise HTTPException(
            status_code=400,
            detail=f"refusing to delete non-taos-base alias: {alias}",
        )

    code, output = await _incus_image_delete(alias)
    if code != 0:
        raise HTTPException(
            status_code=502,
            detail=f"incus image delete failed: {output.strip()[:300]}",
        )
    return {"alias": alias, "deleted": True}


class PrefetchToggle(BaseModel):
    enabled: bool


@router.post("/api/agent-images/prefetch")
async def set_prefetch(body: PrefetchToggle, request: Request):
    """Persist the base-image prefetch preference.

    ``is_prefetch_enabled`` reads the ``TAOS_PREFETCH_BASE_IMAGE`` env var,
    which a UI cannot set. We persist the toggle to a small pref file in
    data_dir so it survives, and the response reflects the stored value.

    Note: the boot-time prefetch is gated on the env var, so a change here
    only governs UI state until the env var is also set; the effective
    enabled flag (env OR pref) is returned so callers see the live value.
    """
    data_dir = request.app.state.config_path.parent
    pref_path = data_dir / _PREFETCH_PREF_FILE
    try:
        pref_path.write_text(json.dumps({"prefetch_enabled": body.enabled}))
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"could not persist preference: {exc}")

    # Effective value: the env var still wins if it is set on.
    effective = is_prefetch_enabled() or body.enabled
    return {"prefetch_enabled": effective, "stored": body.enabled}

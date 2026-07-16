"""Node-local backend service manager.

Phase 1, unit 2 of the cluster-aware backend service management design
(docs/design/cluster-backend-service-management.md). Reusable, node-agnostic
helpers that:

  * read the managed-backend contract from catalog manifests
    (``lifecycle.auto_manage`` + ``unit`` / ``scope`` / ``health``),
  * resolve a systemd unit's scope (user vs system),
  * report a unit's state (installed / enabled / active) and health, and
  * run a fail-soft service action (start / stop / restart).

The systemctl logic is promoted from ``routes/system.py::_restart_ai_unit`` and
generalised to any verb so the #1743 recovery endpoint and the worker-agent
backend endpoints can share one implementation. This module is additive: it
does not change any existing behaviour on its own.
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from pathlib import Path

import httpx
import yaml

logger = logging.getLogger(__name__)

VALID_SCOPES = {"system", "user"}


@dataclass(frozen=True)
class ManagedBackend:
    """A backend the node manages as a systemd service (from its manifest)."""

    id: str
    unit: str
    scope: str          # "system" | "user"
    health_url: str
    health_expect: str


def load_managed_backends(catalog_root: Path) -> list[ManagedBackend]:
    """Return the managed backends declared under ``catalog_root/services``.

    Only manifests with ``lifecycle.auto_manage: true`` and a valid
    ``unit`` + ``scope`` are returned; the manifest lint
    (scripts/check_manifests.py) guarantees those fields exist for any
    auto-managed service, so a manifest missing them is skipped here rather
    than raised.
    """
    out: list[ManagedBackend] = []
    services_dir = catalog_root / "services"
    for manifest in sorted(services_dir.glob("*/manifest.yaml")):
        try:
            data = yaml.safe_load(manifest.read_text())
        except yaml.YAMLError:
            logger.warning("skipping unparseable service manifest %s", manifest)
            continue
        if not isinstance(data, dict):
            logger.warning("skipping non-mapping service manifest %s", manifest)
            continue
        lifecycle = data.get("lifecycle")
        if not isinstance(lifecycle, dict) or lifecycle.get("auto_manage") is not True:
            continue
        unit = lifecycle.get("unit")
        scope = lifecycle.get("scope")
        if not unit or scope not in VALID_SCOPES:
            # The CI lint guarantees these fields for auto-managed services;
            # a device-side catalog edit that breaks the contract would
            # silently drop the backend out of recovery, so say so.
            logger.warning(
                "skipping managed backend %s: lifecycle.unit/scope invalid "
                "(unit=%r scope=%r)", manifest, unit, scope,
            )
            continue
        health = lifecycle.get("health") if isinstance(lifecycle.get("health"), dict) else {}
        out.append(
            ManagedBackend(
                id=str(data.get("id") or manifest.parent.name),
                unit=str(unit),
                scope=str(scope),
                health_url=str(health.get("url", "")),
                health_expect=str(health.get("expect", "")),
            )
        )
    return out


def _systemd_present() -> bool:
    return bool(os.environ.get("INVOCATION_ID") or os.path.exists("/run/systemd/system"))


async def _rc(args: list[str]) -> int:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.wait()
    return proc.returncode


async def resolve_scope(unit: str, prefer: str | None = None) -> str | None:
    """Return the scope ("user"/"system") a unit file exists in, else None.

    Uses ``systemctl [--user] cat`` (returns 0 iff the unit file exists) so a
    dead/crashed unit is still resolved, unlike an is-active probe. ``prefer``
    (from the manifest ``lifecycle.scope``) is tried first.
    """
    order: list[str] = []
    if prefer in VALID_SCOPES:
        order.append(prefer)  # type: ignore[arg-type]
    for s in ("user", "system"):
        if s not in order:
            order.append(s)
    for scope in order:
        flag = ["--user"] if scope == "user" else []
        if await _rc(["systemctl", *flag, "cat", unit]) == 0:
            return scope
    return None


async def unit_state(unit: str, prefer: str | None = None) -> dict:
    """Report {installed, scope, enabled, active} for a unit, fail-soft."""
    if not _systemd_present():
        return {"installed": False, "enabled": False, "active": False,
                "detail": "systemd not available on host"}
    try:
        scope = await resolve_scope(unit, prefer)
    except Exception as exc:  # pragma: no cover - defensive
        return {"installed": False, "enabled": False, "active": False,
                "detail": f"systemctl unavailable: {str(exc)[:160]}"}
    if scope is None:
        return {"installed": False, "enabled": False, "active": False}
    flag = ["--user"] if scope == "user" else []
    enabled = await _rc(["systemctl", *flag, "is-enabled", unit]) == 0
    active = await _rc(["systemctl", *flag, "is-active", unit]) == 0
    return {"installed": True, "scope": scope, "enabled": enabled, "active": active}


async def service_action(unit: str, verb: str, prefer: str | None = None,
                         timeout: float = 45.0) -> dict:
    """Run ``systemctl <verb> <unit>`` in the resolved scope, fail-soft.

    Returns a per-unit result dict (never raises): a unit that is not installed,
    or that the service user lacks rights to touch (polkit / interactive auth),
    is reported rather than raised. On timeout the child is killed and reaped so
    it is not orphaned.
    """
    if verb not in ("start", "stop", "restart"):
        raise ValueError(f"unsupported verb: {verb}")
    if not _systemd_present():
        return {"unit": unit, "ok": False, "detail": "systemd not available on host"}
    try:
        scope = await resolve_scope(unit, prefer)
    except Exception as exc:  # pragma: no cover - defensive
        return {"unit": unit, "ok": False, "detail": f"systemctl unavailable: {str(exc)[:160]}"}
    if scope is None:
        return {"unit": unit, "ok": False, "detail": "not installed"}

    flag = ["--user"] if scope == "user" else []
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(
            "systemctl", *flag, verb, unit,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        if proc is not None:
            try:
                proc.kill()
                await proc.wait()
            except Exception:  # pragma: no cover - defensive
                pass
        return {"unit": unit, "ok": False, "scope": scope, "detail": f"{verb} timed out"}
    except Exception as exc:  # pragma: no cover - defensive
        return {"unit": unit, "ok": False, "scope": scope, "detail": str(exc)[:200]}

    if proc.returncode == 0:
        return {"unit": unit, "ok": True, "scope": scope}
    detail = (stderr.decode(errors="replace").strip() or f"exit code {proc.returncode}")[:200]
    return {"unit": unit, "ok": False, "scope": scope, "detail": detail}


async def health_probe(url: str, expect: str, timeout: float = 5.0) -> dict:
    """Probe a backend health endpoint. Returns {ok, detail}.

    ``expect`` is a literal substring that must appear in a 200 response body.
    """
    if not url:
        return {"ok": False, "detail": "no health url"}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url)
    except Exception as exc:
        return {"ok": False, "detail": str(exc)[:160]}
    if resp.status_code != 200:
        return {"ok": False, "detail": f"HTTP {resp.status_code}"}
    if expect and expect not in resp.text:
        return {"ok": False, "detail": "health body missing expected marker"}
    return {"ok": True}

"""Rebuild the desktop frontend bundle if source has moved ahead of the bundle.

Used by both the in-app Install Update handler and the background auto-update
service.  Mirrors the intent of ExecStartPre in the systemd unit and
bin/update.sh — so all update paths converge on the same conditional rebuild
regardless of platform (systemd/Pi, Docker, Mac .app, dev host).
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RebuildResult:
    """Outcome of a rebuild attempt.

    ``rebuilt`` indicates whether a rebuild was attempted at all (False when
    the staleness check skipped it or npm wasn't available).  ``success``
    indicates whether the result is healthy (False on npm failure or
    timeout).  ``message`` is human-readable for log/error surfaces.

    Callers can branch on ``success`` directly rather than string-matching
    the message — see issue #327.
    """
    rebuilt: bool
    success: bool
    message: str


def _is_bundle_stale(project_root: Path) -> bool:
    """Return True if any file under desktop/src is newer than the built bundle."""
    desktop_dir = project_root / "desktop"
    if not desktop_dir.is_dir():
        return False  # nothing to build
    index_html = project_root / "static" / "desktop" / "index.html"
    if not index_html.is_file():
        return True  # never built
    bundle_mtime = index_html.stat().st_mtime
    src_dir = desktop_dir / "src"
    if not src_dir.is_dir():
        return False
    for path in src_dir.rglob("*"):
        if path.is_file() and path.stat().st_mtime > bundle_mtime:
            return True
    return False


async def rebuild_desktop_bundle_if_stale(
    project_root: Path,
    *,
    timeout_seconds: int = 600,
    force: bool = False,
) -> RebuildResult:
    """Run npm install + npm run build if the bundle is stale (or always, if force=True).

    Returns a :class:`RebuildResult` with ``rebuilt`` (was a build attempted?),
    ``success`` (did it succeed?), and ``message`` (human-readable detail).

    On hosts where npm/node aren't installed the rebuild reports
    ``rebuilt=False, success=True`` (the skip is a successful no-op for the
    caller — it's not the rebuild's job to install npm).

    Use ``force=True`` for explicit user-initiated rebuilds (e.g. the
    ``/api/settings/rebuild-frontend`` endpoint or applied updates) where the
    staleness heuristic isn't trustworthy — committed bundles can lie about
    their freshness when a PR landed source-only.
    """
    if not force and not _is_bundle_stale(project_root):
        return RebuildResult(
            rebuilt=False,
            success=True,
            message="Desktop bundle is current — skipping rebuild.",
        )

    desktop_dir = project_root / "desktop"
    if not (desktop_dir / "package.json").is_file():
        return RebuildResult(
            rebuilt=False,
            success=True,
            message="No desktop/package.json found — skipping rebuild.",
        )

    logger.info("Desktop source is ahead of bundle — rebuilding...")

    try:
        proc = await asyncio.create_subprocess_exec(
            "npm", "install", "--silent",
            cwd=str(desktop_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)
        if proc.returncode != 0:
            msg = f"npm install failed (rc={proc.returncode}): {stderr.decode(errors='replace')[-500:]}"
            logger.error(msg)
            return RebuildResult(rebuilt=True, success=False, message=msg)

        proc = await asyncio.create_subprocess_exec(
            "npm", "run", "build",
            cwd=str(desktop_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)
        if proc.returncode != 0:
            msg = f"npm run build failed (rc={proc.returncode}): {stderr.decode(errors='replace')[-500:]}"
            logger.error(msg)
            return RebuildResult(rebuilt=True, success=False, message=msg)

        logger.info("Desktop bundle rebuilt successfully.")
        return RebuildResult(rebuilt=True, success=True, message="Desktop bundle rebuilt successfully.")

    except asyncio.TimeoutError:
        try:
            proc.terminate()
        except Exception:
            pass
        msg = f"Desktop rebuild timed out after {timeout_seconds}s."
        logger.error(msg)
        return RebuildResult(rebuilt=True, success=False, message=msg)

    except FileNotFoundError as exc:
        # npm not on PATH — e.g. minimal Docker image, dev box without Node.
        # This is a benign skip from the caller's perspective.
        msg = f"npm not available — skipping desktop rebuild: {exc}"
        logger.warning(msg)
        return RebuildResult(rebuilt=False, success=True, message=msg)

    except Exception as exc:
        msg = f"Desktop rebuild error: {exc!r}"
        logger.error(msg)
        return RebuildResult(rebuilt=True, success=False, message=msg)

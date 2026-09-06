"""Pre-built agent base image management.

Fresh agent deploys used to spend ~60-90s per container doing work that
is identical for every openclaw agent on a given arch: apt install Node
/ curl, download openclaw tarball, npm install, systemd unit scaffolding.

Under this module a prebuilt LXC image (`taos-openclaw-base`) published
by ``.github/workflows/build-agent-images.yml`` is imported once per
host. The deployer then launches containers from that alias instead of
``images:debian/bookworm`` and install.sh skips the heavy steps.

The helpers here are deliberately small wrappers around ``incus`` so
the deployer can ask "is the base image already imported?" without
pulling in the full container backend abstraction.
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import logging
import os
import platform
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

# Progress state for the current (or most recent) prefetch operation.
# Read by the /api/agent-image/status endpoint so the frontend can
# show download progress. Keys: status (idle|downloading|importing|done|failed),
# started_at (ISO timestamp), url (str).
_prefetch_state: dict = {"status": "idle"}

_FRAMEWORK_UPDATE_SCRIPT_SRC = Path(__file__).parent / "scripts" / "taos-framework-update.sh"

# Tag + asset naming must match the workflow at
# .github/workflows/build-agent-images.yml. Both must move together.
BASE_IMAGE_ALIAS = "taos-openclaw-base"
RELEASE_BASE_URL = (
    "https://github.com/jaylfc/tinyagentos-images/releases/download/rolling-images"
)

# Generic base alias usable by any framework. It bakes the common deps
# (python3/node/npm/build-essential/git/curl/etc) without any single
# framework's package, so a deploy from it still skips the cold apt run.
GENERIC_BASE_ALIAS = "taos-base"

# Frameworks that have a dedicated prebuilt base image (alias
# "taos-<framework>-base"). openclaw keeps its historical alias for
# back-compat. Hermes' base additionally bakes the NousResearch
# hermes-agent prerequisites so a hermes deploy needs only config.
FRAMEWORKS_WITH_DEDICATED_BASE = frozenset({"openclaw", "hermes"})


def base_image_alias(framework: str) -> str:
    """Return the prebuilt base-image alias for ``framework``.

    Frameworks listed in :data:`FRAMEWORKS_WITH_DEDICATED_BASE` get a
    dedicated ``taos-<framework>-base`` alias (openclaw keeps its
    historical ``taos-openclaw-base``). Everything else maps to the
    generic ``taos-base``. The deployer tries the dedicated alias first
    and falls back to the generic one, then to a cold image.
    """
    if framework in FRAMEWORKS_WITH_DEDICATED_BASE:
        return f"taos-{framework}-base"
    return GENERIC_BASE_ALIAS


def is_prefetch_enabled() -> bool:
    """Return True if the user has opted in to base image prefetching.

    Controlled by the environment variable ``TAOS_PREFETCH_BASE_IMAGE``.
    Set to ``1``, ``true``, or ``yes`` (case-insensitive) to enable.
    Default is off — no image download unless explicitly opted in.
    """
    val = os.environ.get("TAOS_PREFETCH_BASE_IMAGE", "").strip().lower()
    return val in ("1", "true", "yes")


def get_prefetch_state() -> dict:
    """Return a copy of the current prefetch progress state."""
    return dict(_prefetch_state)


def register_prefetch_endpoint(app):
    """Register /api/agent-image/status on the FastAPI app.

    Called from app startup. Returns current prefetch state:
    {status: idle|downloading|importing|done|failed, started_at: ISO, url: str}
    """
    from fastapi import APIRouter

    router = APIRouter()

    @router.get("/api/agent-image/status")
    async def prefetch_status():
        return dict(_prefetch_state)

    app.include_router(router)


def arch_suffix() -> str:
    """Return the tarball arch suffix matching the workflow matrix.

    Aligns with openclaw's fork CI naming so there is a single arch
    vocabulary across tooling: ``arm64`` or ``x64``.
    """
    machine = platform.machine().lower()
    if machine in ("aarch64", "arm64"):
        return "arm64"
    if machine in ("x86_64", "amd64"):
        return "x64"
    return machine or "unknown"


def base_image_url(arch: str | None = None, framework: str | None = None) -> str:
    """URL of the published image tarball for ``arch`` (defaults to host arch).

    ``framework`` selects which base alias the URL points at. When omitted
    the historical ``taos-openclaw-base`` alias is used so existing callers
    (which only pass ``arch``) keep their behaviour. Pass a framework name to
    get that framework's dedicated base, or the generic ``taos-base`` fallback.
    """
    alias = base_image_alias(framework) if framework is not None else BASE_IMAGE_ALIAS
    return base_image_url_for_alias(alias, arch)


def base_image_url_for_alias(alias: str, arch: str | None = None) -> str:
    """URL of the published tarball for a base-image ``alias`` directly.

    Aliases map 1:1 onto asset names in the images release, so this is the
    single source of truth the alias-keyed prefetch path uses to avoid
    importing the wrong tarball under an alias (e.g. the openclaw tarball
    under ``taos-hermes-base``).
    """
    return f"{RELEASE_BASE_URL}/{alias}-linux-{arch or arch_suffix()}.tar.gz"


def all_base_image_aliases() -> list[str]:
    """Aliases that prefetch should import: every dedicated-base framework's
    alias plus the generic ``taos-base``.

    Ordered, de-duplicated, deterministic. The deployer's fast-path tries a
    framework's dedicated base then the generic one, so both must be present
    on a fresh host for the fast-path to trigger for any framework.
    """
    aliases: list[str] = []
    for framework in sorted(FRAMEWORKS_WITH_DEDICATED_BASE):
        alias = base_image_alias(framework)
        if alias not in aliases:
            aliases.append(alias)
    if GENERIC_BASE_ALIAS not in aliases:
        aliases.append(GENERIC_BASE_ALIAS)
    return aliases


async def is_image_present(alias: str = BASE_IMAGE_ALIAS, remote: str | None = None) -> bool:
    """Return True iff incus already has an image with this alias.

    Uses ``incus image list --format=csv -c f <alias>`` and checks the output
    has any non-empty row. ``remote`` (e.g. ``"fedora-worker"``) checks an
    enrolled remote's nested incus instead of the local one. Any failure
    (incus not installed, daemon down) returns False -- the caller will fall
    back to the uncached deploy path.
    """
    args = ["incus", "image", "list", "--format=csv", "-c", "f"]
    if remote:
        args.append(f"{remote}:")
    args.append(alias)
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
    except (FileNotFoundError, asyncio.TimeoutError):
        return False
    except Exception:  # pragma: no cover - defensive
        return False
    if proc.returncode != 0:
        return False
    for line in (stdout or b"").decode().splitlines():
        if line.strip():
            return True
    return False



async def _bake_scripts_into_image(alias: str) -> None:
    """Launch a temporary container from *alias*, inject taos-framework-update,
    publish the result back over the same alias, then delete the temp container.

    Non-fatal — any failure is logged and the image is left as-is so deploys
    still work; agents will just be missing the helper until the next
    successful bake (or the next image import cycle).
    """
    from tinyagentos.containers import exec_in_container, push_file

    tmp_name = f"taos-bake-{alias}-tmp"
    script_dest = "/usr/local/bin/taos-framework-update"

    async def _incus(*args: str, timeout: int = 60) -> tuple[int, str]:
        proc = await asyncio.create_subprocess_exec(
            "incus", *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode, (stdout or b"").decode()

    try:
        # Launch a temporary container from the just-imported image
        code, out = await _incus("launch", alias, tmp_name, timeout=120)
        if code != 0:
            logger.warning("agent_image: bake launch failed for %s: %s", alias, out[:300])
            return
        # Give the container a moment to reach running state
        await asyncio.sleep(2)
        # Push the update script into the image
        await push_file(tmp_name, str(_FRAMEWORK_UPDATE_SCRIPT_SRC), script_dest)
        await exec_in_container(tmp_name, ["chmod", "+x", script_dest])
        # Stop the container, then publish it back over the same alias
        code, out = await _incus("stop", tmp_name, "--force", timeout=60)
        if code != 0:
            logger.warning("agent_image: bake stop failed for %s: %s", tmp_name, out[:200])
            return
        # Delete the old image, then publish the modified container as the alias
        await _incus("image", "delete", alias, timeout=30)
        code, out = await _incus("publish", tmp_name, "--alias", alias, timeout=120)
        if code != 0:
            logger.warning("agent_image: bake publish failed for %s: %s", alias, out[:300])
            return
        logger.info("agent_image: taos-framework-update baked into %s", alias)
    except Exception as exc:
        logger.warning("agent_image: bake scripts failed for %s: %s", alias, exc)
    finally:
        # Always clean up the temp container regardless of outcome
        try:
            await _incus("delete", tmp_name, "--force", timeout=30)
        except Exception:
            pass

async def ensure_image_present(
    alias: str = BASE_IMAGE_ALIAS,
    url: str | None = None,
    remote: str | None = None,
) -> bool:
    """Import the base image from ``url`` if not already present.

    Non-fatal: returns True on success or already-present, False on any
    failure. The deployer retains a fallback path that launches from
    ``images:debian/bookworm`` so a missing cache image never blocks deploys.

    This is intended as a one-time bootstrap called from app startup.
    The image is ~300-500 MB so expect this to take a minute on first
    run; subsequent taOS boots are no-ops.

    Downloads to a temp file then calls ``incus image import <path>``.
    Incus 6.x rejects both ``-`` (stdin) and bare HTTPS URLs (expects
    an incus image server, not a plain tarball), so staging through
    disk is the only reliable path. The temp file lives in the system
    temp dir so it lands on the same filesystem as /var/tmp and can be
    imported without extra copying.
    """
    if await is_image_present(alias, remote=remote):
        return True
    # Derive the URL from the alias so a non-openclaw alias never imports the
    # openclaw tarball (an explicit url still wins for tests / overrides).
    import_url = url or base_image_url_for_alias(alias)
    _prefetch_state.update(
        status="downloading",
        started_at=_dt.datetime.now(_dt.timezone.utc).isoformat(),
        url=import_url,
    )
    logger.info(
        "agent_image: importing base image %s from %s (one-time bootstrap, ~300-500MB)",
        alias, import_url,
    )
    tmp_dir = os.environ.get("TAOS_TMPDIR") or None
    if tmp_dir:
        os.makedirs(tmp_dir, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(prefix="taos-image-", suffix=".tar.gz", dir=tmp_dir)
    os.close(tmp_fd)
    try:
        try:
            curl = await asyncio.create_subprocess_exec(
                "curl", "-fsSL", "--max-time", "600", "-o", tmp_path, import_url,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            curl_out, _ = await asyncio.wait_for(curl.communicate(), timeout=900)
        except (FileNotFoundError, asyncio.TimeoutError) as exc:
            logger.warning("agent_image: download failed for %s: %s", alias, exc)
            _prefetch_state["status"] = "failed"
            return False
        if curl.returncode != 0:
            logger.warning(
                "agent_image: curl for %s exited %s: %s (is the image published yet?)",
                alias, curl.returncode, (curl_out or b"").decode()[:300],
            )
            _prefetch_state["status"] = "failed"
            return False
        _prefetch_state["status"] = "importing"
        import_args = ["incus", "image", "import", tmp_path]
        if remote:
            import_args.append(f"{remote}:")
        import_args += ["--alias", alias]
        try:
            incus = await asyncio.create_subprocess_exec(
                *import_args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            incus_out, _ = await asyncio.wait_for(incus.communicate(), timeout=300)
        except (FileNotFoundError, asyncio.TimeoutError) as exc:
            logger.warning("agent_image: import failed for %s: %s", alias, exc)
            _prefetch_state["status"] = "failed"
            return False
        if incus.returncode != 0:
            logger.warning(
                "agent_image: incus image import of %s returned %s: %s",
                alias, incus.returncode, (incus_out or b"").decode()[:500],
            )
            _prefetch_state["status"] = "failed"
            return False
        logger.info("agent_image: %s imported OK%s", alias, f" on {remote}" if remote else "")
        # The bake launches a temp container on the LOCAL incus to inject the
        # taos-framework-update helper, so it only applies to local imports.
        # On a remote the prefetched base is used as-is (the helper is
        # non-essential and can be baked on the worker separately if needed).
        if not remote:
            await _bake_scripts_into_image(alias)
        _prefetch_state["status"] = "done"
        return True
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("agent_image: import failed for %s: %s", alias, exc)
        _prefetch_state["status"] = "failed"
        return False
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


async def ensure_all_base_images_present() -> dict[str, bool]:
    """Import every base image the deployer fast-path can use.

    Imports each alias from :func:`all_base_image_aliases` (the dedicated-base
    frameworks plus the generic ``taos-base``) so the fast-path can trigger
    for any framework on a fresh host -- not just openclaw. Each import is
    independent and non-fatal: a failure on one alias is logged and the rest
    still run. Returns an ``{alias: ok}`` map.

    Intended as the one-time boot bootstrap (called when prefetch is enabled).
    """
    results: dict[str, bool] = {}
    for alias in all_base_image_aliases():
        try:
            results[alias] = await ensure_image_present(alias)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("agent_image: ensure %s failed: %s", alias, exc)
            results[alias] = False
    return results


__all__ = [
    "BASE_IMAGE_ALIAS",
    "GENERIC_BASE_ALIAS",
    "FRAMEWORKS_WITH_DEDICATED_BASE",
    "base_image_alias",
    "RELEASE_BASE_URL",
    "is_prefetch_enabled",
    "get_prefetch_state",
    "register_prefetch_endpoint",
    "arch_suffix",
    "base_image_url",
    "base_image_url_for_alias",
    "all_base_image_aliases",
    "is_image_present",
    "ensure_image_present",
    "ensure_all_base_images_present",
]

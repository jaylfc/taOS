"""macOS launchd plist migration for Settings updates.

This module provides a pure function to migrate old bare-uvicorn plists
(from pre-#3313 installs) to use the package entrypoint `python -m tinyagentos`.

The migration is necessary because only the package entrypoint starts the
LLM gateway's agent listener and sets app.state.llm_gateway_agent_port.
A bare-uvicorn controller has no verified gateway port, so local agent
deploys are refused.
"""

from __future__ import annotations

import logging
import os
import plistlib
import shlex
import shutil
from pathlib import Path

from tinyagentos.atomic_io import atomic_write_bytes

logger = logging.getLogger(__name__)

PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / "com.tinyagentos.controller.plist"
HELPER_PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / "com.tinyagentos.plist-reload.plist"


def _pending_launchd_reload_path() -> Path:
    return PLIST_PATH.parent / "com.tinyagentos.pending-launchd-reload"


def write_pending_launchd_reload() -> None:
    _pending_launchd_reload_path().write_text("1")


def read_pending_launchd_reload() -> bool:
    return _pending_launchd_reload_path().exists()


def clear_pending_launchd_reload() -> None:
    _pending_launchd_reload_path().unlink(missing_ok=True)


def _write_reload_helper(controller_plist: Path) -> None:
    uid = os.getuid()
    controller_plist_q = shlex.quote(str(controller_plist))
    helper_plist_q = shlex.quote(str(HELPER_PLIST_PATH))
    script = (
        f"sleep 1; "
        f"launchctl bootout gui/{uid} com.tinyagentos.controller; "
        f"for i in 1 2 3 4 5; do "
        f"  launchctl bootstrap gui/{uid} {controller_plist_q} && "
        f"  launchctl print gui/{uid}/com.tinyagentos.controller >/dev/null 2>&1 && break; "
        f"  sleep 2; "
        f"done; "
        f"if launchctl print gui/{uid}/com.tinyagentos.controller >/dev/null 2>&1; then "
        f"  rm {helper_plist_q}; "
        f"  launchctl bootout gui/{uid} com.tinyagentos.plist-reload; "
        f"fi"
    )
    helper_plist = {
        "Label": "com.tinyagentos.plist-reload",
        "RunAtLoad": True,
        "KeepAlive": False,
        "ProgramArguments": ["/bin/sh", "-c", script],
    }
    atomic_write_bytes(HELPER_PLIST_PATH, plistlib.dumps(helper_plist, fmt=plistlib.FMT_XML))


def _is_tinyagentos_controller_plist(plist: dict) -> bool:
    """Check if this is the tinyagentos controller plist."""
    return plist.get("Label") == "com.tinyagentos.controller"


def _is_old_uvicorn_format(program_args: list[str]) -> bool:
    """Detect if ProgramArguments uses bare uvicorn (old format)."""
    if not program_args:
        return False
    # Check for uvicorn in any argument
    for arg in program_args:
        if arg.endswith("uvicorn") or "tinyagentos.app:" in arg:
            return True
    return False


def _is_already_migrated(program_args: list[str]) -> bool:
    """Check if ProgramArguments already uses the module entrypoint."""
    return (
        len(program_args) >= 3
        and program_args[0].endswith(".venv/bin/python")
        and program_args[1] == "-m"
        and program_args[2] == "tinyagentos"
    )


def _extract_host_port_from_args(program_args: list[str]) -> tuple[str | None, str | None]:
    """Extract --host and --port values from uvicorn command line args."""
    host = None
    port = None

    i = 0
    while i < len(program_args):
        arg = program_args[i]
        if arg.startswith("--host="):
            host = arg.split("=", 1)[1]
        elif arg == "--host" and i + 1 < len(program_args):
            host = program_args[i + 1]
            i += 1
        elif arg.startswith("--port="):
            port = arg.split("=", 1)[1]
        elif arg == "--port" and i + 1 < len(program_args):
            port = program_args[i + 1]
            i += 1
        i += 1

    return host, port


def _is_flagless_uvicorn_plist(plist_bytes: bytes) -> bool:
    """Check for an un-migrated bare-uvicorn plist with no --host and/or --port.

    Such a plist bound uvicorn's own defaults (127.0.0.1:8000). There is no
    bind address to carry over, so migrate_launchd_plist leaves it alone rather
    than widening exposure, and the caller warns instead.
    """
    try:
        plist = plistlib.loads(plist_bytes)
    except Exception:
        return False
    if not _is_tinyagentos_controller_plist(plist):
        return False
    program_args = plist.get("ProgramArguments", [])
    if _is_already_migrated(program_args) or not _is_old_uvicorn_format(program_args):
        return False
    host, port = _extract_host_port_from_args(program_args)
    return host is None or port is None


def migrate_launchd_plist(plist_bytes: bytes, install_dir: str) -> bytes | None:
    """Migrate an old bare-uvicorn launchd plist to the new module entrypoint format.

    This is a pure function (bytes in, bytes out or None) for testability on Linux CI.

    Parameters
    ----------
    plist_bytes:
        Raw plist data as bytes (XML format).
    install_dir:
        The installation directory (used to construct the venv python path).

    Returns
    -------
    bytes | None
        New plist bytes if migration was needed, None if already migrated
        or not a tinyagentos controller plist.

    Notes
    -----
    - Uses plistlib, not regex, for robust XML parsing.
    - Preserves ALL other keys and environment variables unchanged.
    - Extracts --host/--port from old ProgramArguments and sets TAOS_HOST/TAOS_PORT.
      A plist missing either flag is NOT migrated: there is no bind address to
      carry over, and guessing would widen exposure.
    - Keeps a .bak copy and writes atomically (caller responsibility).
    """
    try:
        plist = plistlib.loads(plist_bytes)
    except Exception:
        # Invalid plist - log and return None (safety: don't corrupt unknown plists)
        logger.debug("launchd migration: invalid plist data, skipping")
        return None

    # Only migrate our controller plist
    if not _is_tinyagentos_controller_plist(plist):
        return None

    program_args = plist.get("ProgramArguments", [])

    # Already migrated?
    if _is_already_migrated(program_args):
        return None

    # Not an old uvicorn format?
    if not _is_old_uvicorn_format(program_args):
        return None

    # Extract host/port from old args. Both must be present: a plist without
    # them ran on uvicorn's own 127.0.0.1:8000 default, and defaulting to
    # 0.0.0.0:6969 would WIDEN exposure. Leave it alone; the user re-runs the
    # installer, which writes both flags.
    host, port = _extract_host_port_from_args(program_args)
    if host is None or port is None:
        logger.warning(
            "launchd migration: com.tinyagentos.controller.plist has no --host/--port "
            "in its ProgramArguments, leaving it unmigrated (re-run the installer to "
            "set the bind address)"
        )
        return None

    # Build new ProgramArguments
    venv_python = f"{install_dir}/.venv/bin/python"
    new_program_args = [venv_python, "-m", "tinyagentos"]

    # Update EnvironmentVariables with TAOS_HOST/TAOS_PORT
    env_vars = plist.get("EnvironmentVariables", {})
    new_env_vars = dict(env_vars)  # Preserve all existing env vars
    new_env_vars["TAOS_HOST"] = host
    new_env_vars["TAOS_PORT"] = str(port)

    # Build new plist preserving ALL other keys
    new_plist = dict(plist)
    new_plist["ProgramArguments"] = new_program_args
    new_plist["EnvironmentVariables"] = new_env_vars

    logger.info(
        "launchd migration: migrated com.tinyagentos.controller.plist from bare uvicorn "
        "to `python -m tinyagentos` (host=%s, port=%s)",
        host,
        port,
    )

    return plistlib.dumps(new_plist, fmt=plistlib.FMT_XML)


async def apply_launchd_migration(install_dir: str) -> tuple[bool, str | None]:
    """Apply the launchd plist migration on Darwin.

    This is the async wrapper that handles file I/O, atomic write, backup,
    and records that a reload is needed. Called from the Settings update path.

    The actual bootout/bootstrap is performed by a separate one-shot launchd
    helper job started from _do_restart after the HTTP response is returned.

    Parameters
    ----------
    install_dir:
        The installation directory.

    Returns
    -------
    tuple[bool, str | None]
        (success, warning). success=True means migration applied or not needed.
        warning is None on success, or a warning message on failure.
    """
    import sys

    if sys.platform != "darwin":
        return True, None

    plist_path = PLIST_PATH
    if not plist_path.exists():
        return True, None

    try:
        # Read current plist
        plist_bytes = plist_path.read_bytes()

        # Attempt migration
        new_plist_bytes = migrate_launchd_plist(plist_bytes, install_dir)

        if new_plist_bytes is None:
            # Already migrated, not applicable, or skipped for a missing
            # --host/--port. In that last case say so: the plist still runs the
            # old bare uvicorn, so the user has to re-run the installer.
            if _is_flagless_uvicorn_plist(plist_bytes):
                return True, (
                    f"launchd migration skipped: {plist_path} has no --host/--port, "
                    "so it was left unchanged. Re-run the installer to set the bind "
                    "address."
                )
            return True, None

        # Write atomically: use atomic_write_bytes instead of temp file + rename
        bak_path = plist_path.with_suffix(".plist.bak")

        atomic_write_bytes(bak_path, plist_bytes)

        atomic_write_bytes(plist_path, new_plist_bytes)

        # The new-format plist is only usable once the reload is scheduled. If
        # the prep below fails, put the original back from the .bak: a migrated
        # plist with no reload scheduled is seen as already migrated by every
        # later update and never retried.
        try:
            # Write the one-shot reload helper (C1: separate job, no bootout here)
            _write_reload_helper(plist_path)

            # Record that a reload is needed
            write_pending_launchd_reload()
        except Exception:
            try:
                atomic_write_bytes(plist_path, bak_path.read_bytes())
                logger.warning(
                    "launchd migration: reload prep failed, restored the original "
                    "plist from %s",
                    bak_path,
                )
            except Exception as restore_exc:  # pragma: no cover - defensive
                logger.warning(
                    "launchd migration: reload prep failed and the original plist "
                    "could not be restored from %s: %s",
                    bak_path,
                    restore_exc,
                )
            raise

        logger.info(
            "launchd migration: migrated com.tinyagentos.controller.plist from bare uvicorn "
            "to `python -m tinyagentos`"
        )

        return True, None

    except Exception as e:
        logger.warning("launchd migration failed: %s", e)
        return True, f"launchd migration failed: {e}"


def _maybe_migrate_launchd_plist(plist_bytes: bytes, install_dir: str) -> bytes | None:
    """Platform-gated migration function for testability.

    On Darwin, calls migrate_launchd_plist. On other platforms, returns None.
    This allows the update path to call it unconditionally while the Darwin
    gate is tested via monkeypatch.
    """
    import sys

    if sys.platform != "darwin":
        return None
    return migrate_launchd_plist(plist_bytes, install_dir)
"""Controller side of the root maintenance runner (taos-maint).

The controller runs as an unprivileged account and cannot change the host. A
root-owned runner (scripts/taos-maint, installed by scripts/install-maint.sh)
applies fixed maintenance steps that the project signed. This module can only
ASK the runner to look again: it drops an empty ``*.req`` file in the trigger
directory (systemd's taos-maint.path starts the runner) and reads the root-owned
status the runner writes. It passes no step name, path or argument.

Where there is no runner (older install, macOS, Docker, a user-level service)
``availability()`` says why, so the caller can show the right next step instead
of failing silently.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import secrets
import time
from pathlib import Path

logger = logging.getLogger(__name__)

HELPER = Path("/usr/local/sbin/taos-maint")
STATE_DIR = Path("/var/lib/taos-maint")
CONF = Path("/etc/taos/maint/maint.conf")
SETUP_URL_REF = "master"  # release tag the one-time setup command is pinned to
WAIT_SECONDS = 900.0
POLL_SECONDS = 1.0


def _in_container() -> bool:
    return Path("/.dockerenv").exists() or bool(os.environ.get("container"))


def availability() -> dict:
    """{"mode": "ready"|"setup-needed"|"unsupported", "reason": str}"""
    if platform.system() != "Linux":
        return {"mode": "unsupported", "reason": "not Linux: the update runs as the owner's own account"}
    if _in_container():
        return {"mode": "unsupported", "reason": "container install: update by pulling the new image"}
    if not Path("/run/systemd/system").exists():
        return {"mode": "unsupported", "reason": "no systemd on this machine"}
    if HELPER.exists() and CONF.exists() and (STATE_DIR / "trigger").is_dir():
        return {"mode": "ready", "reason": ""}
    return {"mode": "setup-needed", "reason": "the maintenance runner is not installed on this machine"}


def setup_command(project_dir: Path | None = None) -> str:
    """The one command the machine owner runs once to turn automatic maintenance on.

    Pinned to the release the owner is running, fetched over TLS, so it does not
    depend on the files in the (service-writable) install directory.
    """
    return (
        "curl -fsSL https://raw.githubusercontent.com/jaylfc/taOS/"
        f"{SETUP_URL_REF}/scripts/install-maint.sh | sudo bash -s -- --fetch {SETUP_URL_REF}"
    )


def read_status() -> dict:
    try:
        return json.loads((STATE_DIR / "status.json").read_text())
    except (OSError, ValueError):
        return {}


def read_state() -> dict:
    try:
        return json.loads((STATE_DIR / "state.json").read_text())
    except (OSError, ValueError):
        return {}


def summary() -> dict:
    avail = availability()
    out = {**avail, "status": read_status(), "steps": read_state().get("steps", {})}
    if avail["mode"] == "setup-needed":
        out["setup_command"] = setup_command()
    return out


def _request() -> None:
    d = STATE_DIR / "trigger"
    fd = os.open(d / f"{secrets.token_hex(8)}.req", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o640)
    os.close(fd)


async def run_pending(*, wait: float = WAIT_SECONDS) -> dict:
    """Ask the runner to apply pending signed steps and wait for its answer.

    Returns {"mode", "result", "steps", "detail"}; ``result`` is one of
    applied|nothing|failed|refused|error|timeout|unavailable. Never raises.
    """
    avail = availability()
    if avail["mode"] != "ready":
        return {"mode": avail["mode"], "result": "unavailable", "detail": avail["reason"], "steps": []}
    try:
        before = int(read_status().get("run_seq", 0))
        if os.geteuid() == 0 and HELPER.exists():  # root controller (legacy installer): no trigger needed
            proc = await asyncio.create_subprocess_exec(
                str(HELPER), "run-pending",
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=wait)
        else:
            _request()
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            st = read_status()
            if int(st.get("run_seq", 0)) > before and st.get("state") == "idle":
                return {"mode": "ready", "result": st.get("result", "error"),
                        "detail": st.get("detail", ""), "steps": st.get("steps", [])}
            await asyncio.sleep(POLL_SECONDS)
        return {"mode": "ready", "result": "timeout", "detail": "the maintenance runner did not finish in time", "steps": []}
    except Exception as exc:  # the update must still complete
        logger.warning("maintenance trigger failed: %s", exc)
        return {"mode": "ready", "result": "error", "detail": str(exc), "steps": []}


def failure_text(report: dict) -> str | None:
    """Human text for a report that needs the owner's attention, else None."""
    result = report.get("result")
    if result in ("applied", "nothing", None) or report.get("mode") == "unsupported":
        return None
    if result == "unavailable":
        return (f"Automatic maintenance is not set up on this machine. Run once as the machine owner: "
                f"{setup_command()}")
    failed = [s for s in report.get("steps", []) if s.get("result") in ("failed", "refused")]
    bits = "; ".join(f"{s['id']}: {s.get('detail') or s['result']}"[:300] for s in failed) or report.get("detail", "")
    return f"A maintenance step did not finish ({result}). {bits}".strip()

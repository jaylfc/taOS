"""Health of the host qmd service as the memory routes depend on it.

The memory routes pick an index by sending ``dbPath`` to qmd. A qmd that
ignores ``dbPath`` (npm ``@jaylfc/qmd`` before the multi-tenant fork, e.g.
2.6.0) answers every request from ONE default index, so per-agent and
per-person isolation does not exist behind the routes. ``qmd_honours_dbpath``
detects that, and the routes refuse rather than serve the shared index.

Signal: ``POST /delete-chunk`` with an empty body. A qmd that understands
``dbPath`` validates the body and answers 400; one that does not has no such
endpoint and answers 404. The request changes nothing and creates no file.
"""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

PINNED_QMD_VERSION = "2.8.3-taos.2"
PROBE_TTL_SECONDS = 60.0
UNKNOWN_TTL_SECONDS = 5.0

UNIT_PATH = Path("/etc/systemd/system/qmd.service")
DEFAULTS_PATH = Path("/etc/default/qmd")

UPGRADE_MESSAGE = (
    "The memory service (qmd) needs updating before agent and personal memory can be "
    "kept apart. As the machine owner run: sudo bash {script}"
)
BIND_FIX_COMMAND = (
    "echo 'QMD_BIND=127.0.0.1' | sudo tee -a /etc/default/qmd && sudo systemctl restart qmd"
)


def upgrade_command(project_dir: Path | None = None) -> str:
    base = project_dir or Path(__file__).resolve().parent.parent
    return f"sudo bash {base / 'scripts' / 'upgrade-qmd.sh'}"


def _is_new_qmd_answer(resp) -> bool:
    """True only for the answer a qmd that understands ``dbPath`` gives.

    Measured on ``@jaylfc/qmd@2.8.3-taos.2`` (and the older multi-tenant 2.1.1):
    ``POST /delete-chunk`` with ``{}`` answers HTTP 400 with a JSON object
    carrying a string ``error``. Anything else is not recognised: a 404 (a qmd
    without the endpoint, such as 2.5.3 to 2.6.1), a 200, a redirect, an HTML
    page from a proxy, a truncated or non-JSON body.
    """
    try:
        if resp.status_code != 400:
            return False
        body = resp.json()
        return isinstance(body, dict) and isinstance(body.get("error"), str) and bool(body["error"])
    except Exception:
        return False


async def qmd_honours_dbpath(state, *, force: bool = False) -> bool | None:
    """True only for a positively recognised new qmd; None only when qmd is unreachable.

    Fails closed: every answer that is not the recognised one returns False, so
    the routes refuse rather than risk an index shared across callers. A
    transport failure (connection refused, timeout) returns None and the real
    request then fails with its usual 502.

    Cached on ``state`` for a short TTL (unreachable for even less).
    """
    now = time.monotonic()
    cached = getattr(state, "_qmd_dbpath_probe", None)
    if cached and not force and cached[0] > now:
        return cached[1]
    result: bool | None
    try:
        base = state.qmd_client.base_url
        resp = await state.http_client.post(f"{base}/delete-chunk", json={}, timeout=3)
        result = _is_new_qmd_answer(resp)
    except httpx.TransportError as exc:  # qmd down or unreachable: the real call will 502
        logger.debug("qmd capability probe could not connect: %s", exc)
        result = None
    except Exception as exc:  # anything unexpected is not a recognised new qmd
        logger.warning("qmd capability probe failed unexpectedly: %s", exc)
        result = False
    ttl = UNKNOWN_TTL_SECONDS if result is None else PROBE_TTL_SECONDS
    state._qmd_dbpath_probe = (now + ttl, result)
    return result


def _env_value(text: str, key: str) -> str | None:
    value = None
    for line in text.splitlines():
        m = re.match(rf"^\s*(?:Environment=)?\"?{re.escape(key)}=([^\s\"]*)\"?\s*$", line)
        if m:
            value = m.group(1)
    return value


def qmd_bind_exposed(unit_path: Path = UNIT_PATH, defaults_path: Path = DEFAULTS_PATH) -> bool:
    """True when the installed qmd unit listens on all interfaces.

    ``/etc/default/qmd`` overrides the unit's ``Environment=`` (EnvironmentFile
    wins), so it is read last. A unit that cannot be read is not reported.
    """
    try:
        unit = unit_path.read_text()
    except OSError:
        return False
    bind = _env_value(unit, "QMD_BIND")
    try:
        override = _env_value(defaults_path.read_text(), "QMD_BIND")
    except OSError:
        override = None
    if override is not None:
        bind = override
    return bind in ("0.0.0.0", "::", "")


async def run_qmd_service_check(state) -> None:
    """Startup check: tell the install owner when qmd needs the owner's action."""
    import asyncio

    await asyncio.sleep(10)  # let qmd and the controller finish coming up
    notifications = getattr(state, "notifications", None)
    if notifications is None:
        return
    owner = None
    try:
        primary = state.auth.get_primary_user()
        owner = primary["id"] if primary else None
    except Exception:
        owner = None
    if await qmd_honours_dbpath(state) is False:
        await notifications.add(
            title="Memory service needs updating",
            message=UPGRADE_MESSAGE.format(script=upgrade_command().split(" ", 2)[2]),
            level="warning", source="memory", user_id=owner,
        )
    if qmd_bind_exposed():
        await notifications.add(
            title="Memory service is reachable from the network",
            message=f"qmd listens on every network interface. To limit it to this machine run: {BIND_FIX_COMMAND}",
            level="warning", source="memory", user_id=owner,
        )

"""Per-boot secret that identifies the device's own shell process.

A loopback peer address says only "something on this machine". It does not say
"the screen in front of the user": any local account, and any proxy or tunnel
that dials 127.0.0.1, looks the same. Console-only routes therefore also ask
for this secret.

* It lives in a file readable only by the shell's user (mode 0600, or 0640 with
  a group when ``TAOS_CONSOLE_SECRET_GROUP`` names one).
* It is regenerated on every boot: a file older than the current boot is
  replaced. A controller restart inside one boot keeps it, so the running shell
  is not locked out by an update.
* Scripts send it as ``X-taOS-Console: <secret>``. The browser shell trades it
  once, from a page only the shell user can read, for an HttpOnly cookie
  (``POST /auth/console-open``); the cookie value is derived from the secret,
  so the secret itself never sits in the browser profile.
"""
from __future__ import annotations

import hmac
import hashlib
import logging
import os
import secrets
import time
from pathlib import Path

import psutil

from tinyagentos.atomic_io import atomic_write_text

logger = logging.getLogger(__name__)

ENV_FILE = "TAOS_CONSOLE_SECRET_FILE"
ENV_GROUP = "TAOS_CONSOLE_SECRET_GROUP"
DEFAULT_FILE = "/run/taos/console-secret"
COOKIE_NAME = "taos_console"

_cache: dict[str, tuple[tuple[int, int], str]] = {}
_warned: set[str] = set()


def _candidate_paths(data_dir: Path | None) -> list[Path]:
    env = os.environ.get(ENV_FILE, "").strip()
    if env:
        return [Path(env)]
    paths = [Path(DEFAULT_FILE)]
    if data_dir is not None:
        paths.append(Path(data_dir) / ".console_secret")
    return paths


def _fresh(path: Path) -> bool:
    """True when the file exists, is not group/world writable and is from this boot."""
    try:
        st = path.stat()
    except OSError:
        return False
    if st.st_mode & 0o022:
        return False
    try:
        booted = psutil.boot_time()
    except Exception:  # noqa: BLE001 - unknown boot time: treat as stale
        return False
    return st.st_mtime >= booted


def _read(path: Path) -> str | None:
    try:
        st = path.stat()
        key = (st.st_mtime_ns, st.st_ino)
        hit = _cache.get(str(path))
        if hit is not None and hit[0] == key:
            return hit[1]
        value = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not value:
        return None
    _cache[str(path)] = (key, value)
    return value


def _write(path: Path) -> str | None:
    value = secrets.token_hex(32)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        group = os.environ.get(ENV_GROUP, "").strip()
        atomic_write_text(path, value + "\n", mode=0o640 if group else 0o600)
        if group:
            import grp

            os.chown(path, -1, grp.getgrnam(group).gr_gid)
    except (OSError, KeyError) as exc:
        if str(path) not in _warned:
            _warned.add(str(path))
            logger.warning("console secret not writable at %s: %s", path, exc)
        return None
    return value


def get_secret(data_dir: Path | None = None) -> str | None:
    """The current per-boot secret, creating it if this boot has none.

    None means no secret could be established. Callers must treat that as "not
    the console" (fail closed): loopback alone never means console.
    """
    for path in _candidate_paths(data_dir):
        if _fresh(path):
            value = _read(path)
            if value:
                return value
        value = _write(path)
        if value:
            return value
    return None


#: One-time tickets: the shell's launcher mints one (proving it can read the
#: secret file) and hands it to the browser in the start URL, so neither the
#: secret nor anything reusable shows up in the process list.
TICKET_PARAM = "console_ticket"
TICKET_TTL_S = 60
_tickets: dict[str, float] = {}


def mint_ticket(now: float | None = None) -> str:
    now = time.time() if now is None else now
    for t in [t for t, exp in _tickets.items() if exp <= now]:
        del _tickets[t]
    ticket = secrets.token_urlsafe(24)
    _tickets[ticket] = now + TICKET_TTL_S
    return ticket


def redeem_ticket(ticket: str, now: float | None = None) -> bool:
    """True once per valid, unexpired ticket."""
    now = time.time() if now is None else now
    expiry = _tickets.pop(ticket or "", None)
    return expiry is not None and expiry > now


def header_matches(secret: str | None, headers) -> bool:
    """Only the header form (scripts that can read the secret file)."""
    presented = (headers.get("x-taos-console", "") if headers else "").strip()
    return bool(secret and presented) and hmac.compare_digest(
        presented.encode(), secret.encode()
    )


def cookie_value(secret: str) -> str:
    return hmac.new(secret.encode(), b"taos-console-cookie-v1", hashlib.sha256).hexdigest()


def matches(secret: str | None, headers, cookies) -> bool:
    """Does this request carry the secret (header) or its derived cookie?"""
    if not secret:
        return False
    presented = (headers.get("x-taos-console", "") if headers else "").strip()
    if presented and hmac.compare_digest(presented.encode(), secret.encode()):
        return True
    cookie = (cookies.get(COOKIE_NAME, "") if cookies else "")
    return bool(cookie) and hmac.compare_digest(
        cookie.encode(), cookie_value(secret).encode()
    )

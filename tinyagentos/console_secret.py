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


def current_boot_id() -> str | None:
    """The kernel's id for this boot, or None where there is none."""
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip() or None
    except OSError:
        return None


def _split(path: Path) -> tuple[str, str | None] | None:
    """(secret, boot id the file was written under) or None when unreadable."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    lines = [ln.strip() for ln in lines if ln.strip()]
    if not lines:
        return None
    return lines[0], (lines[1] if len(lines) > 1 else None)


def _fresh(path: Path) -> bool:
    """Is this the secret for THIS boot, readable only by who it should be?

    The boot id decides freshness, not the clock: a phone with no RTC jumps its
    clock when NTP answers, which must not rotate the secret mid-boot. A file
    with no recorded boot id falls back to its mtime against the boot time.
    """
    try:
        st = path.stat()
    except OSError:
        return False
    if st.st_uid != os.geteuid():
        return False
    group_ok = bool(os.environ.get(ENV_GROUP, "").strip())
    if st.st_mode & (0o037 if group_ok else 0o077):
        return False
    parts = _split(path)
    if parts is None:
        return False
    boot = current_boot_id()
    if parts[1] is not None and boot is not None:
        return parts[1] == boot
    try:
        return st.st_mtime >= psutil.boot_time()
    except Exception:  # noqa: BLE001 - unknown boot time: treat as stale
        return False


def _read(path: Path) -> str | None:
    try:
        st = path.stat()
        key = (st.st_mtime_ns, st.st_ino)
        hit = _cache.get(str(path))
        if hit is not None and hit[0] == key:
            return hit[1]
    except OSError:
        return None
    parts = _split(path)
    if parts is None:
        return None
    _cache[str(path)] = (key, parts[0])
    return parts[0]


def _write(path: Path) -> str | None:
    value = secrets.token_hex(32)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        group = os.environ.get(ENV_GROUP, "").strip()
        atomic_write_text(
            path, value + "\n" + (current_boot_id() or "") + "\n",
            mode=0o640 if group else 0o600,
        )
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
_tickets: dict[str, tuple[float, int | None]] = {}


def peer_uid(host: str | None, port: int | None) -> int | None:
    """The uid that owns the client end of a loopback TCP connection.

    Read from /proc/net/tcp{,6}: the row whose LOCAL address is the peer's
    address and port is the client's socket. None where that cannot be read.
    """
    if not host or not port:
        return None
    import ipaddress

    try:
        addr = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return None
    mapped = getattr(addr, "ipv4_mapped", None)
    wanted = {str(addr)}
    if mapped is not None:
        wanted.add(str(mapped))
    for table, v6 in (("/proc/net/tcp", False), ("/proc/net/tcp6", True)):
        try:
            rows = Path(table).read_text().splitlines()[1:]
        except OSError:
            continue
        for row in rows:
            f = row.split()
            if len(f) < 8:
                continue
            ip_hex, _, port_hex = f[1].partition(":")
            if int(port_hex, 16) != int(port):
                continue
            try:
                raw = bytes.fromhex(ip_hex)
                if v6:
                    raw = b"".join(raw[i:i + 4][::-1] for i in range(0, 16, 4))
                    ip = ipaddress.ip_address(raw)
                    ip = getattr(ip, "ipv4_mapped", None) or ip
                else:
                    ip = ipaddress.ip_address(raw[::-1])
            except ValueError:
                continue
            if str(ip) in wanted:
                return int(f[7])
    return None


def mint_ticket(uid: int | None, now: float | None = None) -> str:
    """A one-time ticket bound to ``uid``. An unknown uid is refused."""
    if uid is None:
        raise ValueError("cannot bind a ticket to an unknown uid")
    now = time.time() if now is None else now
    for t in [t for t, (exp, _) in _tickets.items() if exp <= now]:
        del _tickets[t]
    ticket = secrets.token_urlsafe(24)
    _tickets[ticket] = (now + TICKET_TTL_S, uid)
    return ticket


def redeem_ticket(ticket: str, uid: int | None, now: float | None = None) -> bool:
    """True once per valid, unexpired ticket, for the uid that minted it.

    The ticket sits in the browser's argv, which other local users can read, so
    it only works for the account that asked for it. A wrong-uid attempt does
    not burn it. An unknown uid (cannot be read) is refused: fail closed.
    """
    if uid is None:
        return False
    now = time.time() if now is None else now
    hit = _tickets.get(ticket or "")
    if hit is None or hit[0] <= now:
        _tickets.pop(ticket or "", None)
        return False
    if hit[1] != uid:
        return False
    del _tickets[ticket]
    return True


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

"""Controller-side half of the taOS software updater.

This module fetches and pre-checks the signed update manifest and reads the
status file written by the separate maintenance helper. It never installs
anything and never runs as root: the helper owns every privileged step.

CLI: ``python -m tinyagentos.updates status|check`` prints JSON.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from tinyagentos.atomic_io import atomic_write_text

DEFAULT_CHANNEL = "stable"
DEFAULT_FEED = "https://taos.my/updates/{channel}/"
DEFAULT_MIRROR = "https://github.com/jaylfc/tinyagentos/releases/download/updates-{channel}/"
DEFAULT_KEYRING = "/etc/taos/maint/release.gpg"
DEFAULT_STATUS_FILE = "/var/lib/taos-maint/status.json"

FETCH_TIMEOUT = 10.0
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_SIG_BYTES = 64 * 1024
MAX_WINDOW = timedelta(days=30)
CACHE_NAME = "last_check.json"

_BAD_STATUS = frozenset(
    {"BADSIG", "ERRSIG", "EXPSIG", "EXPKEYSIG", "REVKEYSIG", "NO_PUBKEY"}
)
_RELEASE_RE = re.compile(r"^release-(\d+)$")


@dataclass
class UpdateConfig:
    channel: str = DEFAULT_CHANNEL
    feed_url: str = ""
    mirror_url: str = ""
    keyring: str = DEFAULT_KEYRING
    status_file: str = DEFAULT_STATUS_FILE
    timeout: float = FETCH_TIMEOUT

    def __post_init__(self) -> None:
        if not self.feed_url:
            self.feed_url = DEFAULT_FEED.format(channel=self.channel)
        if not self.mirror_url:
            self.mirror_url = DEFAULT_MIRROR.format(channel=self.channel)

    @classmethod
    def from_env(cls) -> "UpdateConfig":
        env = os.environ
        channel = env.get("TAOS_UPDATE_CHANNEL") or DEFAULT_CHANNEL
        return cls(
            channel=channel,
            feed_url=env.get("TAOS_UPDATE_FEED", ""),
            mirror_url=env.get("TAOS_UPDATE_MIRROR", ""),
            keyring=env.get("TAOS_UPDATE_KEYRING") or DEFAULT_KEYRING,
            status_file=env.get("TAOS_UPDATE_STATUS") or DEFAULT_STATUS_FILE,
        )


def _result(ok: bool, reason: str | None = None, source: str | None = None, **kw: Any) -> dict:
    out: dict[str, Any] = {
        "ok": ok, "source": source, "seq": None, "release": None,
        "published": None, "expires": None, "reason": reason,
    }
    out.update(kw)
    return out


# ---------------------------------------------------------------- fetching

class _FetchError(Exception):
    pass


def _fetch(url: str, limit: int, timeout: float) -> bytes:
    """GET `url`; raise _FetchError on network error, non-200, timeout or oversize.

    Oversize raises _TooLarge (a refusal, not a reason to try the mirror).
    """
    if not url.startswith(("http://", "https://")):
        raise _FetchError("unsupported scheme")
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
            if resp.status != 200:
                raise _FetchError(f"http {resp.status}")
            data = resp.read(limit + 1)
    except _FetchError:
        raise
    except Exception as exc:  # URLError, HTTPError, timeout, ssl, ...
        raise _FetchError(str(exc)) from exc
    if len(data) > limit:
        raise _TooLarge()
    return data


class _TooLarge(Exception):
    pass


# ------------------------------------------------------------ verification

def _status_accepts(status_text: str) -> bool:
    """True iff the gpgv status lines show exactly one GOODSIG and one VALIDSIG
    and none of the failure keywords."""
    good = valid = 0
    for line in status_text.splitlines():
        parts = line.split()
        if len(parts) < 2 or parts[0] != "[GNUPG:]":
            continue
        kw = parts[1]
        if kw in _BAD_STATUS:
            return False
        if kw == "GOODSIG":
            good += 1
        elif kw == "VALIDSIG":
            valid += 1
    return good == 1 and valid == 1


def _verify(manifest: bytes, sig: bytes, keyring: str) -> bool:
    """PRE-check for the UI only.

    The root helper re-verifies the signature itself before installing
    anything; this result only decides what the UI shows and is never an
    authorisation to install.
    """
    ring = Path(keyring)
    if not ring.is_file():
        return False
    try:
        with tempfile.TemporaryDirectory(prefix="taos-gpgv-") as tmp:
            tmp_p = Path(tmp)
            home = tmp_p / "home"
            home.mkdir(mode=0o700)
            (tmp_p / "manifest.json").write_bytes(manifest)
            (tmp_p / "manifest.json.sig").write_bytes(sig)
            env = {**os.environ, "GNUPGHOME": str(home)}
            proc = subprocess.run(
                ["gpgv", "--keyring", str(ring.resolve()), "--status-fd", "1",
                 str(tmp_p / "manifest.json.sig"), str(tmp_p / "manifest.json")],
                capture_output=True, text=True, env=env, timeout=20, check=False,
            )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0 and _status_accepts(proc.stdout)


# ----------------------------------------------------------------- parsing

def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return None


def _step_ids(steps: Any) -> list[str]:
    ids: list[str] = []
    if not isinstance(steps, list):
        return ids
    for s in steps:
        sid = s.get("id") if isinstance(s, dict) else s
        if isinstance(sid, str):
            ids.append(sid)
    return ids


def _release_number(step_ids: list[str]) -> int | None:
    nums = [int(m.group(1)) for sid in step_ids if (m := _RELEASE_RE.match(sid))]
    return max(nums) if nums else None


def _evaluate(manifest: bytes, source: str) -> dict:
    try:
        doc = json.loads(manifest)
    except (ValueError, UnicodeDecodeError):
        return _result(False, "malformed", source)
    if not isinstance(doc, dict):
        return _result(False, "malformed", source)
    fmt = doc.get("format")
    seq = doc.get("seq")
    if fmt not in (1, 2) or isinstance(fmt, bool):
        return _result(False, "malformed", source)
    if not isinstance(seq, int) or isinstance(seq, bool):
        return _result(False, "malformed", source)
    published = _parse_time(doc.get("published"))
    if published is None:
        return _result(False, "malformed", source)
    expires = None
    if fmt == 2:
        expires = _parse_time(doc.get("expires"))
        if expires is None:
            return _result(False, "malformed", source)
        if expires <= datetime.now(timezone.utc):
            return _result(False, "expired", source)
        if expires - published > MAX_WINDOW:
            return _result(False, "window", source)
    return _result(
        True, None, source, seq=seq,
        release=_release_number(_step_ids(doc.get("steps"))),
        published=published.isoformat(),
        expires=expires.isoformat() if expires else None,
    )


# ------------------------------------------------------------------ public

def _try_source(base: str, source: str, cfg: UpdateConfig) -> dict | None:
    """Fetch and verify from one feed. None means unreachable (try the next)."""
    base = base if base.endswith("/") else base + "/"
    try:
        manifest = _fetch(base + "manifest.json", MAX_MANIFEST_BYTES, cfg.timeout)
        sig = _fetch(base + "manifest.json.sig", MAX_SIG_BYTES, cfg.timeout)
    except _FetchError:
        return None
    except _TooLarge:
        return _result(False, "too-large", source)
    if not _verify(manifest, sig, cfg.keyring):
        return _result(False, "signature", source)
    return _evaluate(manifest, source)


def check(cfg: UpdateConfig | None = None, cache_dir: Path | str | None = None) -> dict:
    """Fetch the signed manifest (primary, then mirror) and pre-check it.

    PRE-check for the UI only: the privileged helper verifies the manifest
    again on its own before acting. Never raises; returns
    {ok, source, seq, release, published, expires, reason}.
    """
    cfg = cfg or UpdateConfig.from_env()
    try:
        result = None
        for base, source in ((cfg.feed_url, "primary"), (cfg.mirror_url, "mirror")):
            result = _try_source(base, source, cfg)
            if result is not None:
                break
        if result is None:
            result = _result(False, "unreachable")
    except Exception as exc:  # never raise to the caller
        result = _result(False, f"error: {type(exc).__name__}")
    result["checked_at"] = datetime.now(timezone.utc).isoformat()
    if cache_dir is not None:
        _save_last_check(Path(cache_dir), result)
    return result


def _save_last_check(cache_dir: Path, result: dict) -> None:
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_text(cache_dir / CACHE_NAME, json.dumps(result))
    except OSError:
        pass


def last_check(cache_dir: Path | str | None) -> dict | None:
    if cache_dir is None:
        return None
    try:
        doc = json.loads((Path(cache_dir) / CACHE_NAME).read_text())
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def status(cfg: UpdateConfig | None = None) -> dict:
    """Read the helper's status file; {"helper": "absent"} if missing or unreadable."""
    cfg = cfg or UpdateConfig.from_env()
    try:
        with open(cfg.status_file, "rb") as fh:
            doc = json.loads(fh.read(MAX_MANIFEST_BYTES))
    except (OSError, ValueError):
        return {"helper": "absent"}
    if not isinstance(doc, dict):
        return {"helper": "absent"}
    doc["helper"] = "present"
    return doc


def installed_release(st: dict) -> int | None:
    """Newest release step the helper reports as applied (or already current)."""
    steps = st.get("steps")
    if not isinstance(steps, list):
        return None
    nums = []
    for s in steps:
        if not isinstance(s, dict) or s.get("result") not in ("applied", "nothing"):
            continue
        sid = s.get("id")
        m = _RELEASE_RE.match(sid) if isinstance(sid, str) else None
        if m:
            nums.append(int(m.group(1)))
    return max(nums) if nums else None


def summary(cfg: UpdateConfig, cache_dir: Path | str | None) -> dict:
    """Payload for GET /api/updates."""
    st = status(cfg)
    installed = installed_release(st)
    lc = last_check(cache_dir)
    available = None
    if lc and lc.get("ok") and isinstance(lc.get("release"), int):
        if lc["release"] > (installed or 0):
            available = lc["release"]
    return {
        "channel": cfg.channel,
        "installed_release": installed,
        "last_check": lc,
        "available_release": available,
        "helper": st,
    }


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    cmd = args[0] if args else ""
    if cmd == "status":
        print(json.dumps(status(), indent=2))
        return 0
    if cmd == "check":
        data_dir = os.environ.get("TAOS_DATA_DIR")
        cache = Path(data_dir) / "updates" if data_dir else None
        print(json.dumps(check(cache_dir=cache), indent=2))
        return 0
    if cmd in ("install", "rollback"):
        print(f"{cmd}: not available yet", file=sys.stderr)
        return 2
    print("usage: python -m tinyagentos.updates status|check", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())

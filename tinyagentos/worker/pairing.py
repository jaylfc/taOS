"""Worker-side pairing utilities.

Handles the announce -> admin-confirm -> claim flow, key persistence,
and request signing. The HMAC signing string and headers must byte-match
tinyagentos.cluster.worker_auth:

    message = f"{timestamp}.{METHOD}.{path}.{sha256(body).hexdigest()}"
    headers: X-TAOS-Worker-Name, X-TAOS-Timestamp, X-TAOS-Signature
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import secrets
import sys
import time
from pathlib import Path

# Unambiguous alphabet: strips 0/O/1/I/l to reduce transcription errors.
_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz"


def default_state_dir() -> Path:
    """Return the default directory for persisting worker state.

    Priority:
      1. $TAOS_WORKER_STATE_DIR (explicit override)
      2. $XDG_STATE_HOME/taos-worker  (POSIX)
      3. %LOCALAPPDATA%/taos-worker   (Windows)
      4. ~/.local/state/taos-worker   (POSIX fallback)
    """
    override = os.environ.get("TAOS_WORKER_STATE_DIR")
    if override:
        return Path(override)
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
        return Path(base) / "taos-worker"
    xdg = os.environ.get("XDG_STATE_HOME")
    if xdg:
        return Path(xdg) / "taos-worker"
    return Path.home() / ".local" / "state" / "taos-worker"


def key_path(state_dir: Path) -> Path:
    """Return the path for the 32-byte signing key file."""
    return state_dir / "signing_key"


def load_signing_key(state_dir: Path) -> bytes | None:
    """Return the persisted signing key, or None if absent."""
    p = key_path(state_dir)
    try:
        return p.read_bytes()
    except FileNotFoundError:
        return None


def save_signing_key(state_dir: Path, key: bytes) -> None:
    """Persist the signing key with restricted permissions (0600 on POSIX)."""
    state_dir.mkdir(parents=True, exist_ok=True)
    p = key_path(state_dir)
    p.write_bytes(key)
    if sys.platform != "win32":
        p.chmod(0o600)


def generate_pairing_code() -> str:
    """Return an 8-character code using an unambiguous alphabet."""
    return "".join(secrets.choice(_ALPHABET) for _ in range(8))


def code_hash(code: str) -> str:
    """Return the sha256 hex digest of the code (matches pairing_store)."""
    return hashlib.sha256(code.encode()).hexdigest()


def sign_request_headers(
    key: bytes,
    name: str,
    method: str,
    path: str,
    body: bytes,
) -> dict:
    """Return the three HMAC auth headers for a worker request.

    Signing string: f"{timestamp}.{METHOD}.{path}.{sha256(body).hexdigest()}"
    """
    ts = str(int(time.time()))
    body_hash = hashlib.sha256(body).hexdigest()
    message = f"{ts}.{method.upper()}.{path}.{body_hash}".encode()
    sig = hmac.new(key, message, hashlib.sha256).hexdigest()
    return {
        "X-TAOS-Worker-Name": name,
        "X-TAOS-Timestamp": ts,
        "X-TAOS-Signature": sig,
    }


async def run_pairing(
    client,
    controller_url: str,
    name: str,
    url: str,
    platform: str,
    state_dir: Path,
    *,
    poll_interval: float = 3.0,
    timeout: float = 600.0,
    print_fn=print,
) -> bytes:
    """Drive the full pairing flow and return the signing key.

    If a key already exists in state_dir, returns it immediately (idempotent).

    Raises TimeoutError if the admin does not confirm within timeout seconds.
    Raises RuntimeError on a 404 (invalidated/unknown worker).
    """
    existing = load_signing_key(state_dir)
    if existing is not None:
        return existing

    deadline = time.monotonic() + timeout
    controller_url = controller_url.rstrip("/")

    code = generate_pairing_code()
    await _announce(client, controller_url, name, url, platform, code, print_fn)

    while True:
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"pairing timed out after {timeout}s; "
                f"re-run `python -m tinyagentos.worker.pair {controller_url} --name {name}` to resume"
            )
        await asyncio.sleep(poll_interval)

        resp = await client.post(
            f"{controller_url}/api/cluster/pairing/claim",
            json={"name": name, "code": code},
        )
        if resp.status_code == 202:
            # Not confirmed yet; keep polling.
            continue
        if resp.status_code == 410:
            # Expired; re-announce with a fresh code.
            code = generate_pairing_code()
            await _announce(client, controller_url, name, url, platform, code, print_fn)
            continue
        if resp.status_code == 200:
            key = bytes.fromhex(resp.json()["signing_key"])
            save_signing_key(state_dir, key)
            return key
        # 404 or any other error
        raise RuntimeError(
            f"pairing claim failed with HTTP {resp.status_code}: {resp.json()}"
        )


async def _announce(
    client,
    controller_url: str,
    name: str,
    url: str,
    platform: str,
    code: str,
    print_fn,
) -> None:
    resp = await client.post(
        f"{controller_url}/api/cluster/pairing/announce",
        json={
            "name": name,
            "url": url,
            "platform": platform,
            "code_hash": code_hash(code),
        },
    )
    if resp.status_code != 200:
        raise RuntimeError(
            f"announce failed with HTTP {resp.status_code}: {resp.json()}"
        )
    # Print the code prominently so the user can enter it in the taOS UI.
    print_fn("")
    print_fn("=" * 56)
    print_fn(f"  Pairing code: {code[:4]}-{code[4:]}")
    print_fn("  Enter this in taOS > Cluster to approve this worker")
    print_fn("=" * 56)
    print_fn("")

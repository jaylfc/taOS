"""Worker pairing CLI.

Drives the announce -> admin-confirm -> claim flow and optionally
performs a signed first-registration so the worker is known to the
controller before the incus-enroll step in the install script.

Usage (called by install-worker.sh and install-worker.ps1):
    python -m tinyagentos.worker.pair <controller_url> \\
        --name <name> --url <worker_url> --register-after

Exit codes:
    0  paired (and registered if --register-after)
    1  pairing error (timeout, invalidated, network)
"""
from __future__ import annotations

import argparse
import asyncio
import json as _json
import platform
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Pair this worker with a taOS controller")
    parser.add_argument("controller", help="Controller URL, e.g. http://192.168.1.10:6969")
    parser.add_argument("--name", help="Worker name (default: hostname)")
    parser.add_argument("--url", help="Advertised worker URL (default: auto-detected LAN URL)")
    parser.add_argument("--platform", dest="platform_name",
                        default=platform.system().lower(),
                        help="Platform string (default: current OS)")
    parser.add_argument("--state-dir", type=Path, default=None,
                        help="Directory for key persistence (default: system default)")
    parser.add_argument("--manual", action="store_true",
                        help="Free-tier manual pairing: show address + PIN and "
                             "poll for admin authorisation (no announce/discovery)")
    parser.add_argument("--register-after", action="store_true",
                        help="Send a signed POST /api/cluster/workers after pairing")
    parser.add_argument("--timeout", type=float, default=600.0,
                        help="Max seconds to wait for admin confirmation (default: 600)")
    args = parser.parse_args()

    try:
        asyncio.run(_run(args))
    except TimeoutError as exc:
        print(f"\n[pair] {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:  # noqa: BLE001
        print(f"\n[pair] pairing failed: {exc}", file=sys.stderr)
        sys.exit(1)


async def _run(args) -> None:
    import httpx
    from tinyagentos.worker.pairing import (
        default_state_dir,
        run_manual_pairing,
        run_pairing,
    )

    state_dir = args.state_dir or default_state_dir()
    name = args.name or _hostname()

    # Resolve advertised URL — use provided, or infer from LAN IP.
    if args.url:
        worker_url = args.url
    else:
        from tinyagentos.worker.agent import WorkerAgent
        agent = WorkerAgent(args.controller, name=name, state_dir=state_dir)
        worker_url = agent.get_worker_url()

    async with httpx.AsyncClient(timeout=15) as client:
        if args.manual:
            key = await run_manual_pairing(
                client,
                args.controller,
                name,
                worker_url,
                state_dir,
                timeout=args.timeout,
            )
        else:
            key = await run_pairing(
                client,
                args.controller,
                name,
                worker_url,
                args.platform_name,
                state_dir,
                timeout=args.timeout,
            )

    print(f"\n[pair] paired successfully as '{name}'. Signing key saved to {state_dir}")

    if args.register_after:
        await _signed_register(args.controller, name, worker_url, args.platform_name, key)


async def _signed_register(
    controller_url: str,
    name: str,
    worker_url: str,
    plat: str,
    key: bytes,
) -> None:
    """POST /api/cluster/workers with HMAC auth so the worker is known before incus-enroll."""
    import httpx
    from tinyagentos.worker.pairing import sign_request_headers

    controller_url = controller_url.rstrip("/")
    path = "/api/cluster/workers"
    payload = {
        "name": name,
        "url": worker_url,
        "platform": plat,
        "hardware": {},
        "backends": [],
        "capabilities": [],
        "models": [],
    }
    body = _json.dumps(payload).encode()
    headers = sign_request_headers(key, name, "POST", path, body)
    headers["content-type"] = "application/json"

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{controller_url}{path}",
            content=body,
            headers=headers,
        )

    if resp.status_code in (200, 409):
        status = "registered" if resp.status_code == 200 else "already registered"
        print(f"[pair] worker {status} with controller")
    else:
        raise RuntimeError(
            f"signed register failed with HTTP {resp.status_code}: {resp.text}"
        )


def _hostname() -> str:
    import socket
    return socket.gethostname()


if __name__ == "__main__":
    main()

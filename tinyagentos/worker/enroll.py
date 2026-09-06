"""Signed incus-enrollment for a paired worker.

After pairing persists the HMAC signing key, the installer enrols the
worker's nested incus daemon with the controller so the controller can
deploy agent containers into it. That endpoint is HMAC-gated like
``POST /api/cluster/workers`` and ``/api/cluster/heartbeat``, so the
request must be signed with the worker's signing key — which is why this
lives in Python, not the installer's shell (crypto stays out of bash).

Usage (called by install-worker.sh):
    python -m tinyagentos.worker.enroll <controller_url> \\
        --name <name> --incus-url <url> --token <trust_token> \\
        [--state-dir DIR]

Exit codes:
    0  enrolled
    1  enrollment error (not paired, network, controller rejection)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Enrol this worker's incus daemon with a taOS controller"
    )
    parser.add_argument("controller", help="Controller URL, e.g. http://192.168.1.10:6969")
    parser.add_argument("--name", required=True, help="Worker name (must match registration)")
    parser.add_argument("--incus-url", required=True, help="This worker's incus HTTPS URL")
    parser.add_argument("--token", required=True, help="incus trust token for the controller")
    parser.add_argument("--state-dir", type=Path, default=None,
                        help="Directory holding the signing key (default: system default)")
    args = parser.parse_args()

    import httpx
    from tinyagentos.worker.pairing import (
        default_state_dir,
        load_signing_key,
        sign_request_headers,
    )

    controller = args.controller.rstrip("/")
    state_dir = args.state_dir or default_state_dir()
    key = load_signing_key(state_dir)
    if key is None:
        print(
            f"[enroll] not paired: no signing key at {state_dir}; pair the worker first",
            file=sys.stderr,
        )
        sys.exit(1)

    path = f"/api/cluster/workers/{args.name}/incus-enroll"
    body = json.dumps({"incus_url": args.incus_url, "token": args.token}).encode()
    headers = sign_request_headers(key, args.name, "POST", path, body)
    headers["content-type"] = "application/json"

    try:
        resp = httpx.post(f"{controller}{path}", content=body, headers=headers, timeout=30)
    except Exception as exc:  # noqa: BLE001
        print(f"[enroll] request failed: {exc}", file=sys.stderr)
        sys.exit(1)

    if resp.status_code // 100 == 2:
        print(f"[enroll] worker incus enrolled with controller (HTTP {resp.status_code})")
        sys.exit(0)

    print(
        f"[enroll] enrollment rejected (HTTP {resp.status_code}): {resp.text[:300]}",
        file=sys.stderr,
    )
    sys.exit(1)


if __name__ == "__main__":
    main()

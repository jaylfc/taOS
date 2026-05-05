"""`taos worker ...` subcommands.

Manage the local worker LXC on the controller host (the bare host this
CLI is running on). Three subcommands:

  taos worker convert-to-lxc <controller_url> [-y]
      Convert a flat-mode taOS install to worker-LXC mode. Drains
      existing flat-mode agents (memory dirs on shared cluster storage
      survive), runs install-worker.sh fresh to set up the worker LXC,
      and redeploys each agent inside the new worker LXC's nested incus.

  taos worker dedup enable|disable
      Toggle bees deduplication daemon inside the worker LXC.

  taos worker resize-storage --size NGB
      Expand the worker LXC's btrfs loopback file. Stops the LXC,
      truncates the file to the new size, restarts the LXC, and
      grows the btrfs filesystem.

Invoked via the ``taos-worker-ctl`` console script or directly:

    python -m tinyagentos.cli.worker <subcommand> ...
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import subprocess
import sys
from pathlib import Path

from tinyagentos.cluster.convert_to_lxc import (
    drain_and_delete_agents,
    list_flat_mode_agents,
    redeploy_agents,
)

logger = logging.getLogger(__name__)


def _load_agents_json(path: Path = Path("data/agents.json")) -> list[dict]:
    """Load the controller's agents.json as a list of agent config dicts."""
    if not path.exists():
        return []
    return json.loads(path.read_text())


async def _convert_to_lxc(args) -> int:
    print("Enumerating flat-mode agents...")
    agents = list_flat_mode_agents()
    if not agents:
        print("No flat-mode agents found; nothing to drain.")
    else:
        print(f"Found {len(agents)} flat-mode agents:")
        for a in agents:
            print(f"  {a['name']} ({a['state']})")
        if not args.yes:
            try:
                resp = input("Delete these and continue? [y/N] ")
            except EOFError:
                resp = "n"
            if resp.strip().lower() != "y":
                print("Aborted.")
                return 1

        print("Stopping and deleting flat-mode agents...")
        await drain_and_delete_agents(agents)

    print("Running install-worker.sh fresh...")
    install_path = Path("scripts/install-worker.sh")
    if not install_path.exists():
        print(f"ERROR: {install_path} not found. Run from the repo root.", file=sys.stderr)
        return 2
    r = subprocess.run(
        ["bash", str(install_path), args.controller_url],
        check=False,
    )
    if r.returncode != 0:
        print(f"install-worker.sh failed with code {r.returncode}", file=sys.stderr)
        return r.returncode

    print("Redeploying agents into worker LXC...")
    agent_cfgs = _load_agents_json()
    await redeploy_agents(agent_cfgs)

    print("Convert-to-LXC complete.")
    return 0


def _dedup(args) -> int:
    """Toggle bees inside the worker LXC."""
    action = args.action  # "enable" or "disable"
    cmd = [
        "sudo", "incus", "exec", "taos-worker", "--",
        "systemctl", action, "--now", "bees.service",
    ]
    r = subprocess.run(cmd, check=False)
    if r.returncode != 0:
        print(f"systemctl {action} bees.service failed inside worker LXC", file=sys.stderr)
    else:
        print(f"bees {action}d in worker LXC")
    return r.returncode


def _parse_iec_bytes(s: str) -> int:
    """Parse '500G', '1T', '512M', or raw bytes into integer bytes.
    Accepts the same forms as truncate(1)."""
    s = s.strip()
    units = {"K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}
    if not s:
        raise ValueError("empty size")
    if s[-1].upper() in units:
        return int(float(s[:-1]) * units[s[-1].upper()])
    return int(s)


def _resize_storage(args) -> int:
    """Resize the worker LXC's btrfs loopback file in place.

    Sequence:
      1. Stop the worker LXC.
      2. truncate -s <new_size> the loopback image.
      3. Start the worker LXC.
      4. Grow the btrfs filesystem inside via ``btrfs filesystem resize max``.
    """
    pool_img = "/var/lib/incus/disks/taos-worker-pool.img"
    new_size_str = args.size

    try:
        new_bytes = _parse_iec_bytes(new_size_str)
    except ValueError as exc:
        print(f"Invalid --size: {exc}", file=sys.stderr)
        return 2

    # Pre-flight: refuse to shrink (would destroy data).
    try:
        current_bytes = int(subprocess.check_output(
            ["sudo", "stat", "-c", "%s", pool_img], text=True
        ).strip())
    except subprocess.CalledProcessError:
        print(f"Could not stat {pool_img}; is the worker installed?", file=sys.stderr)
        return 2
    if new_bytes <= current_bytes:
        print(
            f"Refusing to shrink: current size is {current_bytes} bytes, "
            f"requested {new_bytes} bytes ({new_size_str}). "
            f"Shrinking would destroy data.",
            file=sys.stderr,
        )
        return 2

    print("Stopping taos-worker...")
    subprocess.run(["sudo", "incus", "stop", "taos-worker"], check=True)

    print(f"Resizing {pool_img} to {new_size_str}...")
    subprocess.run(["sudo", "truncate", "-s", new_size_str, pool_img], check=True)

    print("Starting taos-worker...")
    subprocess.run(["sudo", "incus", "start", "taos-worker"], check=True)

    print("Resizing btrfs filesystem inside worker LXC...")
    subprocess.run(
        [
            "sudo", "incus", "exec", "taos-worker", "--",
            "btrfs", "filesystem", "resize", "max",
            "/var/lib/incus/storage-pools/default",
        ],
        check=True,
    )
    print(f"Resize to {new_size_str} complete.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build the argparse tree for ``taos worker ...``.

    Returned so the parent CLI dispatcher can attach this as a subparser
    group, and so tests can drive the parser directly without side-effects.
    """
    parser = argparse.ArgumentParser(
        prog="taos worker",
        description="Manage the local taOS worker LXC",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    # --- convert-to-lxc ---
    p_convert = sub.add_parser(
        "convert-to-lxc",
        help="Convert flat-mode install to worker-LXC mode",
    )
    p_convert.add_argument("controller_url", help="Controller base URL, e.g. http://192.168.1.10:6969")
    p_convert.add_argument(
        "-y", "--yes", action="store_true",
        help="Skip confirmation prompt",
    )
    p_convert.set_defaults(func=lambda a: asyncio.run(_convert_to_lxc(a)))

    # --- dedup ---
    p_dedup = sub.add_parser(
        "dedup",
        help="Enable/disable bees deduplication daemon inside the worker LXC",
    )
    p_dedup.add_argument("action", choices=["enable", "disable"])
    p_dedup.set_defaults(func=_dedup)

    # --- resize-storage ---
    p_resize = sub.add_parser(
        "resize-storage",
        help="Resize the worker LXC's btrfs loopback file in place",
    )
    p_resize.add_argument(
        "--size", required=True,
        help="New size, e.g. 500G or 1T (units accepted by truncate(1))",
    )
    p_resize.set_defaults(func=_resize_storage)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    ns = parser.parse_args(argv)
    return ns.func(ns)


if __name__ == "__main__":
    sys.exit(main())

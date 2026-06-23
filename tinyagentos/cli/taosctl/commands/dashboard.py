"""taosctl dashboard -- read system, health, and cluster status from the shell.

Wraps the read endpoints in tinyagentos/routes/dashboard.py so agents and
scripts can check capabilities, health, version, system info, backends, the
cluster summary, recent activity, and metrics without the web UI. Follows the
reference shape in commands/agents.py.

Skipped: GET / (an HTML redirect, not data) and POST /setup/complete (a one-time
setup flow, not a shell verb).
"""
from __future__ import annotations

import argparse
from urllib.parse import quote

NOUN = "dashboard"


def _positive_int(value: str) -> int:
    iv = int(value)
    if iv <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return iv


def register(subparsers) -> None:
    p = subparsers.add_parser(NOUN, help="Read system, health, and cluster status")
    verbs = p.add_subparsers(dest="verb", required=True, metavar="<verb>")

    cp = verbs.add_parser("capabilities", help="Detected platform capabilities")
    cp.set_defaults(func=_capabilities)

    hp = verbs.add_parser("health", help="Service health")
    hp.add_argument("--detailed", action="store_true", help="Per-service detail")
    hp.set_defaults(func=_health)

    vp = verbs.add_parser("version", help="Controller version")
    vp.set_defaults(func=_version)

    sp = verbs.add_parser("system", help="System info (host, resources)")
    sp.set_defaults(func=_system)

    csp = verbs.add_parser("cluster-summary", help="Cluster summary")
    csp.set_defaults(func=_cluster_summary)

    bp = verbs.add_parser("backends", help="Configured backends")
    bp.set_defaults(func=_backends)

    ap = verbs.add_parser("activity", help="Recent dashboard activity")
    ap.add_argument("--limit", type=_positive_int, default=15)
    ap.set_defaults(func=_activity)

    mp = verbs.add_parser("metrics", help="Query a named time-series metric")
    mp.add_argument("name")
    mp.add_argument("--range", default="24h", help="Time range, e.g. 24h, 7d")
    mp.set_defaults(func=_metrics)


def _capabilities(args, client):
    return client.get("/api/capabilities")


def _health(args, client):
    params = {"detailed": True} if args.detailed else None
    return client.get("/api/health", params=params)


def _version(args, client):
    return client.get("/api/version")


def _system(args, client):
    return client.get("/api/system")


def _cluster_summary(args, client):
    return client.get("/api/dashboard/cluster-summary")


def _backends(args, client):
    return client.get("/api/backends")


def _activity(args, client):
    return client.get("/api/dashboard/activity", params={"limit": args.limit})


def _metrics(args, client):
    return client.get(
        f"/api/metrics/{quote(args.name, safe='')}", params={"range": args.range}
    )

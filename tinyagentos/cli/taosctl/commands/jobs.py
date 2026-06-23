"""taosctl jobs -- inspect and manage scheduled jobs.

Reference noun: mirrors the agents module pattern (NOUN, register(), small handlers).
"""
from __future__ import annotations

from urllib.parse import quote

from tinyagentos.cli.taosctl.argtypes import positive_int

NOUN = "jobs"


def register(subparsers) -> None:
    p = subparsers.add_parser(NOUN, help="Inspect and manage scheduled jobs")
    verbs = p.add_subparsers(dest="verb", required=True, metavar="<verb>")

    lp = verbs.add_parser("list", help="List recent jobs")
    lp.add_argument("--status", help="Filter by status", default=None)
    lp.add_argument("--limit", help="Max results", type=positive_int, default=50)
    lp.set_defaults(func=_list)

    gp = verbs.add_parser("get", help="Get one job by id")
    gp.add_argument("job_id", help="Job id")
    gp.set_defaults(func=_get)

    sp = verbs.add_parser("stats", help="Job queue statistics")
    sp.set_defaults(func=_stats)

    rp = verbs.add_parser("running", help="Currently running jobs")
    rp.set_defaults(func=_running)

    cp = verbs.add_parser("cancel", help="Cancel a pending job")
    cp.add_argument("job_id", help="Job id")
    cp.set_defaults(func=_cancel)

    clp = verbs.add_parser("cleanup", help="Remove old completed/failed jobs")
    clp.set_defaults(func=_cleanup)


def _list(args, client):
    params = {"status": args.status, "limit": args.limit}
    return client.get("/api/jobs", params=params)


def _get(args, client):
    return client.get(f"/api/jobs/{quote(args.job_id, safe='')}")


def _stats(args, client):
    return client.get("/api/jobs/stats")


def _running(args, client):
    return client.get("/api/jobs/running")


def _cancel(args, client):
    return client.post(f"/api/jobs/{quote(args.job_id, safe='')}/cancel")


def _cleanup(args, client):
    return client.post("/api/jobs/cleanup")

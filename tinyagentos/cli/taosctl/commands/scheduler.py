"""taosctl scheduler -- inspect scheduler state and backends.

All current scheduler endpoints are reads (observability only); there are no
create / update / delete surfaces, so no POST / PATCH / DELETE verbs yet.
Skipped (not simple JSON body endpoints):
  N/A -- the route file has no POST/PATCH/DELETE handlers at all.
"""
from __future__ import annotations

from tinyagentos.cli.taosctl.argtypes import positive_int

NOUN = "scheduler"


def register(subparsers) -> None:
    p = subparsers.add_parser(NOUN, help="Inspect scheduler state and backends")
    verbs = p.add_subparsers(dest="verb", required=True, metavar="<verb>")

    sp = verbs.add_parser("stats", help="Live scheduler stats")
    sp.set_defaults(func=_stats)

    tp = verbs.add_parser("tasks", help="Recent task history")
    tp.add_argument("--limit", type=positive_int, default=None, help="Max tasks (1-500)")
    tp.set_defaults(func=_tasks)

    bp = verbs.add_parser("backends", help="Live backend catalog")
    bp.set_defaults(func=_backends)


def _stats(args, client):
    return client.get("/api/scheduler/stats")


def _tasks(args, client):
    params = {}
    if args.limit is not None:
        params["limit"] = args.limit
    return client.get("/api/scheduler/tasks", params=params or None)


def _backends(args, client):
    return client.get("/api/scheduler/backends")

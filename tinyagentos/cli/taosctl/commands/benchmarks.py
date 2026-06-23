"""taosctl benchmarks -- read worker benchmark results and capability leaderboards.

Wraps the read endpoints in tinyagentos/routes/benchmarks.py so agents and
scripts can inspect benchmark data from the shell. Follows the reference shape
in commands/agents.py.

Skipped: POST /api/workers/{id}/benchmark/results takes a nested BenchmarkReport
(a worker_id plus a list of per-task BenchmarkResult objects). It is a
worker-to-controller submission with a complex nested body, not a shell verb, so
it is not wired here.
"""
from __future__ import annotations

from urllib.parse import quote

NOUN = "benchmarks"


def register(subparsers) -> None:
    p = subparsers.add_parser(NOUN, help="Read worker benchmarks and capability leaderboards")
    verbs = p.add_subparsers(dest="verb", required=True, metavar="<verb>")

    wp = verbs.add_parser("worker", help="Benchmark results for one worker")
    wp.add_argument("worker_id")
    wp.add_argument("--limit", type=int, default=100)
    wp.set_defaults(func=_worker)

    lp = verbs.add_parser("leaderboard", help="Capability leaderboard across workers")
    lp.add_argument("capability")
    lp.add_argument("--metric", default=None, help="Filter/sort by a specific metric")
    lp.set_defaults(func=_leaderboard)


def _worker(args, client):
    return client.get(
        f"/api/workers/{quote(args.worker_id, safe='')}/benchmark",
        params={"limit": args.limit},
    )


def _leaderboard(args, client):
    params = {}
    if args.metric is not None:
        params["metric"] = args.metric
    return client.get(
        f"/api/benchmarks/capability/{quote(args.capability, safe='')}",
        params=params or None,
    )

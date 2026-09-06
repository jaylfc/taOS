"""taosctl observatory -- watch the agent fleet and steer the work queue.

The Observatory backend exposes the fleet view plus the pause and concurrency
dials the dispatch loop polls each iteration. This group is the terminal/script
control surface for them, so steering the queue is a command rather than a hand
edit of a local dispatch script. Pause/throttle changes are admin-only server
side; reads require an admin session or an agent token holding
``observatory_control``.
"""
from __future__ import annotations

from tinyagentos.cli.taosctl.argtypes import positive_int

NOUN = "observatory"

_GLOBAL = "global"


def register(subparsers) -> None:
    p = subparsers.add_parser(NOUN, help="Watch the agent fleet and steer the queue")
    verbs = p.add_subparsers(dest="verb", required=True, metavar="<verb>")

    fp = verbs.add_parser("fleet", help="Fleet view: which agents are working and what they hold")
    fp.set_defaults(func=_fleet)

    psp = verbs.add_parser("pause-status", help="Show the current pause state")
    psp.set_defaults(func=_pause_status)

    pp = verbs.add_parser("pause", help="Pause the queue globally or for one lane")
    pp.add_argument("scope", nargs="?", default=_GLOBAL,
                    help="Lane handle, or 'global' (default) for the whole fleet")
    pp.set_defaults(func=_pause)

    rp = verbs.add_parser("resume", help="Resume the queue globally or for one lane")
    rp.add_argument("scope", nargs="?", default=_GLOBAL,
                    help="Lane handle, or 'global' (default)")
    rp.set_defaults(func=_resume)

    tsp = verbs.add_parser("throttle-status", help="Show the current concurrency caps")
    tsp.set_defaults(func=_throttle_status)

    tp = verbs.add_parser("throttle", help="Set or clear a concurrency cap")
    tp.add_argument("scope", nargs="?", default=_GLOBAL,
                    help="Lane handle, or 'global' (default)")
    cap = tp.add_mutually_exclusive_group(required=True)
    cap.add_argument("--max", dest="max_concurrent", type=positive_int,
                     help="Max cards a lane may hold in flight at once")
    cap.add_argument("--clear", action="store_true",
                     help="Clear the cap (fall back to the loop default)")
    tp.set_defaults(func=_throttle)


def _fleet(args, client):
    return client.get("/api/observatory/fleet")


def _pause_status(args, client):
    return client.get("/api/observatory/pause")


def _pause(args, client):
    return client.post("/api/observatory/pause", body={"scope": args.scope, "paused": True})


def _resume(args, client):
    return client.post("/api/observatory/pause", body={"scope": args.scope, "paused": False})


def _throttle_status(args, client):
    return client.get("/api/observatory/throttle")


def _throttle(args, client):
    # --clear and --max are mutually exclusive and one is required; a clear sends
    # an explicit null so the server drops the override.
    limit = None if args.clear else args.max_concurrent
    return client.post(
        "/api/observatory/throttle",
        body={"scope": args.scope, "max_concurrent": limit},
    )

"""taosctl music -- inspect and control music generation."""
from __future__ import annotations

NOUN = "music"


def register(subparsers) -> None:
    p = subparsers.add_parser(NOUN, help="Inspect and control music generation")
    verbs = p.add_subparsers(dest="verb", required=True, metavar="<verb>")

    lp = verbs.add_parser("list", help="List generated tracks")
    lp.set_defaults(func=_list)

    sp = verbs.add_parser("status", help="Show music backend status")
    sp.set_defaults(func=_status)

    # POST /api/music/compose skipped: requires a complex body (prompt, duration).


def _list(args, client):
    return client.get("/api/music")


def _status(args, client):
    return client.get("/api/music/status")

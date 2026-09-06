"""taosctl browsing_history -- inspect and manage browsing history."""

from __future__ import annotations

from tinyagentos.cli.taosctl.argtypes import positive_int

NOUN = "browsing_history"


def register(subparsers) -> None:
    p = subparsers.add_parser(NOUN, help="Inspect and manage browsing history")
    verbs = p.add_subparsers(dest="verb", required=True, metavar="<verb>")

    lp = verbs.add_parser("list", help="List browsing history")
    lp.add_argument("--source-type", dest="source_type", default=None, help="Filter by source type")
    lp.add_argument("--limit", type=positive_int, default=None, help="Max items to return")
    lp.set_defaults(func=_list)

    cp = verbs.add_parser("clear", help="Clear browsing history")
    cp.add_argument("--source-type", dest="source_type", default=None, help="Clear only a specific source type")
    cp.set_defaults(func=_clear)


def _list(args, client):
    params = {}
    if args.source_type is not None:
        params["source_type"] = args.source_type
    if args.limit is not None:
        params["limit"] = args.limit
    return client.get("/api/browsing-history", params=params or None)


def _clear(args, client):
    params = {}
    if args.source_type is not None:
        params["source_type"] = args.source_type
    return client.delete("/api/browsing-history", params=params or None)

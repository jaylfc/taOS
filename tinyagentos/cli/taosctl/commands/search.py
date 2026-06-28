"""taosctl search -- query the global search index."""
from __future__ import annotations

from tinyagentos.cli.taosctl.argtypes import positive_int

NOUN = "search"


def register(subparsers) -> None:
    p = subparsers.add_parser(NOUN, help="Query the global search index")
    verbs = p.add_subparsers(dest="verb", required=True, metavar="<verb>")

    lp = verbs.add_parser("list", help="Search across all platform data")
    lp.add_argument("q", help="Search query string")
    lp.add_argument("--limit", type=positive_int, default=5, help="Max results (default: 5)")
    lp.set_defaults(func=_list)


def _list(args, client):
    return client.get("/api/search", params={"q": args.q, "limit": args.limit})

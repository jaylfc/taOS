"""taosctl templates -- inspect and manage agent templates."""
from __future__ import annotations

from urllib.parse import quote

NOUN = "templates"


def register(subparsers) -> None:
    p = subparsers.add_parser(NOUN, help="Inspect and manage agent templates")
    verbs = p.add_subparsers(dest="verb", required=True, metavar="<verb>")

    lp = verbs.add_parser("list", help="List agent templates")
    lp.add_argument("--category", help="Filter by category")
    lp.add_argument("--source", help="Filter by source")
    lp.add_argument("--limit", type=int, default=50, help="Page size (default 50)")
    lp.add_argument("--offset", type=int, default=0, help="Page offset (default 0)")
    lp.set_defaults(func=_list)

    gp = verbs.add_parser("get", help="Get one template by ID")
    gp.add_argument("template_id", help="Template ID")
    gp.set_defaults(func=_get)

    sp = verbs.add_parser("stats", help="Show template count stats")
    sp.set_defaults(func=_stats)

    sop = verbs.add_parser("sources", help="List external template sources")
    sop.set_defaults(func=_sources)

    # SKIP: external source listing/fetching endpoints need async HTTP client
    # access via request.app.state.http_client and are not suitable for a simple
    # CLI wrapper. Skipped: /api/templates/external/{source_id},
    # /api/templates/external/{source_id}/fetch, /api/personas/library.


def _list(args, client):
    params = {}
    if args.category:
        params["category"] = args.category
    if args.source:
        params["source"] = args.source
    params["limit"] = args.limit
    params["offset"] = args.offset
    return client.get("/api/templates", params=params)


def _get(args, client):
    return client.get(f"/api/templates/{quote(args.template_id, safe='')}")


def _stats(args, client):
    return client.get("/api/templates/stats")


def _sources(args, client):
    return client.get("/api/templates/sources")

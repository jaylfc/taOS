"""taosctl catalog -- drive the session memory catalog from the shell.

Wraps the catalog endpoints in tinyagentos/routes/catalog.py (all under
/api/memory/catalog) so agents and scripts can look up indexed sessions, search
topics, and trigger (re)indexing without the web UI. Follows the reference shape
in commands/agents.py: a NOUN, a register() that wires verb subparsers, and
small handlers that call the client and return data for the framework to render.

Every endpoint takes query params or a small JSON body, so all are wired; there
are no multipart/streaming endpoints to skip.
"""
from __future__ import annotations

from urllib.parse import quote

NOUN = "catalog"


def register(subparsers) -> None:
    p = subparsers.add_parser(NOUN, help="Inspect and (re)index the session memory catalog")
    verbs = p.add_subparsers(dest="verb", required=True, metavar="<verb>")

    sp = verbs.add_parser("stats", help="Catalog statistics")
    sp.set_defaults(func=_stats)

    dp = verbs.add_parser("date", help="Sessions for a date (YYYY-MM-DD)")
    dp.add_argument("date")
    dp.set_defaults(func=_date)

    rp = verbs.add_parser("range", help="Sessions in a date range")
    rp.add_argument("start", help="Start date (YYYY-MM-DD)")
    rp.add_argument("end", help="End date (YYYY-MM-DD)")
    rp.set_defaults(func=_range)

    qp = verbs.add_parser("search", help="Search sessions by topic")
    qp.add_argument("query")
    qp.add_argument("--limit", type=int, default=20)
    qp.set_defaults(func=_search)

    ses = verbs.add_parser("session", help="Get one indexed session by id")
    ses.add_argument("session_id")
    ses.set_defaults(func=_session)

    ctx = verbs.add_parser("context", help="Get the full context for a session")
    ctx.add_argument("session_id")
    ctx.set_defaults(func=_context)

    rc = verbs.add_parser("recent", help="Recently indexed sessions")
    rc.add_argument("--limit", type=int, default=20)
    rc.set_defaults(func=_recent)

    ip = verbs.add_parser("index", help="Index sessions (a date or a date range)")
    ip.add_argument("--date", default=None, help="Single date YYYY-MM-DD")
    ip.add_argument("--start-date", default=None)
    ip.add_argument("--end-date", default=None)
    ip.add_argument("--force", action="store_true", help="Re-index already-indexed sessions")
    ip.set_defaults(func=_index)

    rb = verbs.add_parser("rebuild", help="Rebuild the whole catalog from the archive")
    rb.set_defaults(func=_rebuild)


def _stats(args, client):
    return client.get("/api/memory/catalog/stats")


def _date(args, client):
    return client.get(f"/api/memory/catalog/date/{quote(args.date, safe='')}")


def _range(args, client):
    return client.get("/api/memory/catalog/range", params={"start": args.start, "end": args.end})


def _search(args, client):
    return client.get("/api/memory/catalog/search", params={"q": args.query, "limit": args.limit})


def _session(args, client):
    return client.get(f"/api/memory/catalog/session/{quote(args.session_id, safe='')}")


def _context(args, client):
    return client.get(f"/api/memory/catalog/session/{quote(args.session_id, safe='')}/context")


def _recent(args, client):
    return client.get("/api/memory/catalog/recent", params={"limit": args.limit})


def _index(args, client):
    return client.post(
        "/api/memory/catalog/index",
        {
            "date": args.date,
            "start_date": args.start_date,
            "end_date": args.end_date,
            "force": args.force,
        },
    )


def _rebuild(args, client):
    return client.post("/api/memory/catalog/rebuild", {})

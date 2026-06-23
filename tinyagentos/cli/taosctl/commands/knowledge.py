"""taosctl knowledge -- inspect and manage the knowledge base from the shell.

Wraps tinyagentos/routes/knowledge.py so agents and scripts can drive ingest,
search, items, category rules, and subscriptions without the web UI. Follows the
reference shape in commands/agents.py: a NOUN, a register() that wires verb
subparsers, and small handlers that call the client and return data for the
framework to render.

All endpoints on the knowledge router take query params or small JSON bodies, so
every one is wired here; there are no multipart/streaming endpoints to skip.
"""
from __future__ import annotations

from urllib.parse import quote

NOUN = "knowledge"


def register(subparsers) -> None:
    p = subparsers.add_parser(NOUN, help="Inspect and manage the knowledge base")
    verbs = p.add_subparsers(dest="verb", required=True, metavar="<verb>")

    lp = verbs.add_parser("list", help="List knowledge items")
    lp.set_defaults(func=_list)

    gp = verbs.add_parser("get", help="Get one knowledge item by id")
    gp.add_argument("item_id", help="Knowledge item id")
    gp.set_defaults(func=_get)

    sp = verbs.add_parser("snapshots", help="List snapshots for an item")
    sp.add_argument("item_id", help="Knowledge item id")
    sp.set_defaults(func=_snapshots)

    dp = verbs.add_parser("delete", help="Delete a knowledge item")
    dp.add_argument("item_id", help="Knowledge item id")
    dp.set_defaults(func=_delete)

    qp = verbs.add_parser("search", help="Search the knowledge base")
    qp.add_argument("query", help="Search query")
    qp.add_argument("--mode", default="keyword", help="keyword (default) or semantic")
    qp.add_argument("--limit", type=int, default=20)
    qp.set_defaults(func=_search)

    ip = verbs.add_parser("ingest", help="Ingest a URL into the knowledge base")
    ip.add_argument("--url", required=True, help="Source URL (required by the server)")
    ip.add_argument("--title", default=None)
    ip.add_argument("--text", default=None)
    ip.add_argument("--category", action="append", dest="categories", default=None,
                    help="category to tag (repeatable)")
    ip.add_argument("--source", default=None)
    ip.set_defaults(func=_ingest)

    rp = verbs.add_parser("rules", help="List category rules")
    rp.set_defaults(func=_rules)

    arp = verbs.add_parser("add-rule", help="Create a category rule")
    arp.add_argument("--pattern", required=True)
    arp.add_argument("--match-on", default="title")
    arp.add_argument("--category", required=True)
    arp.set_defaults(func=_add_rule)

    drp = verbs.add_parser("delete-rule", help="Delete a category rule")
    drp.add_argument("rule_id", type=int, help="Rule id (integer)")
    drp.set_defaults(func=_delete_rule)

    subp = verbs.add_parser("subscriptions", help="List agent category subscriptions")
    subp.set_defaults(func=_subscriptions)

    subset = verbs.add_parser("subscribe", help="Upsert an agent subscription for a category")
    subset.add_argument("--agent", required=True)
    subset.add_argument("--category", required=True)
    subset.add_argument("--auto-ingest", action="store_true")
    subset.set_defaults(func=_subscribe)

    unsub = verbs.add_parser("unsubscribe", help="Remove an agent subscription")
    unsub.add_argument("agent_name", help="Agent name")
    unsub.add_argument("category", help="Category")
    unsub.set_defaults(func=_unsubscribe)


def _list(args, client):
    return client.get("/api/knowledge/items")


def _get(args, client):
    return client.get(f"/api/knowledge/items/{quote(args.item_id, safe='')}")


def _snapshots(args, client):
    return client.get(f"/api/knowledge/items/{quote(args.item_id, safe='')}/snapshots")


def _delete(args, client):
    return client.delete(f"/api/knowledge/items/{quote(args.item_id, safe='')}")


def _search(args, client):
    return client.get(
        "/api/knowledge/search",
        params={"q": args.query, "mode": args.mode, "limit": args.limit},
    )


def _ingest(args, client):
    # Send only the fields the caller supplied. The server's IngestRequest types
    # title/text/source as str (not optional), so sending None for an unset field
    # fails validation; omitting it lets the server apply its default.
    body = {"url": args.url}
    if args.title is not None:
        body["title"] = args.title
    if args.text is not None:
        body["text"] = args.text
    if args.categories:
        body["categories"] = args.categories
    if args.source is not None:
        body["source"] = args.source
    return client.post("/api/knowledge/ingest", body)


def _rules(args, client):
    return client.get("/api/knowledge/rules")


def _add_rule(args, client):
    return client.post(
        "/api/knowledge/rules",
        {"pattern": args.pattern, "match_on": args.match_on, "category": args.category},
    )


def _delete_rule(args, client):
    return client.delete(f"/api/knowledge/rules/{args.rule_id}")


def _subscriptions(args, client):
    return client.get("/api/knowledge/subscriptions")


def _subscribe(args, client):
    return client.post(
        "/api/knowledge/subscriptions",
        {"agent_name": args.agent, "category": args.category, "auto_ingest": args.auto_ingest},
    )


def _unsubscribe(args, client):
    return client.delete(
        f"/api/knowledge/subscriptions/{quote(args.agent_name, safe='')}/{quote(args.category, safe='')}"
    )

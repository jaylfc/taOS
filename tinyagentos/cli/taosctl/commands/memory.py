"""taosctl memory -- inspect and manage agent memory.

Maps the memory REST endpoints to verbs:
  list         -> GET  /api/memory/browse
  search       -> POST /api/memory/search  (keyword or semantic)
  collections  -> GET  /api/memory/collections/{agent}
  delete       -> DELETE /api/memory/chunk/{hash}
"""
from __future__ import annotations

from urllib.parse import quote

from tinyagentos.cli.taosctl.argtypes import positive_int, nonneg_int

NOUN = "memory"


def register(subparsers) -> None:
    p = subparsers.add_parser(NOUN, help="Inspect and manage agent memory")
    verbs = p.add_subparsers(dest="verb", required=True, metavar="<verb>")

    lp = verbs.add_parser("list", help="List memory chunks (browse)")
    lp.add_argument("--agent", help="Agent name (omit for user scope)")
    lp.add_argument("--collection", help="Collection filter")
    lp.add_argument("--limit", type=positive_int, default=20, help="Max results")
    lp.add_argument("--offset", type=nonneg_int, default=0, help="Offset")
    lp.set_defaults(func=_list)

    sp = verbs.add_parser("search", help="Search memory (keyword or semantic)")
    sp.add_argument("query", help="Search query")
    sp.add_argument("--agent", help="Agent name (omit for user scope)")
    sp.add_argument("--collection", help="Collection filter")
    sp.add_argument("--limit", type=positive_int, default=20, help="Max results")
    sp.add_argument("--mode", choices=["keyword", "semantic"], default="keyword",
                    help="Search mode (default: keyword)")
    sp.add_argument("--conversation-id", help="W3C trace conversation id")
    sp.set_defaults(func=_search)

    cp = verbs.add_parser("collections", help="List collections for an agent")
    cp.add_argument("agent_name", help="Agent name")
    cp.add_argument("--conversation-id", help="W3C trace conversation id")
    cp.set_defaults(func=_collections)

    dp = verbs.add_parser("delete", help="Delete a memory chunk by hash")
    dp.add_argument("content_hash", help="Chunk content hash")
    dp.add_argument("--agent", help="Agent name (omit for user scope)")
    dp.add_argument("--conversation-id", help="W3C trace conversation id")
    dp.set_defaults(func=_delete)


def _list(args, client):
    params: dict = {"limit": args.limit, "offset": args.offset}
    if args.agent:
        params["agent"] = args.agent
    if args.collection:
        params["collection"] = args.collection
    return client.get("/api/memory/browse", params=params)


def _search(args, client):
    payload: dict = {
        "query": args.query,
        "mode": args.mode,
        "limit": args.limit,
    }
    if args.agent:
        payload["agent"] = args.agent
    if args.collection:
        payload["collection"] = args.collection
    if args.conversation_id:
        payload["conversation_id"] = args.conversation_id
    return client.post("/api/memory/search", json=payload)


def _collections(args, client):
    params: dict = {}
    if args.conversation_id:
        params["conversation_id"] = args.conversation_id
    return client.get(
        f"/api/memory/collections/{quote(args.agent_name, safe='')}",
        params=params,
    )


def _delete(args, client):
    params: dict = {}
    if args.agent:
        params["agent"] = args.agent
    if args.conversation_id:
        params["conversation_id"] = args.conversation_id
    return client.delete(
        f"/api/memory/chunk/{quote(args.content_hash, safe='')}",
        params=params,
    )

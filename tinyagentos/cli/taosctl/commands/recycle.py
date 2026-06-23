"""taosctl recycle -- inspect and restore agent recycle bins from the shell.

Wraps tinyagentos/routes/recycle.py so agents and scripts can list deleted
items, restore them, and purge them without the web UI. Follows the reference
shape in commands/agents.py. All endpoints take a path id or a small JSON body;
none are multipart/streaming.
"""
from __future__ import annotations

from urllib.parse import quote

NOUN = "recycle"


def register(subparsers) -> None:
    p = subparsers.add_parser(NOUN, help="Inspect and restore agent recycle bins")
    verbs = p.add_subparsers(dest="verb", required=True, metavar="<verb>")

    lp = verbs.add_parser("list", help="List recycle items for one agent")
    lp.add_argument("name", help="Agent name")
    lp.set_defaults(func=_list)

    ap = verbs.add_parser("list-all", help="List recycle items across all agents")
    ap.set_defaults(func=_list_all)

    rp = verbs.add_parser("restore", help="Restore a recycle item")
    rp.add_argument("name", help="Agent name")
    # Exactly one target is required: an empty restore body 400s server-side, so
    # enforce the choice at the CLI instead of letting the request fail.
    target = rp.add_mutually_exclusive_group(required=True)
    target.add_argument("--id", dest="item_id", default=None, help="Encoded item id")
    target.add_argument("--original-path", default=None, help="Original path to restore")
    rp.set_defaults(func=_restore)

    pp = verbs.add_parser("purge", help="Permanently delete a recycle item")
    pp.add_argument("name", help="Agent name")
    pp.add_argument("item_id", help="Item id")
    pp.set_defaults(func=_purge)


def _list(args, client):
    return client.get(f"/api/agents/{quote(args.name, safe='')}/recycle")


def _list_all(args, client):
    return client.get("/api/recycle")


def _restore(args, client):
    body = {}
    if args.item_id is not None:
        body["id"] = args.item_id
    if args.original_path is not None:
        body["original_path"] = args.original_path
    return client.post(f"/api/agents/{quote(args.name, safe='')}/recycle/restore", body)


def _purge(args, client):
    return client.delete(
        f"/api/agents/{quote(args.name, safe='')}/recycle/{quote(args.item_id, safe='')}"
    )

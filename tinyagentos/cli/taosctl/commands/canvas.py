"""taosctl canvas -- inspect and manage canvas pages."""
from __future__ import annotations

from urllib.parse import quote

NOUN = "canvas"


def register(subparsers) -> None:
    p = subparsers.add_parser(NOUN, help="Inspect and manage canvas pages")
    verbs = p.add_subparsers(dest="verb", required=True, metavar="<verb>")

    lp = verbs.add_parser("list", help="List all canvases")
    lp.set_defaults(func=_list)

    gp = verbs.add_parser("get", help="Get one canvas by id")
    gp.add_argument("id", help="Canvas id")
    gp.set_defaults(func=_get)

    dp = verbs.add_parser("delete", help="Delete a canvas by id")
    dp.add_argument("id", help="Canvas id")
    dp.set_defaults(func=_delete)


def _list(args, client):
    return client.get("/api/canvas")


def _get(args, client):
    # The single-canvas read route is /api/canvas/{id}/data; the bare
    # /api/canvas/{id} path only has a DELETE handler.
    return client.get(f"/api/canvas/{quote(args.id, safe='')}/data")


def _delete(args, client):
    return client.delete(f"/api/canvas/{quote(args.id, safe='')}")

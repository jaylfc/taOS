"""taosctl themes -- inspect and manage themes."""
from __future__ import annotations

from urllib.parse import quote

NOUN = "themes"


def register(subparsers) -> None:
    p = subparsers.add_parser(NOUN, help="Inspect and manage themes")
    verbs = p.add_subparsers(dest="verb", required=True, metavar="<verb>")

    lp = verbs.add_parser("list", help="List installed themes")
    lp.set_defaults(func=_list)

    # POST /api/themes/install skipped: requires file upload / multipart
    # GET /api/themes/{theme_id}/assets/{path} skipped: file download / streaming

    dp = verbs.add_parser("delete", help="Delete a theme")
    dp.add_argument("theme_id", help="Theme id")
    dp.set_defaults(func=_delete)


def _list(args, client):
    return client.get("/api/themes")


def _delete(args, client):
    return client.delete(f"/api/themes/{quote(args.theme_id, safe='')}")

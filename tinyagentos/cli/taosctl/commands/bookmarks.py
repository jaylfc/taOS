"""taosctl bookmarks -- manage browser bookmarks.

Reference noun: mirrors the agents pattern (NOUN, register(), small handlers).
"""
from __future__ import annotations

from urllib.parse import quote

NOUN = "bookmarks"


def register(subparsers) -> None:
    p = subparsers.add_parser(NOUN, help="Manage browser bookmarks")
    verbs = p.add_subparsers(dest="verb", required=True, metavar="<verb>")

    lp = verbs.add_parser("list", help="List bookmarks for a profile")
    lp.add_argument("--profile-id", required=True, help="Browser profile ID")
    lp.set_defaults(func=_list)

    cp = verbs.add_parser("create", help="Add a new bookmark")
    cp.add_argument("--profile-id", required=True, help="Browser profile ID")
    cp.add_argument("--url", required=True, help="Bookmark URL")
    cp.add_argument("--title", required=True, help="Bookmark title")
    cp.set_defaults(func=_create)

    dp = verbs.add_parser("delete", help="Remove a bookmark by ID")
    dp.add_argument("bookmark_id", help="Bookmark ID")
    dp.add_argument("--profile-id", required=True, help="Browser profile ID")
    dp.set_defaults(func=_delete)


def _list(args, client):
    return client.get(
        "/api/desktop/browser/bookmarks",
        params={"profile_id": args.profile_id},
    )


def _create(args, client):
    return client.post(
        "/api/desktop/browser/bookmarks",
        body={
            "profile_id": args.profile_id,
            "url": args.url,
            "title": args.title,
        },
    )


def _delete(args, client):
    path = f"/api/desktop/browser/bookmarks/{quote(args.bookmark_id, safe='')}"
    return client.delete(path, params={"profile_id": args.profile_id})

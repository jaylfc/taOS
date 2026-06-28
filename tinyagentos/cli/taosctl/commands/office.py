"""taosctl office -- manage Office Suite documents from the shell.

Wraps tinyagentos/routes/office.py (CRUD on /api/office/docs) so agents and
scripts can create, list, read, update, and delete documents (write, calc, db,
slides) without the web UI. Follows the reference shape in commands/agents.py.
All endpoints take a small JSON body or a path id; none are multipart/streaming.
"""
from __future__ import annotations

from urllib.parse import quote

NOUN = "office"

KINDS = ("write", "calc", "db", "slides")


def register(subparsers) -> None:
    p = subparsers.add_parser(NOUN, help="Manage Office Suite documents")
    verbs = p.add_subparsers(dest="verb", required=True, metavar="<verb>")

    cp = verbs.add_parser("create", help="Create a document")
    cp.add_argument("--kind", required=True, choices=KINDS)
    cp.add_argument("--title", required=True)
    cp.add_argument("--content", default="")
    cp.set_defaults(func=_create)

    lp = verbs.add_parser("list", help="List documents")
    lp.set_defaults(func=_list)

    gp = verbs.add_parser("get", help="Get one document by id")
    gp.add_argument("doc_id")
    gp.set_defaults(func=_get)

    up = verbs.add_parser("update", help="Update a document (only the fields you pass)")
    up.add_argument("doc_id")
    up.add_argument("--kind", choices=KINDS, default=None)
    up.add_argument("--title", default=None)
    up.add_argument("--content", default=None)
    up.set_defaults(func=_update)

    dp = verbs.add_parser("delete", help="Delete a document")
    dp.add_argument("doc_id")
    dp.set_defaults(func=_delete)


def _create(args, client):
    return client.post(
        "/api/office/docs",
        {"kind": args.kind, "title": args.title, "content": args.content},
    )


def _list(args, client):
    return client.get("/api/office/docs")


def _get(args, client):
    return client.get(f"/api/office/docs/{quote(args.doc_id, safe='')}")


def _update(args, client):
    # Partial update: send only the fields the caller actually supplied so the
    # route keeps the existing value for the rest.
    body = {}
    if args.kind is not None:
        body["kind"] = args.kind
    if args.title is not None:
        body["title"] = args.title
    if args.content is not None:
        body["content"] = args.content
    return client.put(f"/api/office/docs/{quote(args.doc_id, safe='')}", body)


def _delete(args, client):
    return client.delete(f"/api/office/docs/{quote(args.doc_id, safe='')}")

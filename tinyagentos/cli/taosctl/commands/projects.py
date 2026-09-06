"""taosctl projects -- inspect and manage projects."""
from __future__ import annotations

NOUN = "projects"


def register(subparsers) -> None:
    p = subparsers.add_parser(NOUN, help="Inspect and manage projects")
    verbs = p.add_subparsers(dest="verb", required=True, metavar="<verb>")

    lp = verbs.add_parser("list", help="List projects")
    lp.set_defaults(func=_list)

    gp = verbs.add_parser("get", help="Get one project by id")
    gp.add_argument("id", help="Project id")
    gp.set_defaults(func=_get)

    cp = verbs.add_parser("create", help="Create a project")
    cp.add_argument("name", help="Project name")
    cp.add_argument("slug", help="URL-friendly slug")
    cp.add_argument("--description", default="", help="Short description")
    cp.set_defaults(func=_create)

    up = verbs.add_parser("update", help="Update a project")
    up.add_argument("id", help="Project id")
    up.add_argument("--name", default=None, help="New name")
    up.add_argument("--description", default=None, help="New description")
    up.set_defaults(func=_update)

    dp = verbs.add_parser("delete", help="Delete a project")
    dp.add_argument("id", help="Project id")
    dp.set_defaults(func=_delete)

    ap = verbs.add_parser("archive", help="Archive a project")
    ap.add_argument("id", help="Project id")
    ap.set_defaults(func=_archive)


def _list(args, client):
    return client.get("/api/projects")


def _get(args, client):
    return client.get(f"/api/projects/{args.id}")


def _create(args, client):
    body = {"name": args.name, "slug": args.slug, "description": args.description}
    return client.post("/api/projects", body=body)


def _update(args, client):
    body = {}
    if args.name is not None:
        body["name"] = args.name
    if args.description is not None:
        body["description"] = args.description
    return client.patch(f"/api/projects/{args.id}", body=body)


def _delete(args, client):
    return client.delete(f"/api/projects/{args.id}")


def _archive(args, client):
    return client.post(f"/api/projects/{args.id}/archive")

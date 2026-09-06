"""taosctl shared_folders -- inspect and manage shared folders."""
from __future__ import annotations

from urllib.parse import quote

NOUN = "shared_folders"


def register(subparsers) -> None:
    p = subparsers.add_parser(NOUN, help="Inspect and manage shared folders")
    verbs = p.add_subparsers(dest="verb", required=True, metavar="<verb>")

    lp = verbs.add_parser("list", help="List shared folders")
    lp.add_argument("--agent-name", default=None, help="Filter by agent name")
    lp.set_defaults(func=_list)

    gp = verbs.add_parser("get", help="Get one shared folder by id")
    gp.add_argument("id", help="Folder id")
    gp.set_defaults(func=_get)

    cp = verbs.add_parser("create", help="Create a shared folder")
    cp.add_argument("name", help="Folder name")
    cp.add_argument("--description", default="", help="Short description")
    cp.add_argument("--agents", default=None, help="Comma-separated agent names")
    cp.set_defaults(func=_create)

    dp = verbs.add_parser("delete", help="Delete a shared folder")
    dp.add_argument("id", help="Folder id")
    dp.set_defaults(func=_delete)

    fp = verbs.add_parser("files", help="List files in a shared folder")
    fp.add_argument("name", help="Folder name")
    fp.set_defaults(func=_files)

    # POST /api/shared-folders/{name}/upload -- skipped (file upload / multipart)

    ap = verbs.add_parser("grant", help="Grant access to a shared folder")
    ap.add_argument("folder_id", help="Folder id")
    ap.add_argument("agent_name", help="Agent name")
    ap.add_argument("--permission", default="readwrite", help="Permission level")
    ap.set_defaults(func=_grant)


def _list(args, client):
    params = {}
    if args.agent_name:
        params["agent_name"] = args.agent_name
    return client.get("/api/shared-folders", params=params or None)


def _get(args, client):
    # No single-folder GET route exists; fetch the list and filter by id.
    rows = client.get("/api/shared-folders")
    for row in rows or []:
        if str(row.get("id")) == str(args.id):
            return row
    raise SystemExit(f"no shared folder with id: {args.id}")


def _create(args, client):
    body = {"name": args.name, "description": args.description}
    if args.agents:
        body["agents"] = [a.strip() for a in args.agents.split(",")]
    return client.post("/api/shared-folders", body=body)


def _delete(args, client):
    return client.delete(f"/api/shared-folders/{quote(args.id, safe='')}")


def _files(args, client):
    return client.get(f"/api/shared-folders/{quote(args.name, safe='')}/files")


def _grant(args, client):
    body = {"agent_name": args.agent_name, "permission": args.permission}
    return client.post(f"/api/shared-folders/{quote(args.folder_id, safe='')}/access", body=body)

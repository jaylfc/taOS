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

    cop = verbs.add_parser("canvas-original", help="Fetch original payload for one canvas element")
    cop.add_argument("project_id", help="Project id")
    cop.add_argument("element_id", help="Canvas element id")
    cop.set_defaults(func=_canvas_original)

    clp = verbs.add_parser("canvas-legacy", help="List legacy canvas elements with tldraw_shape")
    clp.add_argument("project_id", help="Project id")
    clp.add_argument("--include-deleted", action="store_true", default=False, help="Include soft-deleted rows")
    clp.set_defaults(func=_canvas_legacy)

    cetp = verbs.add_parser("canvas-export-tldr", help="Export project canvas as a .tldr file")
    cetp.add_argument("project_id", help="Project id")
    cetp.add_argument("-o", "--output", default=None, help="Output file path (default: stdout)")
    cetp.set_defaults(func=_canvas_export_tldr)


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


def _canvas_original(args, client):
    return client.get(f"/api/projects/{args.project_id}/canvas/elements/{args.element_id}/original")


def _canvas_legacy(args, client):
    params = {"include_deleted": "1" if args.include_deleted else "0"}
    return client.get(f"/api/projects/{args.project_id}/canvas/legacy", params=params)


def _canvas_export_tldr(args, client):
    resp = client.get(f"/api/projects/{args.project_id}/canvas/snapshot.tldr")
    if args.output:
        import json
        from pathlib import Path
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(resp, (dict, list)):
            out.write_text(json.dumps(resp, separators=(",", ":")))
        elif isinstance(resp, str):
            out.write_text(resp)
        else:
            out.write_bytes(resp if isinstance(resp, bytes) else str(resp).encode())
        return {"file_path": str(out)}
    return resp

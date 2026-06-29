"""taosctl agents -- inspect and manage agents.

Reference noun: shows the pattern every other noun module follows (a NOUN, a
register() that wires verb subparsers, and small handlers that call the client
and return data for the framework to render).
"""
from __future__ import annotations

from urllib.parse import quote

NOUN = "agents"


def register(subparsers) -> None:
    p = subparsers.add_parser(NOUN, help="Inspect and manage agents")
    verbs = p.add_subparsers(dest="verb", required=True, metavar="<verb>")

    lp = verbs.add_parser("list", help="List all agents")
    lp.set_defaults(func=_list)

    gp = verbs.add_parser("get", help="Get one agent by name")
    gp.add_argument("name", help="Agent name")
    gp.set_defaults(func=_get)

    ap = verbs.add_parser("archived", help="List archived agents")
    ap.set_defaults(func=_archived)

    mp = verbs.add_parser(
        "mint",
        help="Mint (or reuse) an internal agent identity + registry token (admin)",
    )
    mp.add_argument("--handle", required=True, help="Agent handle, e.g. @taOS-dev")
    mp.add_argument("--slug", required=True, help="Canonical-id slug, e.g. taos-dev")
    mp.add_argument(
        "--scopes",
        default="a2a_send,a2a_receive",
        help="Comma-separated scopes to grant (default a2a_send,a2a_receive)",
    )
    mp.add_argument("--project", dest="project_id", default=None,
                    help="Optional project id to bind the token/grants to")
    mp.set_defaults(func=_mint)

    sp = verbs.add_parser(
        "seed-internal",
        help="Idempotently mint the four internal driver agents + tokens (admin)",
    )
    sp.set_defaults(func=_seed_internal)


def _list(args, client):
    return client.get("/api/agents")


def _get(args, client):
    return client.get(f"/api/agents/{quote(args.name, safe='')}")


def _archived(args, client):
    return client.get("/api/agents/archived")


def _mint(args, client):
    scopes = [s.strip() for s in args.scopes.split(",") if s.strip()]
    body = {"handle": args.handle, "slug": args.slug, "scopes": scopes}
    if args.project_id:
        body["project_id"] = args.project_id
    return client.post("/api/agents/registry/mint-internal", body=body)


def _seed_internal(args, client):
    return client.post("/api/agents/registry/seed-internal")

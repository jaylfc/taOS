"""taosctl secrets -- inspect and manage secrets."""
from __future__ import annotations

from urllib.parse import quote

NOUN = "secrets"


def register(subparsers) -> None:
    p = subparsers.add_parser(NOUN, help="Inspect and manage secrets")
    verbs = p.add_subparsers(dest="verb", required=True, metavar="<verb>")

    lp = verbs.add_parser("list", help="List all secrets")
    lp.add_argument("--category", help="Filter by category", default=None)
    lp.set_defaults(func=_list)

    gp = verbs.add_parser("get", help="Get one secret by name")
    gp.add_argument("name", help="Secret name")
    gp.set_defaults(func=_get)

    cp = verbs.add_parser("create", help="Create a secret")
    cp.add_argument("name", help="Secret name")
    cp.add_argument("value", help="Secret value")
    cp.add_argument("--category", default="general", help="Category (default: general)")
    cp.add_argument("--description", default="", help="Description")
    cp.set_defaults(func=_create)

    up = verbs.add_parser("update", help="Update a secret")
    up.add_argument("name", help="Secret name")
    up.add_argument("--value", default=None, help="New value")
    up.add_argument("--category", default=None, help="New category")
    up.add_argument("--description", default=None, help="New description")
    up.set_defaults(func=_update)

    dp = verbs.add_parser("delete", help="Delete a secret")
    dp.add_argument("name", help="Secret name")
    dp.set_defaults(func=_delete)

    # Skipped: GET /api/secrets/categories (no simple verb mapping)
    # Skipped: GET /api/secrets/agent/{agent_name} (agent-scoped listing)


def _list(args, client):
    params = {}
    if args.category:
        params["category"] = args.category
    return client.get("/api/secrets", params=params or None)


def _get(args, client):
    return client.get(f"/api/secrets/{quote(args.name, safe='')}")


def _create(args, client):
    body = {
        "name": args.name,
        "value": args.value,
        "category": args.category,
        "description": args.description,
    }
    return client.post("/api/secrets", json=body)


def _update(args, client):
    body = {}
    if args.value is not None:
        body["value"] = args.value
    if args.category is not None:
        body["category"] = args.category
    if args.description is not None:
        body["description"] = args.description
    return client.put(f"/api/secrets/{quote(args.name, safe='')}", json=body)


def _delete(args, client):
    return client.delete(f"/api/secrets/{quote(args.name, safe='')}")

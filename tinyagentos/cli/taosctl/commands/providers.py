"""taosctl providers -- inspect and manage providers."""
from __future__ import annotations

from urllib.parse import quote

NOUN = "providers"


def register(subparsers) -> None:
    p = subparsers.add_parser(NOUN, help="Inspect and manage providers")
    verbs = p.add_subparsers(dest="verb", required=True, metavar="<verb>")

    lp = verbs.add_parser("list", help="List all providers")
    lp.set_defaults(func=_list)

    cp = verbs.add_parser("create", help="Add a new provider")
    cp.add_argument("--name", required=True, help="Provider name")
    cp.add_argument("--type", required=True, help="Provider type (e.g. openai, ollama)")
    cp.add_argument("--url", help="Provider base URL")
    cp.add_argument("--api-key-secret", help="Secrets store key for the API key")
    cp.set_defaults(func=_create)

    up = verbs.add_parser("update", help="Update a provider")
    up.add_argument("name", help="Provider name")
    up.add_argument("--url", help="Provider base URL")
    up.add_argument("--api-key-secret", help="Secrets store key for the API key")
    up.add_argument("--api-key", help="Inline API key")
    up.add_argument("--enabled", choices=["true", "false"], help="Enable or disable")
    up.add_argument("--auto-manage", choices=["true", "false"], help="Auto-manage lifecycle")
    up.add_argument("--keep-alive-minutes", type=int, help="Keep-alive timeout in minutes")
    up.set_defaults(func=_update)

    dp = verbs.add_parser("delete", help="Delete a provider")
    dp.add_argument("name", help="Provider name")
    dp.set_defaults(func=_delete)

    sp = verbs.add_parser("start", help="Start a stopped provider")
    sp.add_argument("name", help="Provider name")
    sp.set_defaults(func=_start)

    stp = verbs.add_parser("stop", help="Stop a running provider")
    stp.add_argument("name", help="Provider name")
    stp.set_defaults(func=_stop)

    # SKIP: test (needs body with type+url, more complex than flat args)
    # SKIP: models (cache management endpoint, read-only)
    # SKIP: models/refresh (cache management endpoint, write but no simple args)
    # SKIP: types (metadata endpoint, read-only, low value as a CLI verb)


def _list(args, client):
    return client.get("/api/providers")


def _create(args, client):
    body = {"name": args.name, "type": args.type}
    if args.url:
        body["url"] = args.url
    if args.api_key_secret:
        body["api_key_secret"] = args.api_key_secret
    return client.post("/api/providers", body=body)


def _update(args, client):
    body = {}
    if args.url is not None:
        body["url"] = args.url
    if args.api_key_secret is not None:
        body["api_key_secret"] = args.api_key_secret
    if args.api_key is not None:
        body["api_key"] = args.api_key
    if args.enabled is not None:
        body["enabled"] = args.enabled == "true"
    if args.auto_manage is not None:
        body["auto_manage"] = args.auto_manage == "true"
    if args.keep_alive_minutes is not None:
        body["keep_alive_minutes"] = args.keep_alive_minutes
    if not body:
        raise SystemExit("providers update: nothing to change (pass at least one field to update)")
    return client.patch(f"/api/providers/{quote(args.name, safe='')}", body=body)


def _delete(args, client):
    return client.delete(f"/api/providers/{quote(args.name, safe='')}")


def _start(args, client):
    return client.post(f"/api/providers/{quote(args.name, safe='')}/start")


def _stop(args, client):
    return client.post(f"/api/providers/{quote(args.name, safe='')}/stop")

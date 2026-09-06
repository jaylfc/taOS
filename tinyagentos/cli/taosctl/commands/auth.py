"""taosctl auth -- configure and check the CLI's connection to a taOS server."""
from __future__ import annotations

import json
import os

from tinyagentos.cli.taosctl import client as _client

NOUN = "auth"


def register(subparsers) -> None:
    p = subparsers.add_parser(NOUN, help="Configure and check the connection")
    verbs = p.add_subparsers(dest="verb", required=True, metavar="<verb>")

    lp = verbs.add_parser("login", help="Save a server URL + token to the config file")
    lp.add_argument("--url", help="Server base URL")
    lp.add_argument("--token", help="API token (Bearer)")
    lp.set_defaults(func=_login)

    sp = verbs.add_parser("status", help="Show the resolved URL + whether a token is set")
    sp.set_defaults(func=_status)

    wp = verbs.add_parser("whoami", help="Verify the token by calling the server")
    wp.set_defaults(func=_whoami)


def _login(args, client):
    # Persist only what the user explicitly passes, falling back to the existing
    # config file (NOT env vars). resolve() would pull in TAOS_TOKEN, which would
    # silently write an env-only token to disk on a bare `auth login`.
    existing = _client._load_config()
    url = args.url or existing.get("url") or _client.DEFAULT_URL
    token = args.token if args.token is not None else existing.get("token")
    _client.CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload = json.dumps({"url": url, "token": token}, indent=2)
    # The config holds a credential, so create it owner-only (0o600) and
    # atomically, never world-readable between create and a later chmod.
    fd = os.open(str(_client.CONFIG_PATH), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(payload)
    return {"saved": str(_client.CONFIG_PATH), "url": url, "token_set": bool(token)}


def _status(args, client):
    return {"url": client.base_url, "token_set": bool(client.token)}


def _whoami(args, client):
    # No dedicated whoami endpoint; a successful authed call to /api/agents
    # confirms the token is accepted by the server.
    client.get("/api/agents")
    return {"url": client.base_url, "authenticated": True}

"""taosctl channels -- inspect and manage agent messaging channels.

Wraps tinyagentos/routes/channels.py. Mirrors the agents pattern (NOUN,
register(), small handlers that call the client and return data).

The route module has no GET-one endpoint, so there is no `get` verb; use
`list --agent <name>` to see one agent's channels. No endpoint was skipped:
none needs upload, multipart or streaming.
"""
from __future__ import annotations

import argparse
import json
from urllib.parse import quote

NOUN = "channels"


def _json_object(raw: str) -> dict:
    try:
        value = json.loads(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"not valid JSON: {exc}") from None
    if not isinstance(value, dict):
        raise argparse.ArgumentTypeError("must be a JSON object")
    return value


def register(subparsers) -> None:
    p = subparsers.add_parser(NOUN, help="Inspect and manage agent channels")
    verbs = p.add_subparsers(dest="verb", required=True, metavar="<verb>")

    lp = verbs.add_parser("list", help="List channels (all, or one agent's)")
    lp.add_argument("--agent", dest="agent_name", default=None,
                    help="Only list channels for this agent")
    lp.set_defaults(func=_list)

    tp = verbs.add_parser("types", help="List supported channel types")
    tp.set_defaults(func=_types)

    cp = verbs.add_parser("create", help="Add a channel to an agent")
    cp.add_argument("--agent", dest="agent_name", required=True, help="Agent name")
    cp.add_argument("--type", dest="channel_type", required=True,
                    help="Channel type (see `channels types`)")
    cp.add_argument("--config", type=_json_object, default=None,
                    help='Channel config as a JSON object, e.g. \'{"token": "..."}\'')
    cp.set_defaults(func=_create)

    dp = verbs.add_parser("delete", help="Remove an agent's channel of a given type")
    dp.add_argument("agent_name", help="Agent name")
    dp.add_argument("channel_type", help="Channel type")
    dp.set_defaults(func=_delete)

    gp = verbs.add_parser("toggle", help="Enable or disable a channel by id")
    gp.add_argument("channel_id", type=int, help="Channel id")
    state = gp.add_mutually_exclusive_group(required=True)
    state.add_argument("--enable", dest="enabled", action="store_true",
                       help="Enable the channel")
    state.add_argument("--disable", dest="enabled", action="store_false",
                       help="Disable the channel")
    gp.set_defaults(func=_toggle)


def _list(args, client):
    if args.agent_name:
        return client.get(f"/api/channels/agent/{quote(args.agent_name, safe='')}")
    return client.get("/api/channels")


def _types(args, client):
    return client.get("/api/channels/types")


def _create(args, client):
    body = {"agent_name": args.agent_name, "type": args.channel_type}
    if args.config is not None:
        body["config"] = args.config
    return client.post("/api/channels", body=body)


def _delete(args, client):
    path = (
        f"/api/channels/{quote(args.agent_name, safe='')}"
        f"/{quote(args.channel_type, safe='')}"
    )
    return client.delete(path)


def _toggle(args, client):
    return client.post(
        f"/api/channels/{args.channel_id}/toggle",
        body={"enabled": args.enabled},
    )

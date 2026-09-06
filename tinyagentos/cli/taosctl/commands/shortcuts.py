"""taosctl shortcuts -- list agent shortcuts.

Reference noun: shows the pattern every other noun module follows (a NOUN, a
register() that wires verb subparsers, and small handlers that call the client
and return data for the framework to render).
"""
from __future__ import annotations

from urllib.parse import quote

NOUN = "shortcuts"


def register(subparsers) -> None:
    p = subparsers.add_parser(NOUN, help="List agent shortcuts")
    verbs = p.add_subparsers(dest="verb", required=True, metavar="<verb>")

    lp = verbs.add_parser("list", help="List shortcuts for an agent")
    lp.add_argument("agent_id", help="Agent name or id")
    lp.set_defaults(func=_list)


def _list(args, client):
    return client.get(f"/api/agents/{quote(args.agent_id, safe='')}/shortcuts")

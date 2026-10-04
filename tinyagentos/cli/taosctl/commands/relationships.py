"""taosctl relationships -- manage agent groups, memberships and messaging permissions.

Wraps tinyagentos/routes/relationships.py. Every route there is mapped; none
needs upload, multipart or streaming.
"""
from __future__ import annotations

from urllib.parse import quote

NOUN = "relationships"


def register(subparsers) -> None:
    p = subparsers.add_parser(
        NOUN, help="Manage agent groups, memberships and messaging permissions"
    )
    verbs = p.add_subparsers(dest="verb", required=True, metavar="<verb>")

    lp = verbs.add_parser("list", help="List all groups with members")
    lp.set_defaults(func=_list)

    cp = verbs.add_parser("create", help="Create an agent group")
    cp.add_argument("name", help="Group name")
    cp.add_argument("--description", default=None, help="Group description")
    cp.add_argument("--lead", dest="lead_agent", default=None, help="Lead agent name")
    cp.add_argument("--color", default=None, help="Display color, e.g. #888888")
    cp.set_defaults(func=_create)

    up = verbs.add_parser("update", help="Update a group's properties")
    up.add_argument("group_id", type=int, help="Group id")
    up.add_argument("--name", default=None, help="New group name")
    up.add_argument("--description", default=None, help="New description")
    up.add_argument("--lead", dest="lead_agent", default=None, help="New lead agent name")
    up.add_argument("--color", default=None, help="New display color")
    up.set_defaults(func=_update)

    dp = verbs.add_parser("delete", help="Delete a group and its memberships")
    dp.add_argument("group_id", type=int, help="Group id")
    dp.set_defaults(func=_delete)

    ap = verbs.add_parser("add-member", help="Add an agent to a group")
    ap.add_argument("group_id", type=int, help="Group id")
    ap.add_argument("agent_name", help="Agent name")
    ap.add_argument("--role", default=None, help="Member role (server default: member)")
    ap.set_defaults(func=_add_member)

    rp = verbs.add_parser("remove-member", help="Remove an agent from a group")
    rp.add_argument("group_id", type=int, help="Group id")
    rp.add_argument("agent_name", help="Agent name")
    rp.set_defaults(func=_remove_member)

    gp = verbs.add_parser("agent", help="Show an agent's groups and permissions")
    gp.add_argument("name", help="Agent name")
    gp.set_defaults(func=_agent)

    pp = verbs.add_parser("allow", help="Allow one agent to message another")
    pp.add_argument("--from", dest="from_agent", required=True, help="Sending agent")
    pp.add_argument("--to", dest="to_agent", required=True, help="Receiving agent")
    pp.set_defaults(func=_allow)

    vp = verbs.add_parser("revoke", help="Revoke a messaging permission")
    vp.add_argument("--from", dest="from_agent", required=True, help="Sending agent")
    vp.add_argument("--to", dest="to_agent", required=True, help="Receiving agent")
    vp.set_defaults(func=_revoke)


def _optional(args, *fields) -> dict:
    return {f: getattr(args, f) for f in fields if getattr(args, f) is not None}


def _list(args, client):
    return client.get(f"/api/relationships/groups")


def _create(args, client):
    body = {"name": args.name}
    body.update(_optional(args, "description", "lead_agent", "color"))
    return client.post(f"/api/relationships/groups", body=body)


def _update(args, client):
    body = _optional(args, "name", "description", "lead_agent", "color")
    return client.put(f"/api/relationships/groups/{args.group_id}", body=body)


def _delete(args, client):
    return client.delete(f"/api/relationships/groups/{args.group_id}")


def _add_member(args, client):
    body = {"agent_name": args.agent_name}
    body.update(_optional(args, "role"))
    return client.post(f"/api/relationships/groups/{args.group_id}/members", body=body)


def _remove_member(args, client):
    name = quote(args.agent_name, safe="")
    return client.delete(f"/api/relationships/groups/{args.group_id}/members/{name}")


def _agent(args, client):
    return client.get(f"/api/relationships/agent/{quote(args.name, safe='')}")


def _allow(args, client):
    body = {"from_agent": args.from_agent, "to_agent": args.to_agent}
    return client.post(f"/api/relationships/permissions", body=body)


def _revoke(args, client):
    # The route reads a JSON body on DELETE; client.delete() sends none, so go
    # through request() directly.
    body = {"from_agent": args.from_agent, "to_agent": args.to_agent}
    return client.request("DELETE", f"/api/relationships/permissions", body=body)

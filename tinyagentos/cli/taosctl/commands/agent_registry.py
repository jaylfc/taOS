"""taosctl agent-registry -- inspect and govern the canonical agent registry.

Wraps the read, update, and lifecycle endpoints in
tinyagentos/routes/agent_registry.py so admins and scripts can list, inspect,
rename, revoke, and run governance transitions (approve/reject/suspend/
reactivate) on registered agent identities without the web UI. Distinct from the
``agents`` group, which drives deploy/lifecycle on /api/agents.

Skipped: POST /api/agents/registry/register, a signed-pubkey enrolment flow that
expects a cryptographically prepared payload, not a shell verb.
"""
from __future__ import annotations

import sys
from urllib.parse import quote

NOUN = "agent-registry"


def register(subparsers) -> None:
    p = subparsers.add_parser(NOUN, help="Inspect and govern the canonical agent registry")
    verbs = p.add_subparsers(dest="verb", required=True, metavar="<verb>")

    lp = verbs.add_parser("list", help="List registry entries (own, or all for admins)")
    lp.add_argument("--status", default=None, help="Filter by status (e.g. pending, active)")
    lp.set_defaults(func=_list)

    gp = verbs.add_parser("get", help="Get one registry entry by canonical id")
    gp.add_argument("canonical_id")
    gp.set_defaults(func=_get)

    pk = verbs.add_parser("pubkey", help="Fetch the registry signing public key")
    pk.set_defaults(func=_pubkey)

    rv = verbs.add_parser("revoked", help="Global revocation feed")
    rv.set_defaults(func=_revoked)

    iv = verbs.add_parser("inactive", help="Inactive (non-active) entries")
    iv.set_defaults(func=_inactive)

    gr = verbs.add_parser("grants", help="Active grants, optionally for one agent")
    gr.add_argument("--canonical-id", default=None, help="Narrow to a single agent")
    gr.set_defaults(func=_grants)

    up = verbs.add_parser("update", help="Update mutable metadata on an entry")
    up.add_argument("canonical_id")
    up.add_argument("--display-name", default=None)
    up.add_argument("--handle", default=None)
    up.add_argument("--role", default=None)
    up.add_argument("--capability", action="append", dest="capabilities", default=None,
                    help="capability to set (repeatable; replaces the list)")
    up.set_defaults(func=_update)

    rk = verbs.add_parser("revoke", help="Revoke an entry (sets revoked_at)")
    rk.add_argument("canonical_id")
    rk.set_defaults(func=_revoke)

    ap = verbs.add_parser("approve", help="Approve a pending agent (admin)")
    ap.add_argument("canonical_id")
    ap.set_defaults(func=_approve)

    rj = verbs.add_parser("reject", help="Reject a pending agent (admin)")
    rj.add_argument("canonical_id")
    rj.set_defaults(func=_reject)

    sp = verbs.add_parser("suspend", help="Suspend an active agent (admin)")
    sp.add_argument("canonical_id")
    sp.set_defaults(func=_suspend)

    ra = verbs.add_parser("reactivate", help="Reactivate a suspended agent (admin)")
    ra.add_argument("canonical_id")
    ra.set_defaults(func=_reactivate)


def _cid(args):
    return quote(args.canonical_id, safe="")


def _list(args, client):
    params = {"status": args.status} if args.status else None
    return client.get("/api/agents/registry", params=params)


def _get(args, client):
    return client.get(f"/api/agents/registry/{_cid(args)}")


def _pubkey(args, client):
    return client.get("/api/agents/registry/pubkey")


def _revoked(args, client):
    return client.get("/api/agents/registry/revoked")


def _inactive(args, client):
    return client.get("/api/agents/registry/inactive")


def _grants(args, client):
    params = {"canonical_id": args.canonical_id} if args.canonical_id else None
    return client.get("/api/agents/registry/grants", params=params)


def _update(args, client):
    # Send only supplied fields; the route's PatchRegistryRequest treats every
    # field as optional and leaves omitted ones untouched.
    body = {}
    if args.display_name is not None:
        body["display_name"] = args.display_name
    if args.handle is not None:
        body["handle"] = args.handle
    if args.role is not None:
        body["role"] = args.role
    if args.capabilities is not None:
        body["capabilities"] = args.capabilities
    if not body:
        # No fields supplied: skip the no-op PATCH and exit non-zero so scripts
        # can detect the mistake instead of getting a silent empty-body call.
        print(
            "no fields supplied; pass at least one of "
            "--display-name/--handle/--role/--capability",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return client.patch(f"/api/agents/registry/{_cid(args)}", body)


def _revoke(args, client):
    return client.delete(f"/api/agents/registry/{_cid(args)}")


def _approve(args, client):
    return client.post(f"/api/agents/registry/{_cid(args)}/approve")


def _reject(args, client):
    return client.post(f"/api/agents/registry/{_cid(args)}/reject")


def _suspend(args, client):
    return client.post(f"/api/agents/registry/{_cid(args)}/suspend")


def _reactivate(args, client):
    return client.post(f"/api/agents/registry/{_cid(args)}/reactivate")

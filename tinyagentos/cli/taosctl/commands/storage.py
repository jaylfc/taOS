"""taosctl storage -- add disks as storage areas and assign a person's primary home.

Adding a disk formats it. The CLI shows the disk's class and reasons first
(``storage disks``); ``storage add`` needs the disk's model and serial typed
back exactly, and the root helper re-checks everything before it writes.
"""
from __future__ import annotations

from urllib.parse import quote

NOUN = "storage"


def register(subparsers) -> None:
    p = subparsers.add_parser(NOUN, help="Storage areas: disks, add, primary home")
    verbs = p.add_subparsers(dest="verb", required=True, metavar="<verb>")

    verbs.add_parser("status", help="Is the root helper installed, and which tools exist").set_defaults(func=_status)
    verbs.add_parser("disks", help="List disks with class (available/protected) and reasons").set_defaults(func=_disks)
    verbs.add_parser("areas", help="List storage areas with capacity and free space").set_defaults(func=_areas)

    a = verbs.add_parser("add", help="Format a disk as a storage area (DESTROYS its contents)")
    a.add_argument("--disk", required=True, help="/dev/disk/by-id/... path from 'storage disks'")
    a.add_argument("--name", required=True, help="Area name (lowercase letters, digits, dashes)")
    a.add_argument("--kind", choices=("private", "shared"), default="private",
                   help="private = btrfs + LUKS2 (default); shared = VeraCrypt NTFS, usable from Windows")
    a.add_argument("--confirm", required=True, help='The disk\'s "<model> <serial>", exactly as listed')
    a.set_defaults(func=_add)

    sp = verbs.add_parser("set-primary", help="Make an area a person's primary home")
    sp.add_argument("--person", required=True)
    sp.add_argument("--area", required=True)
    sp.set_defaults(func=_set_primary)

    pr = verbs.add_parser("primary", help="Show a person's primary home with capacity and free space")
    pr.add_argument("--person", default=None, help="Person id (default: the caller)")
    pr.set_defaults(func=_primary)

    pp = verbs.add_parser("propose", help="Agents: ask the person to add a disk or set a primary home")
    pp.add_argument("action", choices=("add", "set-primary"))
    pp.add_argument("--disk")
    pp.add_argument("--name")
    pp.add_argument("--kind", default="private")
    pp.add_argument("--person")
    pp.add_argument("--area")
    pp.set_defaults(func=_propose)

    verbs.add_parser("proposals", help="List storage proposals").set_defaults(func=_proposals)
    ap = verbs.add_parser("approve", help="Approve a proposal (an add needs --confirm)")
    ap.add_argument("id")
    ap.add_argument("--confirm", default=None)
    ap.set_defaults(func=_approve)
    rp = verbs.add_parser("reject", help="Reject a proposal")
    rp.add_argument("id")
    rp.set_defaults(func=_reject)


def _status(args, client):
    return client.get("/api/storage/status")


def _disks(args, client):
    return client.get("/api/storage/disks")


def _areas(args, client):
    return client.get("/api/storage/areas")


def _add(args, client):
    return client.post("/api/storage/areas", body={
        "disk": args.disk, "name": args.name, "kind": args.kind, "confirm": args.confirm})


def _set_primary(args, client):
    return client.put("/api/storage/primary", json={"person": args.person, "area": args.area})


def _primary(args, client):
    return client.get("/api/storage/primary", params={"person": args.person} if args.person else None)


def _propose(args, client):
    keys = ("disk", "name", "kind") if args.action == "add" else ("person", "area")
    return client.post("/api/storage/proposals", body={
        "action": args.action, "params": {k: getattr(args, k) for k in keys}})


def _proposals(args, client):
    return client.get("/api/storage/proposals")


def _approve(args, client):
    return client.post(f"/api/storage/proposals/{quote(args.id, safe='')}/approve", body={"confirm": args.confirm})


def _reject(args, client):
    return client.post(f"/api/storage/proposals/{quote(args.id, safe='')}/reject", body={})

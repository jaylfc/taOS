"""taosctl storage -- manage storage areas and disks."""
from __future__ import annotations

NOUN = "storage"


def register(subparsers) -> None:
    p = subparsers.add_parser(NOUN, help="Manage storage areas and disks")
    verbs = p.add_subparsers(dest="verb", required=True, metavar="<verb>")

    # disks
    dp = verbs.add_parser("disks", help="List block devices with safety classification")
    dp.set_defaults(func=_disks)

    # add --disk <by-id> --confirm "<model> <serial>" --name <area> [--encrypt system-unlock|none]
    ap = verbs.add_parser("add", help="Add a disk as a storage area")
    ap.add_argument("--disk", required=True, help="Block device by-id path (e.g., /dev/disk/by-id/ata-...)")
    ap.add_argument("--confirm", required=True, help='Exact confirmation string: "<model> <serial>"')
    ap.add_argument("--name", required=True, help="Storage area name (alphanumeric, hyphens, underscores)")
    ap.add_argument("--encrypt", choices=["system-unlock", "none"], default="system-unlock",
                    help="Encryption mode (default: system-unlock)")
    ap.set_defaults(func=_add)

    # set-primary --person <id> --area <area>
    sp = verbs.add_parser("set-primary", help="Set a storage area as a person's primary home")
    sp.add_argument("--person", required=True, help="Person/user ID")
    sp.add_argument("--area", required=True, help="Storage area name")
    sp.set_defaults(func=_set_primary)

    # areas
    lp = verbs.add_parser("areas", help="List registered storage areas")
    lp.set_defaults(func=_areas)

    # primary <person>
    pp = verbs.add_parser("primary", help="Show primary home capacity for a person")
    pp.add_argument("person", help="Person/user ID")
    pp.set_defaults(func=_primary)


def _disks(args, client):
    result = client.get("/api/storage/disks")
    return result


def _add(args, client):
    body = {
        "disk": args.disk,
        "confirm": args.confirm,
        "name": args.name,
        "encrypt": args.encrypt,
    }
    result = client.post("/api/storage/add", body=body)
    return result


def _set_primary(args, client):
    body = {"person": args.person, "area": args.area}
    result = client.post("/api/storage/set-primary", body=body)
    return result


def _areas(args, client):
    result = client.get("/api/storage/areas")
    return result


def _primary(args, client):
    result = client.get(f"/api/storage/primary/{args.person}")
    return result
"""taosctl storage -- manage storage areas and disks."""
from __future__ import annotations

import json

NOUN = "storage"


def register(subparsers) -> None:
    p = subparsers.add_parser(NOUN, help="Manage storage areas and disks")
    verbs = p.add_subparsers(dest="verb", required=True, metavar="<verb>")

    # disks --json
    dp = verbs.add_parser("disks", help="List block devices with safety classification")
    dp.add_argument("--json", action="store_true", help="Machine-readable JSON output")
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
    lp.add_argument("--json", action="store_true", help="Machine-readable JSON output")
    lp.set_defaults(func=_areas)

    # primary <person>
    pp = verbs.add_parser("primary", help="Show primary home capacity for a person")
    pp.add_argument("person", help="Person/user ID")
    pp.add_argument("--json", action="store_true", help="Machine-readable JSON output")
    pp.set_defaults(func=_primary)


def _disks(args, client):
    result = client.get("/api/storage/disks")
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        for disk in result:
            safety = disk.get("safety_class", "unknown")
            reason = disk.get("safety_reason")
            reason_str = f" ({reason})" if reason else ""
            print(f"{disk['by_id_path']:50s} {disk['size_human']:>8s} {disk['model']:30s} {disk['serial']:20s} [{safety}{reason_str}]")
    return result


def _add(args, client):
    body = {
        "disk": args.disk,
        "confirm": args.confirm,
        "name": args.name,
        "encrypt": args.encrypt,
    }
    result = client.post("/api/storage/add", body=body)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Storage area created: {result['name']}")
        print(f"  ID: {result['id']}")
        print(f"  Mount: {result['mount']}")
        print(f"  Size: {result['size_bytes']} bytes")
        print(f"  Encryption: {result['encryption']}")
    return result


def _set_primary(args, client):
    body = {"person": args.person, "area": args.area}
    result = client.post("/api/storage/set-primary", body=body)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Primary home set for {args.person}: {args.area}")
        print(f"  Home path: {result['home_path']}")
    return result


def _areas(args, client):
    result = client.get("/api/storage/areas")
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        for area in result:
            free_gib = area['free_bytes'] / (1024**3)
            size_gib = area['size_bytes'] / (1024**3)
            print(f"{area['name']:20s} {area['mount']:40s} {size_gib:>6.1f} GiB  free: {free_gib:>6.1f} GiB  [{area['encryption']}]")
    return result


def _primary(args, client):
    result = client.get(f"/api/storage/primary/{args.person}")
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        used_gib = result['used_bytes'] / (1024**3)
        size_gib = result['size_bytes'] / (1024**3)
        free_gib = result['free_bytes'] / (1024**3)
        print(f"Primary home for {args.person}:")
        print(f"  Area: {result['area_name']}")
        print(f"  Mount: {result['mount']}")
        print(f"  Capacity: {size_gib:.1f} GiB")
        print(f"  Used: {used_gib:.1f} GiB")
        print(f"  Free: {free_gib:.1f} GiB")
        print(f"  Encryption: {result['encryption']}")
    return result
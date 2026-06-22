"""taosctl store -- inspect and manage the app store catalog."""
from __future__ import annotations

from urllib.parse import quote

NOUN = "store"


def register(subparsers) -> None:
    p = subparsers.add_parser(NOUN, help="Inspect and manage the app store catalog")
    verbs = p.add_subparsers(dest="verb", required=True, metavar="<verb>")

    lp = verbs.add_parser("list", help="List all apps in the catalog")
    lp.add_argument("--type", default=None, help="Filter by app type")
    lp.add_argument("--query", default=None, help="Filter by name/description")
    lp.set_defaults(func=_list)

    ip = verbs.add_parser("installed", help="List currently installed apps")
    ip.set_defaults(func=_installed)

    gp = verbs.add_parser("get", help="Get one app by id")
    gp.add_argument("app_id", help="App id")
    gp.set_defaults(func=_get)

    pp = verbs.add_parser("popularity", help="List popularity data per app")
    pp.add_argument("--type", default=None, help="Filter by app type")
    pp.set_defaults(func=_popularity)

    inp = verbs.add_parser("install", help="Install an app from the catalog")
    inp.add_argument("app_id", help="App id")
    inp.add_argument("--variant-id", default=None, help="Model variant id")
    inp.set_defaults(func=_install)

    unp = verbs.add_parser("uninstall", help="Uninstall an installed app")
    unp.add_argument("app_id", help="App id")
    unp.set_defaults(func=_uninstall)

    sp = verbs.add_parser("sync", help="Sync the app catalog from the git repository")
    sp.set_defaults(func=_sync)

    # POST /api/store/resolve -- skipped: complex nested body (manifest_id,
    # variant_id, target_remote, force) that does not map cleanly to argparse.


def _list(args, client):
    params = {}
    if args.type:
        params["type"] = args.type
    if args.query:
        params["query"] = args.query
    return client.get("/api/store/catalog", params=params or None)


def _installed(args, client):
    return client.get("/api/store/installed")


def _get(args, client):
    return client.get(f"/api/store/app/{quote(args.app_id, safe='')}")


def _popularity(args, client):
    params = {}
    if args.type:
        params["type"] = args.type
    return client.get("/api/store/popularity", params=params or None)


def _install(args, client):
    body = {"app_id": args.app_id}
    if args.variant_id:
        body["variant_id"] = args.variant_id
    return client.post("/api/store/install", json=body)


def _uninstall(args, client):
    return client.post("/api/store/uninstall", json={"app_id": args.app_id})


def _sync(args, client):
    return client.post("/api/store/sync")

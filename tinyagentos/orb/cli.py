"""Command line front end (python -m tinyagentos.orb.cli)."""
import argparse
import sys

from .catalog import build_extents, fmt_list, fmt_versions, load_catalog, plan_install, plan_updates
from .frames import NakError, OrbError, cstr
from .image import NAME_LEN, check_image
from .store_client import Client
from .transport import find_port, open_port

def _progress_printer():
    last = [-1]

    def prog(done, total):
        pct = done * 100 // total if total else 100
        if pct != last[0]:
            last[0] = pct
            sys.stdout.write("\r  %3d%%  %d / %d bytes" % (pct, done, total))
            sys.stdout.flush()
    return prog


def _do_install(cl, cat, dev, plan, name, add):
    exts = build_extents(cat, plan)
    v, app = plan["version"], plan["app"]
    if plan["downgrade"]:
        print(plan["message"])
    print("installing %s %s (%d block%s, %s) as %r" % (app["id"], v["version"], sum(x.nblocks for x in exts),
                                                   "" if sum(x.nblocks for x in exts) == 1 else "s",
                                                   "update" if plan["update"] else "new", name or app["name"]))
    entry = cl.install_app(app["id"], name or app["name"], exts, add=add, progress=_progress_printer())
    print("\ninstalled as entry %d" % entry)


def main(argv=None, opener=None):
    ap = argparse.ArgumentParser(prog="orb_store.py", description="Install apps into the Orb's block storage over USB.")
    ap.add_argument("-p", "--port", help="serial port (default: first /dev/cu.usbmodem* or /dev/ttyACM*)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    pl = sub.add_parser("list", help="show slot 0, the installed apps and the free blocks")
    pl.add_argument("--catalog")
    pi = sub.add_parser("install", help="install an app from the catalog (latest version unless --version)")
    pi.add_argument("app")
    pi.add_argument("--catalog", required=True)
    pi.add_argument("--version")
    pi.add_argument("--name", help="display name shown on the Orb (max 23 bytes; default the catalog name)")
    pi.add_argument("--add", action="store_true", help="install next to an installed entry of the same app id (a second version)")
    pu = sub.add_parser("update", help="install the latest version of installed apps that are behind (pinned ones are skipped)")
    pu.add_argument("entry", nargs="?", type=int)
    pu.add_argument("--all", action="store_true")
    pu.add_argument("--catalog", required=True)
    pv = sub.add_parser("versions", help="list the versions of an app in the catalog")
    pv.add_argument("app")
    pv.add_argument("--catalog", required=True)
    pr = sub.add_parser("remove", help="remove an installed app (entry index)")
    pr.add_argument("entry", type=int)
    pp = sub.add_parser("pin", help="pin an entry: update skips it")
    pp.add_argument("entry", type=int)
    pn = sub.add_parser("unpin", help="unpin an entry")
    pn.add_argument("entry", type=int)
    pt = sub.add_parser("taos", help="install a taOS image into slot 0")
    pt.add_argument("image")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "versions":
            cat = load_catalog(args.catalog)
            print(fmt_versions(cat.app(args.app)))
            return 0
        if args.cmd == "update" and args.entry is None and not args.all:
            raise OrbError("update needs an entry index or --all")
        if args.cmd in ("remove", "pin", "unpin") and not 0 <= args.entry < 12:
            raise OrbError("entry %d is not an app entry (0..11); slot 0 (taOS) cannot be removed or pinned" % args.entry)
        image = None
        if args.cmd == "taos":
            try:
                with open(args.image, "rb") as f:
                    image = f.read()
            except OSError as e:
                raise OrbError("cannot read %s: %s" % (args.image, e))
            check_image(image, "taos")                  # fail before touching the port
        if args.cmd == "install" and args.name:
            cstr(args.name, NAME_LEN)
        cat = load_catalog(args.catalog) if getattr(args, "catalog", None) else None
        ser = (opener or open_port)(args.port or find_port())
        try:
            cl = Client(ser)
            if args.cmd == "list":
                print(fmt_list(cl.hello(), cat))
            elif args.cmd == "install":
                dev = cl.hello()
                plan = plan_install(cat, dev, args.app, args.version, add=args.add)
                _do_install(cl, cat, dev, plan, args.name, args.add)
            elif args.cmd == "update":
                dev = cl.hello()
                todo, skipped = plan_updates(cat, dev, None if args.all else args.entry)
                for s in skipped:
                    print("skipping pinned entry %d (%s)" % (s["index"], s["display_name"] or s["app_id"]))
                if not todo:
                    print("nothing to update")
                for n, t in enumerate(todo):
                    plan = plan_install(cat, dev, t["app"]["id"], t["version"]["version"])
                    _do_install(cl, cat, dev, plan, None, False)
                    if n + 1 < len(todo):
                        dev = cl.hello()                   # entry indexes change after an update: look again
            elif args.cmd == "remove":
                cl.remove(args.entry)
                print("entry %d removed" % args.entry)
            elif args.cmd in ("pin", "unpin"):
                cl.pin(args.entry, args.cmd == "pin")
                print("entry %d %s" % (args.entry, "pinned" if args.cmd == "pin" else "unpinned"))
            elif args.cmd == "taos":
                cl.hello()
                print("installing taOS (%d bytes) into slot 0" % len(image))
                cl.install_taos(image, "taOS", progress=_progress_printer())
                print("\ninstalled")
        finally:
            close = getattr(ser, "close", None)
            if close:
                close()
    except NakError as e:
        print("\nerror: %s" % e, file=sys.stderr)
        return 1
    except (OrbError, ValueError) as e:
        print("error: %s" % e, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ninterrupted (install aborted, the card is as it was)", file=sys.stderr)
        return 1
    except Exception as e:                         # serial errors etc.
        print("error: %s: %s" % (type(e).__name__, e), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Catalog rules: version selection, install and update planning."""
import hashlib
import json
import os

from .frames import OrbError
from .image import BLOCK, TAOS_INDEX, check_image
from .protocol import Extent

# ------------------------------------------------------------------ catalog (SPEC-orb-store.md s11) ----
# Firmware catalog kinds: None (legacy, no kind field) or "orb-firmware"
FIRMWARE_KINDS = (None, "orb-firmware")


def check_firmware_kind(doc: dict) -> None:
    """Raise OrbError if doc's kind is not a firmware catalog kind."""
    if not isinstance(doc, dict):
        raise OrbError("catalog is not a JSON object")
    kind = doc.get("kind")
    if kind not in FIRMWARE_KINDS:
        raise OrbError("catalog kind %r is not a firmware catalog" % kind)


class Catalog:
    def __init__(self, apps=None, base="."):
        self.apps = apps or {}
        self.base = base

    def app(self, app_id):
        if app_id not in self.apps:
            raise OrbError("app %r is not in the catalog (known: %s)" % (app_id, ", ".join(sorted(self.apps)) or "none"))
        return self.apps[app_id]


def load_catalog(path):
    """catalog.json: {"apps": [{"id", "name", "versions": [newest first, ...]}]}. Relative file names are resolved
    against the catalog's directory."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError) as e:
        raise OrbError("cannot read the catalog %s: %s" % (path, e))
    check_firmware_kind(raw)
    base = os.path.dirname(os.path.abspath(path))

    def resolve(fn):
        return None if fn is None else (fn if os.path.isabs(fn) else os.path.join(base, fn))

    apps = {}
    for a in raw.get("apps", []):
        for v in a.get("versions", []):
            if v.get("file"):
                v["file"] = resolve(v["file"])
            for e in v.get("extents", []):
                e["file"] = resolve(e.get("file"))
        apps[a["id"]] = a
    return Catalog(apps, base)


def latest_version(app):
    """latest = the first non-yanked entry in list order. No semver parsing: the publisher orders the list."""
    for v in app.get("versions", []):
        if not v.get("yanked"):
            return v
    return None


def pick_version(app, version):
    if version is None:
        v = latest_version(app)
        if v is None:
            raise OrbError("%s has no installable version (all are yanked)" % app["id"])
        return v
    for v in app.get("versions", []):
        if v["version"] == version:
            return v                          # a yanked one is only offered by exact --version
    raise OrbError("no version %r of %s (have: %s)" % (version, app["id"], ", ".join(v["version"] for v in app.get("versions", []))))


def version_extents(v):
    """The extent list of a catalog version; a plain version (no "extents") is one APP extent sized to its image."""
    if v.get("extents"):
        return v["extents"]
    blocks = max(1, -(-int(v.get("size", 0)) // BLOCK))
    return [{"label": "orb_app", "type": 0, "subtype": 0x1F, "blocks": blocks, "keep": 0, "file": v.get("file")}]


def _index_of(app, elf_hex):
    for i, v in enumerate(app.get("versions", [])):
        if v.get("elf_sha256") == elf_hex:
            return i
    return None


def identify(cat, elf, hint=None):
    """Which (app, version) is this build? By elf_sha256 only: stock OpenHarness, our fork and NixOH all report the same
    project_name and version string. `hint` (the device's app_id) is searched first."""
    elf_hex = elf.hex() if isinstance(elf, (bytes, bytearray)) else elf
    order = ([cat.apps[hint]] if hint in cat.apps else []) + [a for k, a in cat.apps.items() if k != hint]
    for a in order:
        i = _index_of(a, elf_hex)
        if i is not None:
            return a, a["versions"][i]
    return None


def status_of(cat, entry):
    """latest / update available -> V / pinned (latest V) / newer than catalog / unknown (matching by elf)."""
    hit = identify(cat, entry["elf_sha256"], entry.get("app_id"))
    if hit is None:
        return "unknown"
    app, inst = hit
    latest = latest_version(app)
    if latest is None:
        return "unknown"
    i, l = app["versions"].index(inst), app["versions"].index(latest)
    if i == l:
        return "latest"
    if i < l:
        return "newer than catalog"
    if entry.get("pinned"):
        return "pinned (latest %s)" % latest["version"]
    return "update available -> %s" % latest["version"]


def _installed_entry(dev, app_id):
    """The device replaces the FIRST READY entry with an app_id: that is the entry an install of it would update."""
    for s in dev["slots"]:
        if s["index"] != TAOS_INDEX and s["present"] and s["app_id"] == app_id:
            return s
    return None


def plan_install(cat, dev, app_id, version, add=False):
    """Decide an install against what the device reports. Raises OrbError for what must not be sent. Returns
    {"app", "version", "entry" (the installed entry that gets updated or None), "update", "downgrade", "message"}."""
    app = cat.app(app_id)
    target = pick_version(app, version)
    elf = target.get("elf_sha256")
    for s in dev["slots"]:
        if s["present"] and elf and s["elf_sha256"].hex() == elf:
            where = "slot 0" if s["index"] == TAOS_INDEX else "entry %d" % s["index"]
            if s["index"] != TAOS_INDEX and s["app_id"] == app_id:
                raise OrbError("%s %s is already installed (entry %d)" % (app_id, target["version"], s["index"]))
            raise OrbError("this exact build is already installed in %s (%s)" % (where, s["app_id"] or s["project_name"]))
    entry = None if add else _installed_entry(dev, app_id)
    downgrade = False
    message = "install %s %s" % (app_id, target["version"])
    if entry is not None:
        hit = identify(cat, entry["elf_sha256"], app_id)
        if hit is not None and hit[0] is app:
            vs = app["versions"]
            i_inst, i_tgt = vs.index(hit[1]), vs.index(target)
            if i_tgt > i_inst:                           # the target is older than what is installed
                if version is None:
                    raise OrbError("%s %s is newer than the catalog's latest (%s): name the older version with --version to "
                                   "replace it (downgrade)" % (app_id, hit[1]["version"], target["version"]))
                downgrade = True
                message = "downgrade %s -> %s" % (hit[1]["version"], target["version"])
            else:
                message = "update %s %s -> %s" % (app_id, hit[1]["version"], target["version"])
    return {"app": app, "version": target, "entry": entry, "update": entry is not None, "downgrade": downgrade,
            "message": message}


def _read(path, what):
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError as e:
        raise OrbError("cannot read %s %s: %s" % (what, path, e))


def build_extents(cat, plan):
    """Load the files of the chosen version, verify them against the catalog BEFORE anything is sent, and decide which
    extents travel. On an update, a kept data extent whose label and size match the installed version is carried over
    by the device (blob None): it is not resent and not erased."""
    app, v, entry = plan["app"], plan["version"], plan["entry"]
    old_exts = []
    if plan["update"] and entry is not None:
        hit = identify(cat, entry["elf_sha256"], app["id"])
        if hit is not None and hit[0] is app:
            old_exts = version_extents(hit[1])
    out = []
    for j, e in enumerate(version_extents(v)):
        blob = None
        carried = (j > 0 and e.get("keep") and any(o["label"] == e["label"] and o["blocks"] == e["blocks"] and o.get("keep")
                                                  for o in old_exts[1:]))
        if e.get("file") and not carried:
            blob = _read(e["file"], "extent file")
            want = e.get("sha256") or (v.get("sha256") if j == 0 else None)
            if want and hashlib.sha256(blob).hexdigest() != want:
                raise OrbError("sha256 mismatch for %s: the file is not the one the catalog recorded" % e["file"])
            if j == 0:
                d = check_image(blob, "app")
                if v.get("elf_sha256") and d["elf_sha256"].hex() != v["elf_sha256"]:
                    raise OrbError("elf sha256 of %s does not match the catalog" % e["file"])
            if len(blob) > e["blocks"] * BLOCK:
                raise OrbError("%s is %d bytes, its %d block(s) hold only %d" % (e["file"], len(blob), e["blocks"], e["blocks"] * BLOCK))
        elif j == 0:
            raise OrbError("%s %s has no file for its app extent" % (app["id"], v["version"]))
        out.append(Extent(e["label"], e.get("type", 1), e.get("subtype", 0x82), e["blocks"], 1 if e.get("keep") else 0, blob))
    return out


def plan_updates(cat, dev, which):
    """Which installed entries to bring to the latest version. Pinned entries are skipped (returned in `skipped`). The
    device replaces the first READY entry of an app_id, so an entry shadowed by an earlier one of the same app is never
    targeted, and a pinned first entry blocks the whole app."""
    todo, skipped = [], []
    first = {}
    for s in dev["slots"]:
        if s["index"] != TAOS_INDEX and s["present"] and s["app_id"]:
            first.setdefault(s["app_id"], s)
    for s in dev["slots"]:
        if s["index"] == TAOS_INDEX or not s["present"]:
            continue
        if which is not None and s["index"] != which:
            continue
        hit = identify(cat, s["elf_sha256"], s["app_id"])
        if hit is None:
            continue
        app, inst = hit
        latest = latest_version(app)
        if latest is None or app["versions"].index(inst) <= app["versions"].index(latest):
            continue                                    # up to date, or newer than the catalog
        head = first.get(s["app_id"], s)
        if s["pinned"] or head["pinned"] or head is not s:
            skipped.append(s)
            continue
        todo.append({"entry": s, "app": app, "version": latest})
    return todo, skipped


def fmt_versions(app):
    lines = ["%s (%s)" % (app["name"], app["id"])]
    latest = latest_version(app)
    for v in app["versions"]:
        tags = []
        if v is latest:
            tags.append("latest")
        if v.get("yanked"):
            tags.append("yanked")
        blocks = v.get("blocks") or sum(e["blocks"] for e in version_extents(v))
        lines.append("  %-18s %-10s %d block%s  %-14s %s" % (v["version"], v.get("released", ""), blocks, "" if blocks == 1 else "s",
                                                              ",".join(tags), v.get("notes", "")))
    return "\n".join(lines)


def fmt_list(dev, cat=None):
    lines = []
    for s in dev["slots"]:
        if s["index"] == TAOS_INDEX:
            lines.append("slot 0   %-23s %-18s %-10s sha %s  (%d KiB partition)" % (
                s["display_name"] or "taOS", s["project_name"], s["version"], s["elf_sha256"].hex()[:12], s["size"] // 1024))
            continue
        if not s["present"]:
            continue
        name = s["display_name"] or s["app_id"] or s["project_name"]
        status = ""
        if cat is not None:
            status = "  " + status_of(cat, s)
        elif s["pinned"]:
            status = "  pinned"
        lines.append("entry %-2d %-23s %-18s %-10s %d block%s  sha %s%s" % (
            s["index"], name, s["app_id"] or "-", s["version"], s["blocks"], "" if s["blocks"] == 1 else "s",
            s["elf_sha256"].hex()[:12], status))
    lines.append("%d of %d blocks free" % (dev["free"], dev["total"]))
    return "\n".join(lines)



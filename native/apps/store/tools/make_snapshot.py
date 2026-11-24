#!/usr/bin/env python3
"""Refresh the Store's bundled catalogue snapshot and artwork. Run from anywhere:

  native/apps/store/tools/make_snapshot.py [--no-network] [--covers DIR]

Writes (next to this tool, under native/apps/store/):
  catalog/snapshot.json   the catalogue as GET /api/store/catalog would list it (id, name, type, category, version,
                          description, license, requires, hardware tiers, install method, variants, repo, stars)
  catalog/curated.json    editor's picks, shelf lists, stars, cover and icon index (hand edited; kept if present)
  assets/covers/*.jpg     banner art (webp covers of the web Store, converted: Qt may not ship the webp plugin)
  assets/icons/*.png      app icons (128 px), fetched once from the dashboard-icons set
Needs PyYAML and Pillow. The generated files are committed, so a build never needs the network or this tool.
"""
import argparse, glob, json, os, re, subprocess, sys, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
STORE = os.path.dirname(HERE)
REPO = os.path.abspath(os.path.join(STORE, "..", "..", ".."))
CAT = os.path.join(REPO, "app-catalog")

def curated():
    with open(os.path.join(STORE, "catalog", "curated.json")) as f:
        return json.load(f)

def manifests():
    import yaml
    out = []
    for path in sorted(glob.glob(os.path.join(CAT, "*", "*", "manifest.yaml"))):
        d = yaml.safe_load(open(path)) or {}
        if not d.get("id"):
            continue
        inst = d.get("install") if isinstance(d.get("install"), dict) else {}
        hp = d.get("homepage") or ""
        m = re.match(r"https?://github\.com/([^/]+/[^/#?]+)", hp)
        e = {"id": d["id"], "name": d.get("name", d["id"]), "type": d.get("type", "service"),
             "category": d.get("category", ""), "version": str(d.get("version", "")),
             "description": str(d.get("description", "")).strip(), "license": d.get("license", ""),
             "requires": d.get("requires") or {}, "hardware_tiers": d.get("hardware_tiers") or {},
             "install_method": inst.get("method", ""), "repo": m.group(1) if m else "", "homepage": hp}
        for k in ("weights_license", "license_class"):
            if d.get(k):
                e[k] = d[k]
        if d.get("variants"):
            e["variants"] = [{"id": v.get("id"), "name": v.get("name", v.get("id")), "size_mb": v.get("size_mb", 0),
                              "backend": v.get("backend", [])} for v in d["variants"]]
        out.append(e)
    return out

# covers that are not shipped: hermes is a screenshot of a personal desktop
SKIP_COVERS = {"hermes"}

def covers(src):
    from PIL import Image
    dst = os.path.join(STORE, "assets", "covers")
    os.makedirs(dst, exist_ok=True)
    for p in sorted(glob.glob(os.path.join(src, "*.webp"))):
        if os.path.basename(p)[:-5] in SKIP_COVERS:
            continue
        im = Image.open(p).convert("RGB")
        im.thumbnail((960, 540))
        im.save(os.path.join(dst, os.path.basename(p)[:-5] + ".jpg"), quality=82, optimize=True)

def icons(slugs, network):
    from PIL import Image
    import io
    dst = os.path.join(STORE, "assets", "icons")
    os.makedirs(dst, exist_ok=True)
    for slug in sorted(slugs):
        out = os.path.join(dst, slug + ".png")
        if slug.startswith("webapp-") or os.path.exists(out) or not network:
            continue
        url = f"https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/png/{slug}.png"
        try:
            data = urllib.request.urlopen(url, timeout=20).read()
        except Exception as ex:
            print("icon missing", slug, ex, file=sys.stderr)
            continue
        im = Image.open(io.BytesIO(data)).convert("RGBA")
        im.thumbnail((128, 128))
        im.save(out, optimize=True)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-network", action="store_true")
    ap.add_argument("--covers", default="", help="folder of the web Store's *.webp covers (desktop/public/store-covers)")
    a = ap.parse_args()
    c = curated()
    entries = manifests()
    have = {e["id"] for e in entries}
    for x in c.get("extra", []):
        if x["id"] not in have:
            entries.append(x)
    for e in entries:
        e.update({k: v for k, v in c.get("overrides", {}).get(e["id"], {}).items()})
    def clean(v):
        if isinstance(v, str):
            return v.replace(" \u2014 ", " - ").replace("\u2014", "-").replace("\u2013", "-")
        if isinstance(v, list):
            return [clean(x) for x in v]
        if isinstance(v, dict):
            return {k: clean(x) for k, x in v.items()}
        return v
    entries = clean(entries)
    with open(os.path.join(STORE, "catalog", "snapshot.json"), "w") as f:
        json.dump({"version": 1, "entries": entries}, f, indent=1, ensure_ascii=False)
        f.write("\n")
    if a.covers:
        covers(a.covers)
    icons(set(c["icons"].values()) | {f["icon"] for f in c["iconFamilies"]}, not a.no_network)
    present = {os.path.splitext(os.path.basename(p))[0] for p in glob.glob(os.path.join(STORE, "assets", "icons", "*.png"))}
    svgs = {os.path.splitext(os.path.basename(p))[0] for p in glob.glob(os.path.join(STORE, "assets", "icons", "*.svg"))}
    cov = sorted(os.path.splitext(os.path.basename(p))[0] for p in glob.glob(os.path.join(STORE, "assets", "covers", "*.jpg")))
    idx = {"icons": {k: v for k, v in c["icons"].items() if v in present or v in svgs},
           "iconExt": {k: ("svg" if k in svgs else "png") for k in sorted(present | svgs)},
           "families": [f for f in c["iconFamilies"] if f["icon"] in present], "covers": cov, "coverAlias": c.get("coverAlias", {})}
    with open(os.path.join(STORE, "catalog", "art-index.json"), "w") as f:
        json.dump(idx, f, indent=1)
        f.write("\n")
    print(len(entries), "entries,", len(cov), "covers,", len(idx["iconExt"]), "icons")

main()

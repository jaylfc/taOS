#!/usr/bin/env python3
"""Small JSON/image helpers for e2e-overview.sh (standard library only)."""
import json
import sys


def walk(node):
    yield node
    for k in ("nodes", "floating_nodes"):
        for c in node.get(k, []):
            yield from walk(c)


def windows(tree):
    return [n for n in walk(tree) if n.get("type") in ("con", "floating_con") and n.get("pid")]


def cmd_focused():
    """Prints `<con_id> <title>` of the focused window, or `none`."""
    for n in windows(json.load(sys.stdin)):
        if n.get("focused"):
            print(n["id"], n.get("name", ""))
            return 0
    print("none")
    return 0


def cmd_count():
    print(len(windows(json.load(sys.stdin))))
    return 0


def cmd_workspaces():
    """Prints the workspace names that hold windows, then the number of windows per workspace."""
    tree = json.load(sys.stdin)
    out = {}
    for ws in walk(tree):
        if ws.get("type") == "workspace" and not ws["name"].startswith("__"):
            out[ws["name"]] = len(windows(ws))
    print(" ".join("%s=%d" % kv for kv in sorted(out.items())))
    return 0


def cmd_get(key):
    """Reads JSON on stdin and prints top level `key` (lists and dicts as JSON)."""
    v = json.load(sys.stdin).get(key)
    print(json.dumps(v) if isinstance(v, (list, dict)) else v)
    return 0


def cmd_fps():
    """From the daemon's status JSON: `<n frames-rate entries above 3 fps> <max fps>`."""
    f = json.load(sys.stdin).get("fps", {})
    rates = sorted(f.values(), reverse=True)
    print(sum(1 for r in rates if r > 3.0), "%.1f" % (rates[0] if rates else 0.0))
    return 0


def read_ppm(path):
    with open(path, "rb") as fh:
        data = fh.read()
    parts = data.split(None, 4)
    assert parts[0] == b"P6", "not a P6 ppm"
    w, h = int(parts[1]), int(parts[2])
    return w, h, data[len(data) - w * h * 3:]


def cmd_colorfrac(path, x0, y0, x1, y1, rgb, tol="12"):
    """Fraction of pixels in the rectangle within `tol` of the colour RRGGBB."""
    w, h, px = read_ppm(path)
    want = [int(rgb[i:i + 2], 16) for i in (0, 2, 4)]
    tol = int(tol)
    n = hit = 0
    for y in range(int(y0), min(int(y1), h)):
        row = y * w * 3
        for x in range(int(x0), min(int(x1), w)):
            i = row + x * 3
            n += 1
            if abs(px[i] - want[0]) <= tol and abs(px[i + 1] - want[1]) <= tol and abs(px[i + 2] - want[2]) <= tol:
                hit += 1
    print("%.4f" % (hit / n if n else 0.0))
    return 0


def cmd_distinct(path, x0, y0, x1, y1):
    """Number of distinct colours (quantised to 5 bits) in the rectangle: a flat fill has very few."""
    w, h, px = read_ppm(path)
    seen = set()
    for y in range(int(y0), min(int(y1), h), 2):
        row = y * w * 3
        for x in range(int(x0), min(int(x1), w), 2):
            i = row + x * 3
            seen.add((px[i] >> 3, px[i + 1] >> 3, px[i + 2] >> 3))
    print(len(seen))
    return 0


def main(argv):
    cmds = {"focused": cmd_focused, "count": cmd_count, "workspaces": cmd_workspaces, "get": cmd_get,
            "fps": cmd_fps, "colorfrac": cmd_colorfrac, "distinct": cmd_distinct}
    if len(argv) < 2 or argv[1] not in cmds:
        print("usage: overview_helper.py %s" % "|".join(cmds), file=sys.stderr)
        return 2
    return cmds[argv[1]](*argv[2:])


if __name__ == "__main__":
    sys.exit(main(sys.argv))

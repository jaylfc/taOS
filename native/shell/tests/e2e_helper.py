#!/usr/bin/env python3
"""Small JSON/image helpers for e2e-headless.sh (standard library only)."""
import json
import struct
import sys


def walk(node):
    yield node
    for k in ("nodes", "floating_nodes"):
        for c in node.get(k, []):
            yield from walk(c)


def cmd_ws_rect(output):
    tree = json.load(sys.stdin)
    for n in walk(tree):
        if n.get("type") == "output" and n.get("name") == output:
            for w in walk(n):
                if w.get("type") == "workspace":
                    r = w["rect"]
                    print(r["x"], r["y"], r["width"], r["height"])
                    return 0
    return 1


def cmd_output(output):
    outs = json.load(sys.stdin)
    for o in outs:
        if o.get("name") == output:
            m = o["current_mode"]
            print(m["width"], m["height"], o.get("scale"))
            return 0
    return 1


def cmd_pixel(path, x, y):
    with open(path, "rb") as f:
        data = f.read()
    # P6\nW H\n255\n
    parts = data.split(None, 4)
    assert parts[0] == b"P6", "not a P6 ppm"
    w, h = int(parts[1]), int(parts[2])
    off = len(data) - w * h * 3
    i = off + (int(y) * w + int(x)) * 3
    print(data[i], data[i + 1], data[i + 2])
    return 0


def cmd_png_size(path):
    with open(path, "rb") as f:
        head = f.read(24)
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        return 1
    w, h = struct.unpack(">II", head[16:24])
    print(w, h)
    return 0


def main(argv):
    c = argv[1]
    if c == "ws-rect":
        return cmd_ws_rect(argv[2])
    if c == "output":
        return cmd_output(argv[2])
    if c == "pixel":
        return cmd_pixel(argv[2], argv[3], argv[4])
    if c == "png-size":
        return cmd_png_size(argv[2])
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))

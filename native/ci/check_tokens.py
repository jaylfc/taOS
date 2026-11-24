#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tokens-only gate: no hard-coded colour literal in shell or app QML.

Every colour comes from the shared Style/Look tokens. This scans the QML under
native/apps and native/shell (not tests) for colour literals: "#rgb" style
strings, "rgb(" / "rgba(" strings, Qt.rgba/hsla/hsva with a numeric first
argument, and a named colour (other than transparent) assigned to a colour
property. The theme/style definition files are allowed (ALLOW below; native/qt
is the Style singleton and is not scanned).

Today's tree has violations. tokens-baseline.json records the count per file;
the gate fails only when a file has MORE than its baseline (a new file has a
baseline of 0). Fixing violations never fails; lower the baseline with
--update in the same PR so they cannot creep back.

  native/ci/check_tokens.py            check
  native/ci/check_tokens.py --update   rewrite the baseline from the tree
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
NATIVE = os.path.dirname(HERE)
BASELINE = os.path.join(HERE, "tokens-baseline.json")
ROOTS = ("apps", "shell")
# Token / palette definition files: they are where colours are allowed to live.
ALLOW = {
    "apps/common/qml/Taos/Apps/Look.qml",   # the apps' semantic palette over Taos.Style
    "apps/activity/qml/Pal.qml",            # Activity's status palette over Taos.Style
}
SKIP_DIRS = {"tests", "build", "node_modules"}

STR = r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\''
PATTERNS = [
    re.compile(r'["\']\s*#[0-9A-Fa-f]{3,8}\s*["\']'),
    re.compile(r'["\']\s*rgba?\('),
    re.compile(r'\bQt\.(?:rgba|hsla|hsva)\(\s*[0-9.]'),
    re.compile(r'\b[A-Za-z.]*[cC]olor\s*:\s*["\'](?!transparent["\'])[A-Za-z]+["\']'),
]


def code_of(line):
    """The line without its // comment (a // inside a string is kept)."""
    out, i, q = [], 0, None
    while i < len(line):
        c = line[i]
        if q:
            out.append(c)
            if c == "\\" and i + 1 < len(line):
                out.append(line[i + 1]); i += 1
            elif c == q:
                q = None
        elif c in "\"'":
            q = c; out.append(c)
        elif c == "/" and line[i:i + 2] == "//":
            break
        else:
            out.append(c)
        i += 1
    return "".join(out)


def scan():
    found = {}
    for root in ROOTS:
        for d, dirs, files in os.walk(os.path.join(NATIVE, root)):
            dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
            for f in files:
                if not f.endswith(".qml"):
                    continue
                p = os.path.join(d, f)
                rel = os.path.relpath(p, NATIVE)
                if rel in ALLOW:
                    continue
                hits = []
                in_block = False
                with open(p, encoding="utf-8") as fh:
                    for n, raw in enumerate(fh, 1):
                        line = raw
                        if in_block:
                            if "*/" not in line:
                                continue
                            line = line.split("*/", 1)[1]; in_block = False
                        if "/*" in line and "*/" not in line.split("/*", 1)[1]:
                            line = line.split("/*", 1)[0]; in_block = True
                        code = code_of(line)
                        if any(r.search(code) for r in PATTERNS):
                            hits.append((n, raw.strip()))
                if hits:
                    found[rel] = hits
    return found


def main():
    found = scan()
    counts = {k: len(v) for k, v in sorted(found.items())}
    if "--update" in sys.argv:
        with open(BASELINE, "w", encoding="utf-8") as fh:
            json.dump(counts, fh, indent=1, sort_keys=True); fh.write("\n")
        print("baseline written: %d violation(s) in %d file(s)" % (sum(counts.values()), len(counts)))
        return 0
    with open(BASELINE, encoding="utf-8") as fh:
        base = json.load(fh)
    bad, better = [], []
    for rel, hits in sorted(found.items()):
        if len(hits) > base.get(rel, 0):
            bad.append((rel, hits))
    for rel, n in base.items():
        if counts.get(rel, 0) < n:
            better.append(rel)
    total = sum(counts.values())
    print("tokens: %d hard-coded colour literal(s) in %d file(s) (baseline %d)" % (total, len(counts), sum(base.values())))
    for rel in better:
        print("  note: %s improved (%d -> %d); run native/ci/check_tokens.py --update to lock it in" % (rel, base[rel], counts.get(rel, 0)))
    if bad:
        print("\nFAIL: new hard-coded colours. Use a Style/Look token, or add a token for all themes.")
        for rel, hits in bad:
            print("  %s: %d now, baseline %d" % (rel, len(hits), base.get(rel, 0)))
            for n, txt in hits:
                print("    %s:%d: %s" % (rel, n, txt[:140]))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

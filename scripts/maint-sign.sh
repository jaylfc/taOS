#!/usr/bin/env bash
# Release-side tool for the signed maintenance manifest (maint/manifest.json).
# Run by the person who holds the taOS release signing key, never by an
# installed system.
#
#   scripts/maint-sign.sh build          regenerate maint/manifest.json from maint/steps/
#   scripts/maint-sign.sh sign <key-id>  detached-sign it: maint/manifest.json.sig
#   scripts/maint-sign.sh check          confirm manifest.json matches maint/steps/ (CI)
#
# `build` raises seq by one when any step's bytes or version changed. A manifest
# with a lower seq than a machine has already seen is refused there (replay).
# The signing key's FULL fingerprint must be in maint/release.fpr, and its public
# key (gpg --export <fpr> > maint/release.gpg) in maint/release.gpg.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
mode="${1:-}"

gen() {
    python3 -I - "$1" <<'PY'
import hashlib, json, re, sys
from pathlib import Path
prev = {}
try:
    prev = json.loads(Path("maint/manifest.json").read_text())
except Exception:
    pass
steps = []
for f in sorted(Path("maint/steps").glob("*.sh")):
    body = f.read_bytes()
    m = re.search(rb"^# TAOS-MAINT-STEP-VERSION: (\d+)$", body, re.M)
    if not m:
        sys.exit(f"{f}: missing '# TAOS-MAINT-STEP-VERSION: N' line")
    steps.append({"id": f.stem, "version": int(m[1]), "file": str(f),
                  "sha256": hashlib.sha256(body).hexdigest()})
new = {"format": 1, "min_runner": 1, "seq": prev.get("seq", 0), "steps": steps}
if prev.get("steps") != steps:
    new["seq"] = prev.get("seq", 0) + 1
out = json.dumps(new, indent=1, sort_keys=True) + "\n"
if sys.argv[1] == "check":
    sys.exit(0 if Path("maint/manifest.json").read_text() == out else "maint/manifest.json is stale: run scripts/maint-sign.sh build")
Path("maint/manifest.json").write_text(out)
print(f"manifest seq {new['seq']}, {len(steps)} step(s)")
PY
}

case "$mode" in
    build) gen build ;;
    check) gen check ;;
    sign)
        key="${2:?usage: maint-sign.sh sign <key-id>}"
        gen check
        gpg --batch --yes --local-user "$key" --detach-sign -o maint/manifest.json.sig maint/manifest.json
        gpg --batch --verify maint/manifest.json.sig maint/manifest.json
        ;;
    *) echo "usage: $0 build|check|sign <key-id>" >&2; exit 64 ;;
esac

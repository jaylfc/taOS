#!/bin/bash
# Maintenance step qmd-upgrade-2.8.3.
# TAOS-MAINT-STEP-VERSION: 1
#
# Run ONLY by /usr/local/sbin/taos-maint, as root, from a root-only staged copy
# whose sha256 is listed in the release-signed manifest. Never run from the git
# tree. Verbs: check | apply | verify | rollback.
#
# Brings the host memory service (qmd) to the release that keeps one index per
# caller apart, confines the index paths it will open to the taOS data directory
# and, unless the owner chose otherwise, makes it listen on this machine only.
#
# Inputs come from the runner (root-owned maint.conf), never from the caller:
#   TAOS_DATA_DIR  TAOS_QMD_PORT  TAOS_ROOT_PREFIX  TAOS_STATE_DIR  TAOS_CONF_DIR
set -euo pipefail

PIN="2.8.3-taos.2"
# sha512 (npm integrity string) of the published tarball @jaylfc/qmd@2.8.3-taos.2.
PIN_INTEGRITY="sha512-2aOuVMaB8/MBsuG0Lm3FsljpPH6i4UqZyfh4FZaCjG3pkpJ8Kw/j+sBzT1rKNUJjY41yZYDRuQgnOI05UB6GGw=="

ROOT="${TAOS_ROOT_PREFIX:-}"
DEFAULTS="$ROOT/etc/default/qmd"
UNIT="$ROOT/etc/systemd/system/qmd.service"
PORT="${TAOS_QMD_PORT:-7832}"
BACKUP_DIR="$TAOS_STATE_DIR/qmd-upgrade-2.8.3"
KEEP_OPEN="$TAOS_CONF_DIR/qmd-keep-open"   # owner-created, root-owned: leave QMD_BIND alone

say() { printf '[qmd-upgrade-2.8.3] %s\n' "$*"; }

# probe: the exact test the controller uses (qmd_service._is_new_qmd_answer):
# POST /delete-chunk {} answers 400 + JSON {"error": "<text>"} on a qmd that
# honours dbPath, 404 on one that does not.
probe_new_qmd() {
    python3 -I - "$PORT" <<'PY'
import json, sys, urllib.error, urllib.request
req = urllib.request.Request(f"http://127.0.0.1:{sys.argv[1]}/delete-chunk", data=b"{}",
                             headers={"content-type": "application/json"}, method="POST")
try:
    urllib.request.urlopen(req, timeout=3)
    sys.exit(1)
except urllib.error.HTTPError as e:
    try:
        body = json.loads(e.read())
    except Exception:
        sys.exit(1)
    sys.exit(0 if e.code == 400 and isinstance(body, dict) and isinstance(body.get("error"), str) and body["error"] else 1)
except Exception:
    sys.exit(1)
PY
}

installed_version() {
    local dir
    dir="$(npm root -g 2>/dev/null)/@jaylfc/qmd/package.json"
    [[ -f "$dir" ]] || return 0
    python3 -I -c 'import json,sys; print(json.load(open(sys.argv[1])).get("version",""))' "$dir" 2>/dev/null || true
}

# 0 when the installed version is the pin or newer, or is not a version we
# recognise (never downgrade or overwrite something we do not understand).
version_satisfied() {
    python3 -I - "$1" "$PIN" <<'PY'
import re, sys
def key(v):
    m = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:-taos\.(\d+))?", v)
    return None if not m else (int(m[1]), int(m[2]), int(m[3]), int(m[4]) if m[4] else -1)
have, want = key(sys.argv[1]), key(sys.argv[2])
sys.exit(0 if have is None or have >= want else 1)
PY
}

get_default() { [[ -f "$DEFAULTS" ]] && sed -n "s/^$1=//p" "$DEFAULTS" | tail -1 || true; }

set_default() {  # replace or append KEY=VALUE in /etc/default/qmd
    touch "$DEFAULTS"
    if grep -qE "^$1=" "$DEFAULTS"; then
        sed -i "s|^$1=.*|$1=$2|" "$DEFAULTS"
    else
        printf '%s=%s\n' "$1" "$2" >> "$DEFAULTS"
    fi
}

get_unit() { [[ -f "$UNIT" ]] && sed -n "s/^Environment=\"\{0,1\}$1=\([^\" ]*\).*/\1/p" "$UNIT" | tail -1 || true; }

# 0 when this step should write QMD_BIND=127.0.0.1: the owner has set no bind in
# /etc/default/qmd, has not asked to keep it open, and the unit does not already
# listen on this machine only (units installed before this release say 0.0.0.0).
bind_wanted() {
    [[ -z "$(get_default QMD_BIND)" && ! -e "$KEEP_OPEN" && "$(get_unit QMD_BIND)" != "127.0.0.1" ]]
}

# 0 when qmd is already confined to the data directory, by the defaults file
# (which wins) or by the unit a current installer wrote.
db_root_set() {
    local have
    have="$(get_default QMD_SERVE_DB_ROOT)"
    [[ -n "$have" ]] || have="$(get_unit QMD_SERVE_DB_ROOT)"
    [[ "$have" == "$TAOS_DATA_DIR" ]]
}

check() {
    local have
    have="$(installed_version)"
    [[ -n "$have" ]] || { say "qmd package not found"; return 1; }
    version_satisfied "$have" || { say "installed $have is older than $PIN"; return 1; }
    db_root_set || { say "QMD_SERVE_DB_ROOT not set"; return 1; }
    if bind_wanted; then say "QMD_BIND not set"; return 1; fi
    probe_new_qmd || { say "running qmd is not the new release"; return 1; }
    return 0
}

apply() {
    command -v npm >/dev/null 2>&1 || { say "npm not found"; return 1; }
    mkdir -p "$BACKUP_DIR"
    chmod 700 "$BACKUP_DIR"
    if [[ -f "$DEFAULTS" ]]; then cp -p "$DEFAULTS" "$BACKUP_DIR/qmd.defaults"; else : > "$BACKUP_DIR/qmd.absent"; fi
    installed_version > "$BACKUP_DIR/prev-version"

    local work tarball got
    work="$(mktemp -d)"
    trap 'rm -rf "$work"' RETURN
    say "fetching @jaylfc/qmd@${PIN}"
    ( cd "$work" && npm pack "@jaylfc/qmd@${PIN}" --pack-destination "$work" >/dev/null )
    tarball="$(ls "$work"/*.tgz | head -1)"
    got="sha512-$(openssl dgst -sha512 -binary "$tarball" | base64 -w0)"
    if [[ "$got" != "$PIN_INTEGRITY" ]]; then
        say "tarball integrity mismatch: refusing to install"
        return 1
    fi
    say "tarball matches the pinned integrity; installing"
    ( cd "$work" && npm install -g --allow-scripts='better-sqlite3,node-llama-cpp,tree-sitter-*' "$tarball" )
    set_default QMD_SERVE_DB_ROOT "$TAOS_DATA_DIR"
    if bind_wanted; then
        set_default QMD_BIND 127.0.0.1
    else
        say "leaving QMD_BIND as the owner set it"
    fi
    systemctl restart qmd
    # qmd needs a moment to listen; verify retries.
}

verify() {
    local i
    for i in $(seq 1 30); do
        if probe_new_qmd; then
            if ! systemctl is-active --quiet qmd; then say "qmd is not active"; return 1; fi
            say "qmd answers as the new release"
            return 0
        fi
        sleep 1
    done
    say "qmd did not answer as the new release within 30s"
    return 1
}

rollback() {
    # Put the previous defaults back; reinstalling the previous qmd is best effort
    # (a failed apply that never reached npm install changed nothing there).
    if [[ -f "$BACKUP_DIR/qmd.defaults" ]]; then
        cp -p "$BACKUP_DIR/qmd.defaults" "$DEFAULTS"
    elif [[ -f "$BACKUP_DIR/qmd.absent" ]]; then
        rm -f "$DEFAULTS"
    fi
    local prev
    prev="$(cat "$BACKUP_DIR/prev-version" 2>/dev/null || true)"
    if [[ -n "$prev" && "$(installed_version)" != "$prev" ]]; then
        say "restoring qmd $prev"
        ( cd / && npm install -g "@jaylfc/qmd@$prev" ) || say "could not restore qmd $prev"
    fi
    systemctl restart qmd || true
}

case "${1:-}" in
    check) check ;;
    apply) apply ;;
    verify) verify ;;
    rollback) rollback ;;
    *) echo "usage: $0 check|apply|verify|rollback" >&2; exit 64 ;;
esac

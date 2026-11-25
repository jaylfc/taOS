#!/usr/bin/env bash
# Upgrade the host qmd service on an EXISTING install.
#
# taOS's own updater runs as an unprivileged account and cannot touch the
# root-installed global npm package or the systemd units, so this one step is
# run by the machine owner:
#
#     sudo bash scripts/upgrade-qmd.sh
#
# It installs the qmd release that honours per-request dbPath (needed to keep
# each agent's and each person's memory apart), confines dbPath to the taOS data
# directory, and restarts qmd. Existing index files are kept as they are.
#
#   TAOS_QMD_NPM_VERSION   qmd version to install (default below)
#   TAOS_DATA_DIR          taOS data directory (default: <repo>/data)
#   --local-only           also make qmd listen on 127.0.0.1 only
set -euo pipefail

PIN="${TAOS_QMD_NPM_VERSION:-2.8.3-taos.2}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="${TAOS_DATA_DIR:-$HERE/../data}"
DEFAULTS=/etc/default/qmd
LOCAL_ONLY=0
[[ "${1:-}" == "--local-only" ]] && LOCAL_ONLY=1

if [[ "$(id -u)" != "0" ]]; then
    echo "run as root: sudo bash $0" >&2
    exit 1
fi
command -v npm >/dev/null 2>&1 || { echo "npm not found" >&2; exit 1; }
DATA_DIR="$(cd "$DATA_DIR" && pwd)"

set_default() {  # set_default KEY VALUE: replace or append in /etc/default/qmd
    touch "$DEFAULTS"
    if grep -qE "^$1=" "$DEFAULTS"; then
        sed -i "s|^$1=.*|$1=$2|" "$DEFAULTS"
    else
        printf '%s=%s\n' "$1" "$2" >> "$DEFAULTS"
    fi
}

echo "installing @jaylfc/qmd@${PIN}"
( cd /tmp && HOME=/root npm install -g --allow-scripts=better-sqlite3,node-llama-cpp,tree-sitter-* "@jaylfc/qmd@${PIN}" )
set_default QMD_SERVE_DB_ROOT "$DATA_DIR"
[[ "$LOCAL_ONLY" == "1" ]] && set_default QMD_BIND 127.0.0.1
systemctl restart qmd
echo "qmd upgraded to ${PIN}; dbPath confined to ${DATA_DIR}"

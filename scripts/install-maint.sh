#!/usr/bin/env bash
# Install the root maintenance runner (taos-maint) on a Linux/systemd host.
#
# Run as root, once, by the machine owner (or by install-server.sh, which calls it):
#
#     sudo bash scripts/install-maint.sh
#
# After this, taOS updates apply their signed maintenance steps (for example the
# qmd upgrade) by themselves; nothing here needs to be run again.
#
#   --tree DIR        taOS install directory (default: the running tinyagentos unit's)
#   --data DIR        taOS data directory     (default: <tree>/data)
#   --user NAME       the controller's service account (default: the unit's User=)
#   --from-dir DIR    take taos-maint, the release key and the units from DIR
#                     (default: <tree>); the files are copied, never run from there
#   --fetch REF       take those files from the project's release REF over HTTPS
#                     instead of the install directory. Use this for the one-time
#                     setup on an existing install: the install directory is
#                     writable by the service account, so the runner and the
#                     release key must not come from it.
#   --no-run          install only; do not run pending steps now
#
# What it installs, all root-owned and not writable by the service account:
#   /usr/local/sbin/taos-maint            the runner
#   /etc/taos/maint/maint.conf            tree, data dir, service account, qmd port
#   /etc/taos/maint/release.gpg|.fpr      the release public key and fingerprint
#   /etc/systemd/system/taos-maint.{service,path}
#   /var/lib/taos-maint/                  state (root) and trigger/ (service account may add *.req)
set -euo pipefail

die() { echo "install-maint: $*" >&2; exit 1; }
log() { echo "install-maint: $*"; }

[[ "$(id -u)" == "0" ]] || die "run as root: sudo bash $0"
[[ "$(uname -s)" == "Linux" ]] || die "Linux only"
command -v systemctl >/dev/null 2>&1 || die "systemd is required"
[[ -d /run/systemd/system ]] || die "systemd is not running"

TREE="" DATA="" SVC_USER="" FROM="" FETCH="" RUN_NOW=1
while [[ $# -gt 0 ]]; do
    case "$1" in
        --tree) TREE="${2:?}"; shift 2 ;;
        --data) DATA="${2:?}"; shift 2 ;;
        --user) SVC_USER="${2:?}"; shift 2 ;;
        --from-dir) FROM="${2:?}"; shift 2 ;;
        --fetch) FETCH="${2:?}"; shift 2 ;;
        --no-run) RUN_NOW=0; shift ;;
        *) die "unknown argument $1" ;;
    esac
done

if [[ -z "$TREE" ]]; then
    TREE="$(systemctl show tinyagentos -p WorkingDirectory --value 2>/dev/null || true)"
fi
[[ -n "$TREE" && -d "$TREE/maint" ]] || die "cannot find the taOS install directory; pass --tree DIR"
TREE="$(cd "$TREE" && pwd -P)"
DATA="${DATA:-$TREE/data}"
if [[ -z "$SVC_USER" ]]; then
    SVC_USER="$(systemctl show tinyagentos -p User --value 2>/dev/null || true)"
fi
SVC_USER="${SVC_USER:-taos}"
if [[ -n "$FETCH" ]]; then
    [[ "$FETCH" =~ ^[A-Za-z0-9._/-]+$ ]] || die "bad ref"
    command -v curl >/dev/null 2>&1 || die "curl is required for --fetch"
    FROM="$(mktemp -d)"
    trap 'rm -rf "$FROM"' EXIT
    base="https://raw.githubusercontent.com/jaylfc/taOS/$FETCH"
    mkdir -p "$FROM/scripts/systemd" "$FROM/maint"
    for f in scripts/taos-maint scripts/systemd/taos-maint.service scripts/systemd/taos-maint.path maint/release.gpg maint/release.fpr; do
        curl -fsSL --proto '=https' --tlsv1.2 "$base/$f" -o "$FROM/$f" || die "could not fetch $f from $base"
    done
fi
FROM="$(cd "${FROM:-$TREE}" && pwd -P)"
QMD_PORT="${TAOS_QMD_PORT:-7832}"

for v in "$TREE" "$DATA" "$SVC_USER" "$QMD_PORT"; do
    [[ "$v" != *[[:cntrl:]]* ]] || die "control character in an argument"
done
[[ "$TREE" == /* && "$DATA" == /* ]] || die "paths must be absolute"
[[ "$SVC_USER" =~ ^[a-z_][a-z0-9_-]*$ ]] || die "bad service account name"
[[ "$QMD_PORT" =~ ^[0-9]{1,5}$ ]] || die "bad qmd port"
id -u "$SVC_USER" >/dev/null 2>&1 || die "service account $SVC_USER does not exist"
SVC_GROUP="$(id -gn "$SVC_USER")"

# A placeholder or malformed release key must never be installed.
grep -qE '^[0-9A-F]{40,64}$' "$FROM/maint/release.fpr" || die "maint/release.fpr has no real fingerprint: this build carries no release key"
[[ -s "$FROM/maint/release.gpg" ]] && ! grep -q REPLACE-WITH "$FROM/maint/release.gpg" || die "maint/release.gpg is a placeholder"
[[ -f "$FROM/scripts/taos-maint" ]] || die "scripts/taos-maint missing in $FROM"
[[ -x /usr/bin/python3 ]] && [[ -x /usr/bin/gpgv || -x /usr/bin/gpgv2 ]] || die "python3 and gpgv are required (apt install gpgv python3)"
/usr/bin/python3 -I -m py_compile "$FROM/scripts/taos-maint" || die "runner does not compile"

install -d -m 0755 -o root -g root /etc/taos /etc/taos/maint /var/lib/taos-maint
install -m 0755 -o root -g root "$FROM/scripts/taos-maint" /usr/local/sbin/taos-maint
install -m 0644 -o root -g root "$FROM/maint/release.gpg" /etc/taos/maint/release.gpg
install -m 0644 -o root -g root "$FROM/maint/release.fpr" /etc/taos/maint/release.fpr
{
    echo "# written by install-maint.sh; root-owned. Edit only as root."
    echo "TREE=$TREE"
    echo "DATA_DIR=$DATA"
    echo "SERVICE_USER=$SVC_USER"
    echo "QMD_PORT=$QMD_PORT"
} > /etc/taos/maint/maint.conf.new
chmod 0644 /etc/taos/maint/maint.conf.new
chown root:root /etc/taos/maint/maint.conf.new
mv -f /etc/taos/maint/maint.conf.new /etc/taos/maint/maint.conf
# trigger/: the service account may create *.req here and nothing else matters.
install -d -m 0770 -o root -g "$SVC_GROUP" /var/lib/taos-maint/trigger
install -m 0644 -o root -g root "$FROM/scripts/systemd/taos-maint.service" /etc/systemd/system/taos-maint.service
install -m 0644 -o root -g root "$FROM/scripts/systemd/taos-maint.path" /etc/systemd/system/taos-maint.path
systemctl daemon-reload
systemctl enable --now taos-maint.path
log "installed taos-maint for tree $TREE (service account $SVC_USER)"

# Replay floor first (needs a valid signature on the shipped manifest), then run.
/usr/local/sbin/taos-maint init || die "the maintenance manifest in $TREE is not validly signed by the release key"
if [[ "$RUN_NOW" == "1" ]]; then
    /usr/local/sbin/taos-maint run-pending || log "some steps failed: see 'taos-maint status' and journalctl -u taos-maint"
fi
log "done"

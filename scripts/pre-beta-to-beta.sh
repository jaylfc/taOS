#!/usr/bin/env bash
# pre-beta-to-beta.sh — migrate a root-based pre-beta taOS install to the
# non-root 'taos' beta layout introduced in PR #639 / #677.
#
# Run this AFTER re-installing taOS with the beta installer so the new
# install dir and systemd unit are already in place.  The script then copies
# your old data across, fixes ownership/permissions, and patches the service
# unit if it still says User=root.
#
# Usage (as root or via sudo):
#   sudo bash scripts/pre-beta-to-beta.sh [--yes] \
#         [OLD_TAOS_DIR=/root/tinyagentos] [NEW_TAOS_DIR=/opt/tinyagentos]
#
# Environment overrides (can also be set before calling the script):
#   OLD_TAOS_DIR   path of the pre-beta install containing a data/ subdirectory
#   NEW_TAOS_DIR   path of the freshly-installed beta install
#
# Safety properties:
#   - Refuses to run unless EUID=0.
#   - Never overwrites NEW/data without first taking a timestamped tarball backup.
#   - Never deletes or modifies the OLD install — it is left entirely intact.
#   - Fails loudly if OLD or NEW cannot be determined unambiguously; does NOT guess.
#   - All destructive steps require confirmation unless --yes is passed.
#   - Idempotent: safe to re-run; backup is taken each time if data is already present.

set -euo pipefail

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

log()  { printf '\033[1;34m[pre-beta-mig]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[pre-beta-mig]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[pre-beta-mig]\033[0m %s\n' "$*" >&2; exit 1; }
ok()   { printf '\033[1;32m[pre-beta-mig]\033[0m %s\n' "$*"; }

# ---------------------------------------------------------------------------
# argument / flag parsing
# ---------------------------------------------------------------------------

CONFIRM_YES=0

usage() {
    cat <<EOF
Usage: sudo bash $0 [--yes] [OLD_TAOS_DIR=<path>] [NEW_TAOS_DIR=<path>]

Migrates a root-based pre-beta taOS install to the non-root 'taos' beta layout.
Run AFTER the beta installer has been applied.

Options:
  --yes               skip the interactive confirmation prompt
  OLD_TAOS_DIR=PATH   pre-beta install directory (must contain a data/ subdir)
  NEW_TAOS_DIR=PATH   beta install directory (installed by the beta installer)
  -h, --help          show this help

Environment variables OLD_TAOS_DIR and NEW_TAOS_DIR may also be set before
calling the script instead of passing them as KEY=VALUE arguments.
EOF
}

for arg in "$@"; do
    case "$arg" in
        -h|--help)    usage; exit 0 ;;
        --yes)        CONFIRM_YES=1 ;;
        OLD_TAOS_DIR=*) OLD_TAOS_DIR="${arg#OLD_TAOS_DIR=}" ;;
        NEW_TAOS_DIR=*) NEW_TAOS_DIR="${arg#NEW_TAOS_DIR=}" ;;
        *) die "unknown argument: $arg  (run with -h for usage)" ;;
    esac
done

# ---------------------------------------------------------------------------
# guard: must run as root
# ---------------------------------------------------------------------------

if [[ "${EUID:-$(id -u)}" != "0" ]]; then
    die "this script must be run as root (use: sudo bash $0)"
fi

# ---------------------------------------------------------------------------
# auto-detect NEW_TAOS_DIR
# ---------------------------------------------------------------------------

if [[ -z "${NEW_TAOS_DIR:-}" ]]; then
    # Try the running systemd unit's WorkingDirectory first.
    _unit_wd=""
    if command -v systemctl >/dev/null 2>&1; then
        _unit_wd=$(systemctl show -p WorkingDirectory tinyagentos 2>/dev/null \
                   | sed 's/^WorkingDirectory=//' | grep -v '^$' || true)
    fi

    if [[ -n "$_unit_wd" && -d "$_unit_wd" ]]; then
        NEW_TAOS_DIR="$_unit_wd"
        log "auto-detected NEW_TAOS_DIR from systemd unit: $NEW_TAOS_DIR"
    elif [[ -d /opt/tinyagentos ]]; then
        NEW_TAOS_DIR="/opt/tinyagentos"
        log "auto-detected NEW_TAOS_DIR from default location: $NEW_TAOS_DIR"
    else
        die "cannot determine NEW_TAOS_DIR — pass it explicitly: NEW_TAOS_DIR=/opt/tinyagentos"
    fi
fi

# Normalise (remove trailing slash).
NEW_TAOS_DIR="${NEW_TAOS_DIR%/}"

[[ -d "$NEW_TAOS_DIR" ]] || die "NEW_TAOS_DIR '$NEW_TAOS_DIR' does not exist"

# ---------------------------------------------------------------------------
# auto-detect OLD_TAOS_DIR
# ---------------------------------------------------------------------------

if [[ -z "${OLD_TAOS_DIR:-}" ]]; then
    # Scan common pre-beta locations for a data/ directory that differs from NEW.
    _candidates=()
    for candidate in /root/tinyagentos /home/*/tinyagentos; do
        # Skip if it is (or resolves to) the same path as NEW.
        [[ -d "$candidate" ]] || continue
        [[ "$(realpath "$candidate")" == "$(realpath "$NEW_TAOS_DIR")" ]] && continue
        # A valid pre-beta install must have a data/ directory.
        [[ -d "$candidate/data" ]] || continue
        _candidates+=("$candidate")
    done

    if [[ "${#_candidates[@]}" -eq 1 ]]; then
        OLD_TAOS_DIR="${_candidates[0]}"
        log "auto-detected OLD_TAOS_DIR: $OLD_TAOS_DIR"
    elif [[ "${#_candidates[@]}" -eq 0 ]]; then
        die "no pre-beta install found in /root/tinyagentos or /home/*/tinyagentos
     Pass OLD_TAOS_DIR explicitly: OLD_TAOS_DIR=/root/tinyagentos"
    else
        # More than one candidate — refuse to guess destructively.
        warn "multiple pre-beta installs found:"
        for c in "${_candidates[@]}"; do warn "  $c"; done
        die "cannot determine which is OLD — pass it explicitly:
     OLD_TAOS_DIR=<path>  (e.g. OLD_TAOS_DIR=/root/tinyagentos)"
    fi
fi

OLD_TAOS_DIR="${OLD_TAOS_DIR%/}"

[[ -d "$OLD_TAOS_DIR" ]] || die "OLD_TAOS_DIR '$OLD_TAOS_DIR' does not exist"
[[ -d "$OLD_TAOS_DIR/data" ]] \
    || die "OLD_TAOS_DIR '$OLD_TAOS_DIR' has no data/ subdirectory — is this the right path?"
[[ "$(realpath "$OLD_TAOS_DIR")" != "$(realpath "$NEW_TAOS_DIR")" ]] \
    || die "OLD_TAOS_DIR and NEW_TAOS_DIR resolve to the same path ('$OLD_TAOS_DIR') — nothing to do"

# ---------------------------------------------------------------------------
# confirmation
# ---------------------------------------------------------------------------

BACKUP_TS=$(date +%Y%m%d-%H%M%S)
BACKUP_PATH="$NEW_TAOS_DIR/data.pre-migration-backup.$BACKUP_TS.tgz"

log ""
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
log "  Pre-beta → Beta migration plan"
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
log "  OLD install : $OLD_TAOS_DIR"
log "  NEW install : $NEW_TAOS_DIR"
log ""
log "  Steps:"
log "  1. Stop tinyagentos.service"
log "  2. Backup NEW data dir → $BACKUP_PATH"
log "     (only if NEW/data/ already has content)"
log "  3. Copy OLD/data/ → NEW/data/  (cp -a, preserving timestamps)"
log "  4. Ensure 'taos' system user exists + has incus/docker group membership"
log "  5. chown -R taos:taos NEW/  (whole install dir)  +  chmod 0700 data/  +  chmod 0600 secrets"
log "  6. Patch tinyagentos.service to User=taos if it still says User=root"
log "  7. systemctl daemon-reload && systemctl start tinyagentos"
log "  8. Verify service is active and running as 'taos'"
log ""
log "  The OLD install is NEVER modified or deleted."
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
log ""

if [[ "$CONFIRM_YES" -eq 0 ]]; then
    printf '\033[1;33m[pre-beta-mig]\033[0m Proceed? [y/N] '
    read -r _reply
    case "$_reply" in
        y|Y|yes|YES) ;;
        *) die "aborted by user" ;;
    esac
fi

# ---------------------------------------------------------------------------
# step 1 — stop the service
# ---------------------------------------------------------------------------

log "[1/8] stopping tinyagentos.service..."
if command -v systemctl >/dev/null 2>&1; then
    systemctl stop tinyagentos 2>/dev/null || true
    log "  service stopped (or was not running)"
else
    warn "  systemctl not available — skipping service stop"
fi

# ---------------------------------------------------------------------------
# step 2 — backup NEW data dir if it has content
# ---------------------------------------------------------------------------

log "[2/8] checking for existing content in $NEW_TAOS_DIR/data/ ..."
mkdir -p "$NEW_TAOS_DIR/data"

_new_data_empty=1
if [[ -n "$(ls -A "$NEW_TAOS_DIR/data" 2>/dev/null)" ]]; then
    _new_data_empty=0
fi

if [[ "$_new_data_empty" -eq 0 ]]; then
    log "  NEW/data/ has content — creating backup at:"
    log "  $BACKUP_PATH"
    tar -czf "$BACKUP_PATH" -C "$NEW_TAOS_DIR" data \
        || die "backup failed — aborting (NEW/data/ is untouched)"
    log "  backup created: $BACKUP_PATH"
else
    log "  NEW/data/ is empty — skipping backup"
fi

# ---------------------------------------------------------------------------
# step 3 — copy OLD data → NEW data
# ---------------------------------------------------------------------------

log "[3/8] copying $OLD_TAOS_DIR/data/ → $NEW_TAOS_DIR/data/ ..."
# cp -a: archive mode (preserves perms, timestamps, symlinks, ownership).
# Trailing /. copies the CONTENTS of data/, not the directory itself.
#
# Preserve NEW install's per-install credentials before the bulk copy so the
# OLD install's DB password / master key does not clobber them.  LiteLLM
# cannot start if .litellm_db_url carries the wrong postgres password.
_saved_litellm_db_url=""
_saved_litellm_master_key=""
[[ -f "$NEW_TAOS_DIR/data/.litellm_db_url" ]] \
    && _saved_litellm_db_url="$(cat "$NEW_TAOS_DIR/data/.litellm_db_url")"
[[ -f "$NEW_TAOS_DIR/data/.litellm_master_key" ]] \
    && _saved_litellm_master_key="$(cat "$NEW_TAOS_DIR/data/.litellm_master_key")"

cp -a "$OLD_TAOS_DIR/data/." "$NEW_TAOS_DIR/data/"

# Restore the NEW install's credentials, overriding whatever was copied.
if [[ -n "$_saved_litellm_db_url" ]]; then
    printf '%s' "$_saved_litellm_db_url" > "$NEW_TAOS_DIR/data/.litellm_db_url"
    log "  restored NEW install's .litellm_db_url (postgres password)"
fi
if [[ -n "$_saved_litellm_master_key" ]]; then
    printf '%s' "$_saved_litellm_master_key" > "$NEW_TAOS_DIR/data/.litellm_master_key"
    log "  restored NEW install's .litellm_master_key"
fi

# Also carry the top-level trace/ directory if it lives separately from data/
# (pre-beta versions sometimes placed it at the install root).
if [[ -d "$OLD_TAOS_DIR/trace" ]]; then
    log "  detected separate trace/ dir — copying to $NEW_TAOS_DIR/data/trace/"
    mkdir -p "$NEW_TAOS_DIR/data/trace"
    cp -a "$OLD_TAOS_DIR/trace/." "$NEW_TAOS_DIR/data/trace/"
fi

log "  copy complete"

# ---------------------------------------------------------------------------
# step 4 — ensure taos user + group memberships
# ---------------------------------------------------------------------------

log "[4/8] ensuring 'taos' system user exists..."

_os_name="$(uname -s)"

if [[ "$_os_name" == "Linux" ]]; then
    if ! id -u taos >/dev/null 2>&1; then
        log "  creating system user 'taos'"
        useradd -r -M -s /usr/sbin/nologin -d "$NEW_TAOS_DIR" taos \
            || useradd -r -M -s /sbin/nologin -d "$NEW_TAOS_DIR" taos \
            || { warn "useradd failed — chown step may also fail"; }
        log "  'taos' user created"
    else
        log "  'taos' user already exists — skipping useradd"
    fi

    if getent group incus >/dev/null 2>&1; then
        if usermod -aG incus taos >/dev/null 2>&1; then
            log "  added 'taos' to the 'incus' group"
        else
            warn "  could not add 'taos' to 'incus' — agent container deploys may fail"
        fi
    else
        warn "  'incus' group not found — skipping (install Incus first, then re-run the installer)"
    fi

    if getent group docker >/dev/null 2>&1; then
        if usermod -aG docker taos >/dev/null 2>&1; then
            log "  added 'taos' to the 'docker' group"
        else
            warn "  could not add 'taos' to 'docker' — Store Docker apps may fail"
        fi
    else
        warn "  'docker' group not found — skipping (install Docker first, then re-run the installer)"
    fi
else
    log "  non-Linux OS ($_os_name) — skipping user setup (launchd agent uses the invoking user)"
fi

# ---------------------------------------------------------------------------
# step 5 — fix ownership + permissions
# ---------------------------------------------------------------------------

log "[5/8] setting ownership and permissions on $NEW_TAOS_DIR ..."

# Security trade-off: taos must OWN the entire install dir (repo, .git,
# .venv, static/desktop/) so the in-app self-updater can write to those
# paths while running non-root (git pull, pip install -e ., npm run build).
# Full update-privilege-separation is a post-beta hardening task.
chown -R taos:taos "$NEW_TAOS_DIR" 2>/dev/null \
    || warn "  chown failed (taos user may not exist) — service will fail to start"

# Tighten the data directory and known sensitive credential files on top of
# the broad chown above — done AFTER so the restrictive perms win.
# (Mirrors set_data_dir_ownership in install-server.sh — keep in sync.)
chmod 0700 "$NEW_TAOS_DIR/data"
for _f in \
    "$NEW_TAOS_DIR/data/.auth_password" \
    "$NEW_TAOS_DIR/data/.auth_user.json" \
    "$NEW_TAOS_DIR/data/.auth_sessions" \
    "$NEW_TAOS_DIR/data/.auth_local_token" \
    "$NEW_TAOS_DIR/data/.litellm_db_url" \
    "$NEW_TAOS_DIR/data/browser_cookie_key.hex"; do
    [[ -f "$_f" ]] && chmod 0600 "$_f" && log "  chmod 0600 $_f"
done

log "  ownership + permissions set"

# ---------------------------------------------------------------------------
# step 6 — patch systemd unit if it still says User=root
# ---------------------------------------------------------------------------

UNIT_FILE="/etc/systemd/system/tinyagentos.service"

log "[6/8] checking systemd unit: $UNIT_FILE ..."
if [[ "$_os_name" != "Linux" ]]; then
    log "  non-Linux — skipping systemd step"
elif [[ ! -f "$UNIT_FILE" ]]; then
    warn "  $UNIT_FILE not found — skipping unit patch (re-run the installer to install the unit)"
else
    _current_user=$(grep -Po '(?<=^User=)\S+' "$UNIT_FILE" 2>/dev/null | head -1 || echo "")
    if [[ "$_current_user" == "root" ]]; then
        warn "  unit has User=root — patching to User=taos"
        sed -i 's/^User=root$/User=taos/' "$UNIT_FILE"
        # Also add Group=taos if missing.
        if ! grep -q '^Group=' "$UNIT_FILE"; then
            sed -i '/^User=taos$/a Group=taos' "$UNIT_FILE"
        else
            sed -i 's/^Group=root$/Group=taos/' "$UNIT_FILE"
        fi
        log "  unit patched: User=taos Group=taos"
    elif [[ "$_current_user" == "taos" ]]; then
        log "  unit already has User=taos — no patch needed"
    else
        warn "  unit has User=$_current_user — not patching (expected 'taos' or 'root')"
    fi

    systemctl daemon-reload
    log "  systemctl daemon-reload done"
fi

# ---------------------------------------------------------------------------
# step 7 — start the service
# ---------------------------------------------------------------------------

log "[7/8] starting tinyagentos.service..."
# Clear any stale root-owned LiteLLM config dir from the previous install.
# The controller writes its LiteLLM config to /tmp/taos-litellm at startup;
# if that directory was created by the old root process the non-root taos user
# cannot write to it, causing LiteLLM to fail on first boot after migration.
if [[ -d /tmp/taos-litellm ]]; then
    rm -rf /tmp/taos-litellm
    log "  cleared stale /tmp/taos-litellm (will be recreated by taos user on startup)"
fi
if command -v systemctl >/dev/null 2>&1 && [[ "$_os_name" == "Linux" ]]; then
    systemctl start tinyagentos \
        || { warn "  systemctl start failed — check: journalctl -u tinyagentos --no-pager -n 30"; }
    sleep 3
else
    log "  non-Linux or no systemctl — skipping service start"
fi

# ---------------------------------------------------------------------------
# step 8 — verify
# ---------------------------------------------------------------------------

log "[8/8] verifying service state..."

_pass=1

if command -v systemctl >/dev/null 2>&1 && [[ "$_os_name" == "Linux" ]]; then
    _active=$(systemctl is-active tinyagentos 2>/dev/null || echo "unknown")
    _svc_user=$(systemctl show -p User tinyagentos 2>/dev/null \
                | sed 's/^User=//' | grep -v '^$' || echo "unknown")

    if [[ "$_active" == "active" ]]; then
        ok "  service is active"
    else
        warn "  service state: $_active (expected: active)"
        _pass=0
    fi

    if [[ "$_svc_user" == "taos" ]]; then
        ok "  service user: taos"
    else
        warn "  service user: $_svc_user (expected: taos)"
        _pass=0
    fi
fi

log ""
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
if [[ "$_pass" -eq 1 ]]; then
    ok "  Migration PASSED"
    log ""
    log "  Your old install at $OLD_TAOS_DIR has been left intact."
    log "  Once you have verified the beta works, you may remove it with:"
    log "    sudo rm -rf $OLD_TAOS_DIR"
    if [[ "$_new_data_empty" -eq 0 ]]; then
        log ""
        log "  Pre-migration backup of NEW/data/ is at:"
        log "    $BACKUP_PATH"
        log "  Remove it once you are confident the migration succeeded."
    fi
else
    warn "  Migration finished with WARNINGS — service may not be fully operational."
    log ""
    log "  Diagnose with:"
    log "    journalctl -u tinyagentos --no-pager -n 50"
    log "    systemctl status tinyagentos"
    log ""
    log "  Your OLD install is untouched: $OLD_TAOS_DIR"
    if [[ "$_new_data_empty" -eq 0 ]]; then
        log "  Your pre-migration data backup is at: $BACKUP_PATH"
    fi
    log ""
    log "  If you need to roll back: stop the service, restore from the backup"
    log "  (or simply re-install pointing to the old dir), then investigate."
fi
log "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

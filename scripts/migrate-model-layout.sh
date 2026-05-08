#!/usr/bin/env bash
# One-shot migration to the shared model layout.
#
# Before this script: each backend wrote model files to its own dir
#   ~/rk-llama.cpp/models/<app_id>.gguf
#   /opt/tinyagentos/models/<app_id>-<variant>.<ext>
#
# After: every backend writes to a single tree
#   ~/models/<backend>/<family>/<manifest_id>/<filename>
#
# This script moves files into the new layout in place and updates the
# rk-llama.cpp service-state symlink + systemd unit so the running
# llama-server keeps serving from the new path.
#
# Idempotent: re-running on an already-migrated tree is a no-op.
# Conservative: never deletes a file it can't account for; logs a warning
# instead.

set -euo pipefail

log()  { echo -e "\033[1;34m[migrate-models]\033[0m $*"; }
warn() { echo -e "\033[1;33m[migrate-models]\033[0m $*" >&2; }
die()  { echo -e "\033[1;31m[migrate-models]\033[0m $*" >&2; exit 1; }

# Resolve target user — controller installs as root or jay; pick the owner
# of the running unit's WorkingDirectory rather than guess from $HOME.
TARGET_USER="${SUDO_USER:-$(id -un)}"
TARGET_HOME=$(getent passwd "$TARGET_USER" | cut -d: -f6)
[[ -d "$TARGET_HOME" ]] || die "could not resolve home for $TARGET_USER"

NEW_ROOT="${TAOS_MODELS_ROOT:-$TARGET_HOME/models}"
log "target user: $TARGET_USER  new root: $NEW_ROOT"

mkdir -p "$NEW_ROOT"
chown "$TARGET_USER:$TARGET_USER" "$NEW_ROOT"

# Pure-bash family extractor — first dash-separated token of the id,
# lowercased. Matches model_paths.family_from_manifest's fallback path.
# Manifests with an explicit family: field don't pass through here
# (they got installed via the new code path already).
family_of() {
    local id="$1"
    echo "${id%%-*}" | tr '[:upper:]' '[:lower:]'
}

# --- rk-llama.cpp -----------------------------------------------------
RKDIR="$TARGET_HOME/rk-llama.cpp"
RK_OLD_MODELS="$RKDIR/models"
RK_NEW_BACKEND="$NEW_ROOT/rk-llama.cpp"
moved=0
if [[ -d "$RK_OLD_MODELS" ]]; then
    log "scanning $RK_OLD_MODELS"
    mkdir -p "$RK_NEW_BACKEND"
    for src in "$RK_OLD_MODELS"/*.gguf; do
        [[ -e "$src" ]] || continue
        # Skip the active.gguf symlink — re-pointed below.
        if [[ -L "$src" ]]; then continue; fi
        base=$(basename "$src")
        # Old naming was "<app_id>.gguf"; recover app_id and family from it.
        manifest_id="${base%.gguf}"
        family=$(family_of "$manifest_id")
        target_dir="$RK_NEW_BACKEND/$family/$manifest_id"
        target="$target_dir/$base"
        if [[ -e "$target" ]]; then
            log "  $base already migrated, skipping"
            continue
        fi
        mkdir -p "$target_dir"
        log "  moving $base -> $family/$manifest_id/"
        mv "$src" "$target"
        chown -R "$TARGET_USER:$TARGET_USER" "$target_dir"
        moved=$((moved+1))
    done
fi

# Re-point the active.gguf symlink. The old pattern was
# <install_dir>/models/active.gguf -> <name>.gguf (relative). The new
# pattern is <install_dir>/active.gguf -> <absolute path under NEW_ROOT>.
ACTIVE_OLD="$RK_OLD_MODELS/active.gguf"
ACTIVE_NEW="$RKDIR/active.gguf"
if [[ -L "$ACTIVE_OLD" ]]; then
    target_name=$(readlink "$ACTIVE_OLD")
    # target_name is bare basename like "gemma-4-e2b-gguf.gguf"
    manifest_id="${target_name%.gguf}"
    family=$(family_of "$manifest_id")
    new_target="$RK_NEW_BACKEND/$family/$manifest_id/$target_name"
    if [[ -f "$new_target" ]]; then
        ln -sfn "$new_target" "$ACTIVE_NEW"
        chown -h "$TARGET_USER:$TARGET_USER" "$ACTIVE_NEW"
        rm -f "$ACTIVE_OLD"
        log "  active.gguf -> $new_target"
    else
        warn "  active.gguf points to $target_name but $new_target missing — leaving symlink alone for human review"
    fi
fi

# Restart the rkllamacpp service so llama-server picks up the new -m
# argument from the updated unit. The unit file is rewritten by
# install-rk-llama-cpp.sh on the next controller upgrade; for now we
# rewrite it inline if it still has the old path.
UNIT="/etc/systemd/system/rkllamacpp.service"
if [[ -f "$UNIT" ]] && grep -q "models/active.gguf" "$UNIT"; then
    log "rewriting unit $UNIT to use new active.gguf path"
    sed -i 's|/models/active.gguf|/active.gguf|g' "$UNIT"
    systemctl daemon-reload
fi
if systemctl is-enabled rkllamacpp >/dev/null 2>&1; then
    log "restarting rkllamacpp"
    systemctl restart rkllamacpp || warn "rkllamacpp restart failed — check journalctl -u rkllamacpp"
fi

# --- legacy /opt/tinyagentos/models -----------------------------------
# Older download_installer dropped flat files at /opt/tinyagentos/models.
# We can't always recover the (backend, manifest_id) split from a flat
# filename like "qwen3.5-9b-q4_k_m.gguf", so DON'T move these. Just log
# their presence so the user can decide whether to leave or hand-migrate.
LEGACY="/opt/tinyagentos/models"
if [[ -d "$LEGACY" ]]; then
    legacy_files=$(find "$LEGACY" -maxdepth 1 -type f 2>/dev/null | wc -l)
    if [[ "$legacy_files" -gt 0 ]]; then
        warn "found $legacy_files legacy file(s) at $LEGACY — left in place; uninstall + reinstall to migrate them cleanly"
    fi
fi

log "done. moved $moved file(s) into $NEW_ROOT"

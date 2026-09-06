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

# Resolve the rk-llama.cpp install dir from the live systemd unit so we
# migrate the directory that's actually serving traffic. Falls back to
# $SUDO_USER's home only if no unit is installed (fresh Pi, no service
# yet) — otherwise picking the wrong tree can leave the unit pointing
# at a non-existent active.gguf and the service refuses to start.
UNIT_PATH="/etc/systemd/system/rkllamacpp.service"
RKDIR=""
if [[ -f "$UNIT_PATH" ]]; then
    # ExecStart line lists the binary path — strip the "bin/llama-server …" tail
    # to get the install dir.
    binary=$(awk -F= '/^ExecStart=/{ split($2,a," "); print a[1] }' "$UNIT_PATH" | head -1)
    if [[ -n "$binary" && "$binary" == */bin/llama-server ]]; then
        RKDIR="${binary%/bin/llama-server}"
    fi
fi
if [[ -z "$RKDIR" ]]; then
    # Fall back: $SUDO_USER home, then $HOME. Only used when no unit
    # exists yet (this script ran before install-rk-llama-cpp.sh).
    fallback_user="${SUDO_USER:-$(id -un)}"
    fallback_home=$(getent passwd "$fallback_user" | cut -d: -f6)
    [[ -d "$fallback_home" ]] || die "could not resolve home for $fallback_user"
    RKDIR="$fallback_home/rk-llama.cpp"
fi

# Owner of the install dir drives chown — the service runs as that user.
if [[ -d "$RKDIR" ]]; then
    install_owner=$(stat -c '%U' "$RKDIR")
else
    install_owner="${SUDO_USER:-$(id -un)}"
fi
install_home=$(getent passwd "$install_owner" | cut -d: -f6)
[[ -d "$install_home" ]] || die "could not resolve home for install owner $install_owner"

NEW_ROOT="${TAOS_MODELS_ROOT:-$install_home/models}"
log "rk-llama.cpp install dir: $RKDIR  owner: $install_owner  new models root: $NEW_ROOT"

mkdir -p "$NEW_ROOT"
chown "$install_owner:$install_owner" "$NEW_ROOT" 2>/dev/null || true

# Pure-bash family extractor — first dash-separated token of the id,
# lowercased. Matches model_paths.family_from_manifest's fallback path.
# Manifests with an explicit family: field don't pass through here
# (they got installed via the new code path already).
family_of() {
    local id="$1"
    echo "${id%%-*}" | tr '[:upper:]' '[:lower:]'
}

# --- rk-llama.cpp -----------------------------------------------------
RK_OLD_MODELS="$RKDIR/models"
RK_NEW_BACKEND="$NEW_ROOT/rk-llama.cpp"
moved=0

# Skip rk-llama.cpp's own bundled vocab test fixtures — they're build
# artefacts in the source tree, not user-installed models. Detect them
# by filename pattern so we don't accidentally migrate them and then
# expose them in /api/models as fake installed models.
is_vocab_fixture() {
    local name="$1"
    [[ "$name" == ggml-vocab-* ]]
}

if [[ -d "$RK_OLD_MODELS" ]]; then
    log "scanning $RK_OLD_MODELS"
    mkdir -p "$RK_NEW_BACKEND"
    for src in "$RK_OLD_MODELS"/*.gguf; do
        [[ -e "$src" ]] || continue
        # Skip the active.gguf symlink — re-pointed below.
        if [[ -L "$src" ]]; then continue; fi
        base=$(basename "$src")
        if is_vocab_fixture "$base"; then
            log "  skip $base (rk-llama.cpp vocab test fixture, not a real model)"
            continue
        fi
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
        chown -R "$install_owner:$install_owner" "$target_dir" 2>/dev/null || true
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
        chown -h "$install_owner:$install_owner" "$ACTIVE_NEW" 2>/dev/null || true
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

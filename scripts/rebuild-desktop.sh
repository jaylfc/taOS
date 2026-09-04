#!/bin/bash
# Rebuild the TinyAgentOS desktop SPA when the source is newer than the bundle.
#
# Launched by tinyagentos-rebuild-desktop.service AFTER the controller is already
# running, so a stale (or missing) bundle never blocks service startup.  Previously
# this was an ExecStartPre in tinyagentos.service, which added ~50 s to every
# restart after a git pull, and the mtime check could fire spuriously after a
# checkout/rebase even when nothing changed (taOS #807).
#
# The script is designed to be run from the install directory (WorkingDirectory=),
# matching the paths the old ExecStartPre used.

set -euo pipefail

# The service unit sets WorkingDirectory to the install root, so relative
# paths resolve correctly without needing a parameter.
if [ ! -d desktop ]; then
    echo "[taos-rebuild-desktop] no desktop/ directory found — nothing to rebuild"
    exit 0
fi

# Check whether any desktop build source is newer than the bundle.
# The bundle is considered stale if ANY of these are newer than
# static/desktop/index.html:
#   • desktop/src/**           — the React/TypeScript source
#   • desktop/package.json     — dependency declarations
#   • desktop/*-lock.*         — dependency lock-files (package-lock.json, pnpm-lock.yaml, etc.)
#   • desktop/vite.config.*    — Vite build config
#   • desktop/tsconfig*.json   — TypeScript compiler config
#
# Using -print -quit so find stops at the first hit (no need to scan everything).
if [ -f static/desktop/index.html ]; then
    # Provenance check: if a fetched bundle matches current source, skip
    # regardless of filesystem mtimes (which can be misleading after a fetch).
    # Only trust provenance when the desktop/ working tree is clean (matches
    # the committed HEAD:desktop) -- otherwise local edits or untracked build
    # inputs could be skipped by a stale-but-matching marker.
    #
    # A failing `git status` (git missing, broken/absent .git, unreadable index,
    # extracted tarball) is treated as DIRTY, not clean: we cannot prove the tree
    # matches HEAD, so we rebuild rather than serve a bundle we cannot vouch for.
    # This matches _is_desktop_working_tree_clean() in tinyagentos/desktop_rebuild.py,
    # which returns False on a non-zero git exit.
    _provenance_file="static/desktop/.taos-bundle-provenance"
    if _working_tree_status="$(git status --porcelain --untracked-files=normal -- desktop 2>/dev/null)" \
        && [ -z "$_working_tree_status" ] && [ -f "$_provenance_file" ]; then
        _current_tree="$(git rev-parse HEAD:desktop 2>/dev/null || echo "")"
        _recorded_tree="$(cat "$_provenance_file" 2>/dev/null || echo "")"
        if [ -n "$_current_tree" ] && [ "$_current_tree" = "$_recorded_tree" ]; then
            echo "[taos-rebuild-desktop] desktop bundle provenance is current, skipping rebuild"
            exit 0
        fi
    fi

    _stale_src="$(find desktop/src -type f -not -path '*/node_modules/*' -newer static/desktop/index.html -print -quit 2>/dev/null)"
    _stale_cfg="$(find desktop \( -name 'package.json' -o -name '*-lock.*' -o -name 'vite.config.*' -o -name 'tsconfig*.json' \) -type f -newer static/desktop/index.html -print -quit 2>/dev/null)"
    if [ -z "$_stale_src" ] && [ -z "$_stale_cfg" ]; then
        echo "[taos-rebuild-desktop] desktop bundle is current — skipping rebuild"
        exit 0
    fi
fi

echo "[taos-rebuild-desktop] desktop source newer than bundle (or no bundle) — rebuilding..."

# Guard: without package.json, npm install will fail with a confusing error.
# Exit cleanly so the service doesn't look broken when this is a bare install
# that hasn't checked out the desktop subdirectory yet.
if [ ! -f desktop/package.json ]; then
    echo "[taos-rebuild-desktop] desktop/package.json not found — skipping rebuild (desktop source not checked out?)"
    exit 0
fi

if (cd desktop && npm install && npm run build); then
    echo "[taos-rebuild-desktop] desktop rebuild succeeded"
    # The marker names HEAD:desktop, so it only describes what we just built
    # when the tree that was built is clean.  After a build from a dirty tree we
    # invalidate any existing marker instead of recording a SHA the bundle does
    # not correspond to -- otherwise reverting the edits would present a clean
    # tree plus a matching marker in front of a bundle built from edited source.
    # Mirrors rebuild_desktop_bundle_if_stale() in tinyagentos/desktop_rebuild.py.
    _current_tree="$(git rev-parse HEAD:desktop 2>/dev/null || echo "")"
    if ! _post_build_status="$(git status --porcelain --untracked-files=normal -- desktop 2>/dev/null)" \
        || [ -n "$_post_build_status" ]; then
        rm -f static/desktop/.taos-bundle-provenance || true
        echo "[taos-rebuild-desktop] desktop tree is dirty (or git unavailable) -- provenance marker cleared"
    elif [ -n "$_current_tree" ]; then
        mkdir -p static/desktop || { echo "[taos-rebuild-desktop] could not create static/desktop -- bundle provenance marker NOT written; next update may fall through to the mtime path" >&2; exit 0; }
        # Per-invocation scratch file: two rebuilds racing (systemd Restart=, or a
        # manual `systemctl start` over an in-flight run) must not read or delete
        # each other's stderr capture and swallow a real write failure.
        _marker_err="$(mktemp "${TMPDIR:-/tmp}/taos-rebuild-desktop-marker.XXXXXX")"
        if ! echo "$_current_tree" > static/desktop/.taos-bundle-provenance 2>"$_marker_err"; then
            echo "[taos-rebuild-desktop] could not write bundle provenance marker: $(cat "$_marker_err")" >&2
            rm -f "$_marker_err"
            exit 0
        fi
        rm -f "$_marker_err"
    fi
elif [ -f static/desktop/index.html ]; then
    echo "[taos-rebuild-desktop] desktop rebuild FAILED -- keeping the existing bundle (see journalctl -u tinyagentos-rebuild-desktop)"
else
    echo "[taos-rebuild-desktop] desktop rebuild FAILED and no existing bundle — UI may be unavailable until a successful rebuild"
    exit 1
fi

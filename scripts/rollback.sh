#!/usr/bin/env bash
# taOS rollback -- restore the branch + commit the last update left, then
# restart. Pure git + service restart on purpose: it must work when an update
# has broken the Python app or the dashboard is unreachable.
#
#   bash scripts/rollback.sh            # undo the last update (branch + version)
#   bash scripts/rollback.sh <ref>      # roll back to a specific tag/branch/sha
#
# The updater records the pre-update state in <install>/.taos-rollback before it
# touches anything, so even a clean fast-forward has a restore point. That file
# is read as DATA (see record_field below), never sourced: it is writable by the
# taos service account and this script escalates with sudo.
set -euo pipefail

# --- locate the install (this script lives in <install>/scripts) ---
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="${TAOS_INSTALL_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
cd "$INSTALL_DIR"

if [[ ! -d .git ]]; then
  echo "taos rollback: $INSTALL_DIR is not a git checkout; cannot roll back" >&2
  exit 1
fi

log(){ echo "[rollback] $*"; }

# Read one `key='value'` field out of the record file WITHOUT executing it.
# The record sits in the install dir, which the installer chowns to the taos
# service account, and this script escalates with sudo further down -- so
# `source` would hand arbitrary code from a writable data file straight to
# root. tinyagentos/rollback.py writes the values single-quoted with the
# usual '\'' escape for an embedded quote; anything else is returned verbatim
# and rejected by the validation below.
record_field(){
  local key="$1" line val
  line="$(grep -m1 -E "^[[:space:]]*${key}=" .taos-rollback 2>/dev/null)" || return 0
  val="${line#*=}"
  if [[ "$val" == \'*\' ]]; then
    val="${val:1:${#val}-2}"
    val="${val//\'\\\'\'/\'}"
  fi
  printf '%s' "$val"
}

# A recorded commit is a FULL object name. The writer records `git rev-parse
# HEAD`, which is 40 hex (64 in a sha256 checkout) and never abbreviated -- so a
# short value in the record is a truncated or forged one, not a legitimate
# prefix, even when git would happily resolve it.
sha_safe(){
  [[ "$1" =~ ^[0-9a-fA-F]{40}$ || "$1" =~ ^[0-9a-fA-F]{64}$ ]]
}

# A recorded branch is usable only if git itself calls it a valid ref name, and
# only if it does not start with a dash: `git checkout -B --force <sha>` would
# read the name as an option, and git considers `refs/heads/--force` a perfectly
# valid ref. check-ref-format is the authority on the rest (no `..`, no leading
# `.`, no trailing `.lock`, no `@{`, no control characters, space or ~^:?*[\) --
# hand-rolling that grammar in a regex is how these checks drift out of date.
ref_safe(){
  [[ -n "$1" && "$1" != -* ]] && git check-ref-format "refs/heads/$1" 2>/dev/null
}

target_ref="${1:-}"
prev_branch=""
prev_sha=""

if [[ -n "$target_ref" ]]; then
  # Explicit target: a tag, branch, or sha the user named.
  prev_sha="$target_ref"
  log "explicit target: $target_ref"
elif [[ -f .taos-rollback ]]; then
  prev_branch="$(record_field prev_branch)"
  prev_sha="$(record_field prev_sha)"
  # A truncated or tampered record must not reach git. An unusable commit sends
  # the whole run to the recovery tag; an unusable branch costs only the branch,
  # because getting the commit back still beats not rolling back at all.
  if ! sha_safe "$prev_sha"; then
    log "record file has no usable commit; falling back to the recovery tag"
    prev_branch=""
    prev_sha=""
  else
    if ! ref_safe "$prev_branch"; then
      log "record file has no usable branch; restoring the commit only"
      prev_branch=""
    fi
    log "recorded target: branch='${prev_branch}' commit='${prev_sha:0:12}'"
  fi
fi

if [[ -z "$prev_sha" ]]; then
  # No usable record (install predates the file, or it is missing/corrupt):
  # fall back to the newest recovery tag.
  prev_sha="$(git tag --list 'taos-pre-update-*' --sort=-creatordate | head -1)"
  prev_branch=""
  if [[ -z "$prev_sha" ]]; then
    echo "taos rollback: no usable rollback target and no taos-pre-update-* tag found" >&2
    exit 1
  fi
  log "using newest recovery tag: $prev_sha"
fi

# Best-effort fetch so an explicit branch/tag that only exists on the remote
# can still be resolved; never fatal (offline recovery must still work).
git fetch origin --tags --quiet 2>/dev/null || log "fetch skipped (offline?)"

# An explicit target may name a branch that only exists on the remote
# (origin/<name>). Resolve it to the remote ref and recreate it locally so both
# branch and version are restored, not just a detached commit.
if [[ -n "$target_ref" ]] && ! git rev-parse --verify --quiet "${prev_sha}^{commit}" >/dev/null; then
  if git rev-parse --verify --quiet "origin/${target_ref}^{commit}" >/dev/null; then
    prev_branch="$target_ref"
    prev_sha="origin/${target_ref}"
    log "explicit target resolved to remote branch origin/${target_ref}"
  fi
fi

if ! git rev-parse --verify --quiet "${prev_sha}^{commit}" >/dev/null; then
  echo "taos rollback: cannot resolve '$prev_sha' to a commit" >&2
  exit 1
fi

# A broken update often leaves a dirty tree, and a plain checkout would fail
# under set -e and defeat the recovery. Stash local changes (including
# untracked) first so the checkout always lands; getting back to a working
# version beats preserving in-place edits, and the stash keeps them retrievable.
if [[ -n "$(git status --porcelain 2>/dev/null)" ]]; then
  if git stash push --include-untracked -m "taos-rollback-$(git rev-parse --short HEAD 2>/dev/null)" >/dev/null 2>&1; then
    log "stashed local changes before rollback (recover with: git stash list)"
  else
    log "could not stash local changes; will force the checkout"
  fi
fi

# Restore BOTH branch and version: move/create the recorded branch onto the
# recorded commit and check it out. With no recorded branch (explicit ref or
# tag fallback) we land in detached HEAD at that commit, which is still a valid
# running state. Fall back to --force so a stubborn tree can never block recovery.
if [[ -n "$prev_branch" && "$prev_branch" != "HEAD" ]]; then
  log "restoring branch '$prev_branch' at ${prev_sha:0:12}"
  git checkout -B "$prev_branch" "$prev_sha" 2>/dev/null || git checkout --force -B "$prev_branch" "$prev_sha"
else
  log "checking out $prev_sha (detached)"
  git checkout --quiet --detach "$prev_sha" 2>/dev/null || git checkout --quiet --force --detach "$prev_sha"
fi

# --- restart the service (first method that applies wins) ---
restarted=""
if command -v systemctl >/dev/null 2>&1; then
  if systemctl is-enabled tinyagentos >/dev/null 2>&1 || systemctl status tinyagentos >/dev/null 2>&1; then
    sudo systemctl restart tinyagentos 2>/dev/null && restarted="systemd" || true
  fi
  if [[ -z "$restarted" ]] && systemctl --user status tinyagentos >/dev/null 2>&1; then
    systemctl --user restart tinyagentos 2>/dev/null && restarted="systemd --user" || true
  fi
fi
if [[ -z "$restarted" ]] && command -v launchctl >/dev/null 2>&1; then
  plist="$HOME/Library/LaunchAgents/com.tinyagentos.controller.plist"
  if [[ -f "$plist" ]]; then
    launchctl unload "$plist" 2>/dev/null || true
    launchctl load "$plist" 2>/dev/null && restarted="launchd" || true
  fi
fi
if [[ -z "$restarted" && -x "$INSTALL_DIR/scripts/taos-run.sh" ]]; then
  # Stop the running controller, but never signal THIS rollback process or its
  # ancestors: the invoking `taos` CLI also has "tinyagentos" in its command
  # line, so a bare `pkill -f tinyagentos` would kill the process doing the
  # rollback before the new controller starts.
  skip=" $$ "
  _p=$$
  while _p="$(ps -o ppid= -p "$_p" 2>/dev/null | tr -d ' ')"; do
    [[ -z "$_p" || "$_p" == "0" || "$_p" == "1" ]] && break
    skip="$skip$_p "
  done
  for _pid in $(pgrep -f "taos-run.sh|tinyagentos" 2>/dev/null || true); do
    case "$skip" in *" $_pid "*) continue ;; esac
    kill "$_pid" 2>/dev/null || true
  done
  nohup "$INSTALL_DIR/scripts/taos-run.sh" >/dev/null 2>&1 &
  restarted="nohup"
fi

now_sha="$(git rev-parse --short HEAD)"
now_ref="$(git rev-parse --abbrev-ref HEAD)"
log "rolled back to ${now_ref} @ ${now_sha}"
if [[ -n "$restarted" ]]; then
  log "service restarted via ${restarted}. Give it a few seconds, then reload taOS."
else
  log "could not auto-restart the service; restart taOS manually to finish."
fi
log "note: if the web UI looks stale, the frontend bundle may need a rebuild (re-run the installer)."

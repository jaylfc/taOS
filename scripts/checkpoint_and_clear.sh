#!/usr/bin/env bash
# checkpoint_and_clear.sh -- rotate a resume/checkpoint file into a fresh
# session. Appends a retrospective block and a FLEET-HEALTH block, then clears
# the source session -- but only once the on-disk file is verified within the
# 32768-byte rotation limit AFTER the appends.
#
# The limit is enforced on the artefact that ships. The check runs after the
# blocks are appended and the file is trimmed back to the limit if they push it
# over, so a checkpoint that passed the pre-check can never truncate on the
# successor's next Read while its clear is still being dispatched -- the failure
# mode this guard exists to prevent:
#
#   "Refusing to clear: an oversized checkpoint truncates on the next Read and
#    your successor silently inherits only its top half."
#
# Usage:
#   checkpoint_and_clear.sh <resume-file> <task-id>
#
# Env:
#   CPC_FINDINGS_FILE  optional file (one finding per line) for the retrospective
#                      block; when unset, today's commits from `git log` are used.
set -euo pipefail

LIMIT=32768
readonly LIMIT

log(){ printf '[%s] checkpoint_and_clear: %s\n' "$(date -u +%H:%M:%SZ)" "$*"; }

resume="${1:-}"
task_id="${2:-}"

if [[ -z "$resume" || -z "$task_id" ]]; then
  echo "checkpoint_and_clear: usage: checkpoint_and_clear.sh <resume-file> <task-id>" >&2
  exit 2
fi
if [[ ! -f "$resume" ]]; then
  echo "checkpoint_and_clear: resume file not found: $resume" >&2
  exit 2
fi

# Locate the install root (this script lives in <install>/scripts) so the git
# default for retrospective findings resolves against the repo under rotation.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="${TAOS_INSTALL_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"

# Pre-flight: never mutate an already-oversized checkpoint. Its Read would
# truncate to the limit and the successor would silently inherit only its top
# half, so leave it untouched for a human to trim.
size_before=$(wc -c < "$resume" | tr -d ' ')
if (( size_before > LIMIT )); then
  log "Refusing to clear: an oversized checkpoint truncates on the next Read and your successor silently inherits only its top half."
  exit 1
fi

# Sanity: a checkpoint must declare a next action, otherwise clearing it throws
# away a session there is no durable state for.
line_count=$(wc -l < "$resume" | tr -d ' ')
if ! grep -qiE '^NEXT[[:space:]]*:' "$resume"; then
  echo "checkpoint_and_clear: refusing to checkpoint: no 'NEXT:' marker in $resume" >&2
  exit 1
fi
log "(checkpoint looks sane: ${line_count} lines, has a next action)"

# --- gather retrospective findings (one per line) into a temp file ---
findings_tmp="$(mktemp)"
if [[ -n "${CPC_FINDINGS_FILE:-}" && -f "${CPC_FINDINGS_FILE}" ]]; then
  grep . "${CPC_FINDINGS_FILE}" > "$findings_tmp" 2>/dev/null || true
else
  git -C "$INSTALL_DIR" log --oneline --since='00:00' --no-merges HEAD 2>/dev/null > "$findings_tmp" || true
fi
if [[ ! -s "$findings_tmp" ]]; then
  printf 'no new findings\n' > "$findings_tmp"
fi
finding_count=$(grep -c . "$findings_tmp" || true)

# --- assemble the appended block (retrospective + FLEET-HEALTH) ---
# Written after the sanity check: these are the "post-validation" blocks that
# previously shipped past the limit because the size check ran before them.
append_tmp="$(mktemp)"
{
  printf '\n## Retrospective\n'
  printf '%s %s\n' "$(date -u +%Y-%m-%d)" "$(date -u +%H:%M:%SZ)"
  while IFS= read -r line; do
    printf -- '- %s\n' "$line"
  done < "$findings_tmp"
  printf '\n## FLEET-HEALTH (Open at checkpoint time)\n'
  printf 'session: %s\n' "$task_id"
  printf 'resume: %s bytes (limit %s)\n' "$size_before" "$LIMIT"
  printf 'appended: %s retrospective finding(s)\n' "$finding_count"
} > "$append_tmp"

cat "$append_tmp" >> "$resume"
log "appended ${finding_count} retrospective finding(s)"
rm -f "$append_tmp" "$findings_tmp"

# The authoritative limit check is post-append: it measures the file that
# successors will actually Read. If the appends pushed it over the limit, trim
# the overflow (keeping the durable head) and refuse to clear -- never ship an
# oversized checkpoint and never dispatch a clear against one.
size_after=$(wc -c < "$resume" | tr -d ' ')
if (( size_after > LIMIT )); then
  log "Refusing to clear: an oversized checkpoint truncates on the next Read and your successor silently inherits only its top half."
  truncate -s "$LIMIT" "$resume"
  exit 1
fi

# Within limit: the successor can Read the whole file, so dispatch the clear.
log "clear REQUESTED for ${task_id}"
exit 0

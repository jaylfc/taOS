#!/usr/bin/env bash
# Migrate per-agent trace stores from the pre-pivot agent-home path
# to the new {data_dir}/trace/{slug}/ location.
#
# Old path: {data_dir}/agent-home/{slug}/.taos/trace/
# New path: {data_dir}/trace/{slug}/
#
# Idempotent. Safe to re-run. Skips if nothing to migrate.
# Merges bucket files — no clobber if the destination file already exists.
set -euo pipefail

DATA_DIR="${1:-/opt/tinyagentos/data}"

if [ ! -d "$DATA_DIR/agent-home" ]; then
  exit 0
fi

mkdir -p "$DATA_DIR/trace"

moved=0
for home in "$DATA_DIR"/agent-home/*/; do
  [ -d "$home" ] || continue
  slug=$(basename "$home")
  old="$home.taos/trace"
  new="$DATA_DIR/trace/$slug"
  [ -d "$old" ] || continue
  mkdir -p "$new"
  # Move each bucket file individually so we don't clobber on merge.
  while IFS= read -r -d '' f; do
    b=$(basename "$f")
    if [ -f "$new/$b" ]; then
      echo "[migrate-trace] skip existing $new/$b (no clobber)"
    else
      mv -v "$f" "$new/$b"
      moved=$((moved + 1))
    fi
  done < <(find "$old" -maxdepth 1 -type f \( -name '*.db' -o -name '*.jsonl' \) -print0)
  # Remove the old directories if now empty.
  rmdir "$old" 2>/dev/null || true
  rmdir "$(dirname "$old")" 2>/dev/null || true
done

echo "[migrate-trace] moved $moved bucket files"

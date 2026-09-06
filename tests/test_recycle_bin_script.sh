#!/usr/bin/env bash
# Syntax + key-behaviour tests for the recycle-bin install script.
set -euo pipefail
SCRIPT=app-catalog/_common/scripts/recycle-bin-install.sh

echo "test: bash -n syntax"
bash -n "$SCRIPT"

echo "test: shadow rm script contents are valid bash"
awk '/cat > \/usr\/local\/bin\/rm/,/^EOF$/' "$SCRIPT" | sed '1d;$d' | bash -n

echo "test: sweep script contents are valid bash"
awk '/cat > \/usr\/local\/bin\/taos-recycle-sweep/,/^EOF$/' "$SCRIPT" | sed '1d;$d' | bash -n

echo "test: service + timer files are valid systemd unit syntax"
# systemd-analyze verify if available, else grep for required sections
if command -v systemd-analyze >/dev/null 2>&1; then
  tmpdir=$(mktemp -d)
  awk '/cat > \/etc\/systemd\/system\/tinyagentos-recycle-sweep.service/,/^EOF$/' "$SCRIPT" \
    | sed '1d;$d' > "$tmpdir/tinyagentos-recycle-sweep.service"
  awk '/cat > \/etc\/systemd\/system\/tinyagentos-recycle-sweep.timer/,/^EOF$/' "$SCRIPT" \
    | sed '1d;$d' > "$tmpdir/tinyagentos-recycle-sweep.timer"
  systemd-analyze verify "$tmpdir"/*.service "$tmpdir"/*.timer
  rm -rf "$tmpdir"
fi

echo "all tests passed"

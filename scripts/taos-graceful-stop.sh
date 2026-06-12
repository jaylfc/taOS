#!/bin/bash
# Triggers taOS graceful shutdown via HTTP. Used by systemd stop/pre-shutdown hooks.
# Succeeds even if the API is unreachable so we don't block system reboot.
#
# --max-time is deliberately short: this runs on every `systemctl restart`, and
# if /api/system/prepare-shutdown ever hangs (it has), a long timeout strands the
# service in `deactivating` with the port dead for minutes — which also makes the
# in-app Update appear to fail, since it restarts the service. Draining must be
# best-effort and quick; anything slower belongs in an async background task.
#
# Honour the configured controller port: systemd passes TAOS_PORT into this
# hook's environment (see install-server.sh), so a custom-port install drains
# the right origin instead of a hardcoded 6969 that would silently no-op.
#
# Dedupe: both the unit ExecStop and taos-pre-shutdown.service call this script
# on a reboot. Write a stamp on success so a second invocation within 60s is
# a no-op, avoiding a double agent prepare-shutdown pass.
STAMP_FILE=/run/taos-prepare-shutdown.stamp
# Fall back to /tmp if /run is not writable (e.g. non-root installs).
if [ ! -w /run ] 2>/dev/null; then
    STAMP_FILE=/tmp/taos-prepare-shutdown.stamp
fi

if [ -f "$STAMP_FILE" ]; then
    stamp_age=$(( $(date +%s) - $(stat -c %Y "$STAMP_FILE" 2>/dev/null || echo 0) ))
    if [ "$stamp_age" -lt 60 ]; then
        exit 0
    fi
fi

curl -fsS -X POST --max-time 25 "http://localhost:${TAOS_PORT:-6969}/api/system/prepare-shutdown" || true
touch "$STAMP_FILE" 2>/dev/null || true

#!/bin/bash
# Triggers taOS graceful shutdown via HTTP. Used by systemd stop/pre-shutdown hooks.
# Succeeds even if the API is unreachable so we don't block system reboot.
#
# --max-time is deliberately short: this runs on every `systemctl restart`, and
# if /api/system/prepare-shutdown ever hangs (it has), a long timeout strands the
# service in `deactivating` with the port dead for minutes — which also makes the
# in-app Update appear to fail, since it restarts the service. Draining must be
# best-effort and quick; anything slower belongs in an async background task.
curl -fsS -X POST --max-time 25 http://localhost:6969/api/system/prepare-shutdown || true

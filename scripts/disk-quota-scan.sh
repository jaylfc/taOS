#!/usr/bin/env bash
set -euo pipefail

# Trigger a disk quota scan via the taOS API.
# Used by tinyagentos-disk-quota.service (oneshot).

TOKEN=$(cat /home/jay/tinyagentos/data/.auth_local_token 2>/dev/null \
     || cat /opt/tinyagentos/data/.auth_local_token 2>/dev/null \
     || true)

if [ -z "${TOKEN}" ]; then
    echo "disk-quota-scan: no local token found; taOS may not be started yet"
    exit 0
fi

curl -sf -m 10 \
    -H "Authorization: Bearer ${TOKEN}" \
    -X POST \
    http://127.0.0.1:6969/api/disk-quota/scan \
    >/dev/null || exit 0

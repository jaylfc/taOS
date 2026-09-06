#!/usr/bin/env bash
# install.sh — openclaw agent runtime installer
# Runs once inside a fresh Debian bookworm LXC container.
# Idempotent: safe to re-run on an already-provisioned container.
#
# Installs UPSTREAM OpenClaw from npm (openclaw@latest) — no fork. taOS drives
# the agent over ACP (tinyagentos/openclaw_acp_runtime), so the legacy
# taos-bridge channel is no longer installed/needed.
set -euo pipefail

# The pre-built taos-openclaw-base image warms Node + system deps + a recent
# openclaw. We still always install openclaw@latest from npm (the base image's
# baked version may lag) and re-write per-deploy config/env + the systemd unit.
# See tinyagentos/agent_image.py and .github/workflows/build-agent-images.yml.
TAOS_BASE_IMAGE_PRESENT="${TAOS_BASE_IMAGE_PRESENT:-0}"
echo "[openclaw] installing upstream OpenClaw (openclaw@latest from npm)"

# ---------------------------------------------------------------------------
# 1. Node >= 22.19 via NodeSource (upstream OpenClaw's minimum; Debian default
#    is too old). Also ensure 'git' — OpenClaw has a git-URL transitive dep
#    (libsignal via @whiskeysockets/baileys) that npm fetches at install time.
#
#    Checked the FULL version, not just major: Node 22.0–22.18 satisfies a
#    major-only "== 22" guard but is too old for OpenClaw and only fails later.
#    Run unconditionally — a base image with an older baked Node must be
#    upgraded too (the check is a no-op when Node is already current).
# ---------------------------------------------------------------------------
_node_ok() {
  command -v node >/dev/null 2>&1 || return 1
  local v maj min
  v=$(node -v | sed 's/^v//')      # e.g. 22.22.3
  maj=${v%%.*}
  min=${v#*.}; min=${min%%.*}
  [ "$maj" -gt 22 ] && return 0
  [ "$maj" -eq 22 ] && [ "$min" -ge 19 ] && return 0
  return 1
}
if ! _node_ok; then
  echo "[openclaw] installing Node >=22.19 via NodeSource"
  curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends nodejs
fi
if ! command -v git >/dev/null 2>&1; then
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends git
fi

# ----------------------------------------------------------------------
# 2. Install upstream OpenClaw from npm (latest by default — NO fork).
# The published `openclaw` package ships ready-to-run; we always pull
# @latest so deploys and updates track upstream OpenClaw. We install even
# when the pre-built base image is present (its baked openclaw may be an
# older/forked build) — the npm install is the source of truth for the
# binary version. Requires Node 22.19+ (installed above / in the base image).
# taOS drives the agent over ACP (openclaw_acp_runtime), so no fork bridge
# is needed.
# ----------------------------------------------------------------------

echo "[openclaw] installing openclaw@latest from npm (upstream)"
if ! npm install -g --unsafe-perm openclaw@latest; then
  echo "[openclaw] FATAL: 'npm install -g openclaw@latest' failed"
  echo "[openclaw] check network connectivity to the npm registry from inside this container."
  exit 1
fi

if ! command -v openclaw >/dev/null 2>&1; then
  echo "[openclaw] FATAL: openclaw CLI not on PATH after install"
  exit 1
fi

echo "[openclaw] install OK: $(openclaw --version 2>/dev/null | head -1)"

# ------------------------------------------------------------------
# 2a. Bootstrap config + env for the openclaw bridge. Written from env
# vars the deployer set via `incus config set environment.*`.
# These live inside the container rootfs (not on the host) so they
# travel with snapshot-based archives cleanly.
# ------------------------------------------------------------------

mkdir -p /root/.openclaw
chmod 700 /root/.openclaw

# Resolve values or fall back to safe defaults for dev/test.
: "${TAOS_AGENT_NAME:=unknown}"
: "${TAOS_MODEL:=}"
: "${TAOS_FALLBACK_MODELS:=}"
: "${LITELLM_API_KEY:=}"
: "${OPENAI_API_KEY:=}"
: "${OPENAI_BASE_URL:=http://127.0.0.1:4000/v1}"
: "${TAOS_BRIDGE_URL:=http://127.0.0.1:6969}"
: "${TAOS_LOCAL_TOKEN:=}"

# Build the models[] JSON array from TAOS_MODEL + TAOS_FALLBACK_MODELS.
# Each entry: {"id":"<id>","name":"<id>","contextWindow":128000,"maxTokens":16384,"input":["text"],"reasoning":false}
_build_model_entry() {
  local id="$1"
  printf '{"id":"%s","name":"%s","contextWindow":128000,"maxTokens":16384,"input":["text"],"reasoning":false}' "$id" "$id"
}

MODELS_JSON="["
FIRST=1
if [ -n "$TAOS_MODEL" ]; then
  MODELS_JSON+="$(_build_model_entry "$TAOS_MODEL")"
  FIRST=0
fi
if [ -n "$TAOS_FALLBACK_MODELS" ]; then
  IFS=',' read -ra _FALLBACKS <<< "$TAOS_FALLBACK_MODELS"
  for _fb in "${_FALLBACKS[@]}"; do
    _fb="${_fb// /}"
    [ -z "$_fb" ] && continue
    [ "$_fb" = "$TAOS_MODEL" ] && continue
    [ "$FIRST" = "0" ] && MODELS_JSON+=","
    MODELS_JSON+="$(_build_model_entry "$_fb")"
    FIRST=0
  done
fi
MODELS_JSON+="]"

PRIMARY_REF=""
[ -n "$TAOS_MODEL" ] && PRIMARY_REF="litellm/${TAOS_MODEL}"

cat > /root/.openclaw/openclaw.json <<JSON_EOF
{
  "gateway": { "bind": "loopback", "port": 18789, "auth": { "mode": "token" }, "mode": "local" },
  "channels": {},
  "models": {
    "providers": {
      "litellm": {
        "api": "openai-completions",
        "baseUrl": "http://127.0.0.1:4000",
        "apiKey": "\${LITELLM_API_KEY}",
        "models": ${MODELS_JSON}
      }
    }
  },
  "agents": {
    "defaults": {
      "model": {
        "primary": "${PRIMARY_REF}"
      }
    }
  }
}
JSON_EOF
chmod 600 /root/.openclaw/openclaw.json

cat > /root/.openclaw/env <<ENV_EOF
TAOS_AGENT_NAME=${TAOS_AGENT_NAME}
TAOS_BRIDGE_URL=${TAOS_BRIDGE_URL}
TAOS_LOCAL_TOKEN=${TAOS_LOCAL_TOKEN}
TAOS_MODEL=${TAOS_MODEL}
TAOS_FALLBACK_MODELS=${TAOS_FALLBACK_MODELS}
LITELLM_API_KEY=${LITELLM_API_KEY}
OPENAI_API_KEY=${OPENAI_API_KEY}
OPENAI_BASE_URL=${OPENAI_BASE_URL}
ENV_EOF
chmod 600 /root/.openclaw/env

# ===== BEGIN recycle-bin install (Layer 1) — see app-catalog/_common/scripts/recycle-bin-install.sh =====
# Install taOS recycle-bin (Layer 1). Shared across agent frameworks.
echo "[recycle-bin] installing trash-cli and shadow rm wrapper"

# 1. trash-cli — baked into base image; skip the apt step when present.
if [ "$TAOS_BASE_IMAGE_PRESENT" != "1" ]; then
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends trash-cli
fi

# 2. Point trash-cli at /var/recycle-bin (XDG_DATA_HOME/Trash convention)
mkdir -p /var/recycle-bin/files /var/recycle-bin/info
chmod 1777 /var/recycle-bin /var/recycle-bin/files /var/recycle-bin/info

# Global environment so all shells (including systemd, sudo) pick it up.
cat > /etc/profile.d/taos-recycle-bin.sh <<'EOF'
# Point trash-cli at /var/recycle-bin (system-wide taOS recycle bin).
export XDG_DATA_HOME=/var
EOF
chmod 644 /etc/profile.d/taos-recycle-bin.sh

# 3. Shadow /usr/local/bin/rm
cat > /usr/local/bin/rm <<'EOF'
#!/usr/bin/env bash
# taOS shadow rm — soft-delete via trash-put unless TAOS_TRASH_DISABLE=1 set.
# Invoke /usr/bin/rm directly for permanent delete without this shadow.
set -euo pipefail
if [ "${TAOS_TRASH_DISABLE:-0}" = "1" ]; then
  exec /usr/bin/rm "$@"
fi
# Pass through if no args or only flags (trash-put rejects; /usr/bin/rm handles usage errors).
HAS_PATHS=0
for a in "$@"; do
  case "$a" in
    -*) ;;
    *)  HAS_PATHS=1; break ;;
  esac
done
if [ "$HAS_PATHS" = "0" ]; then
  exec /usr/bin/rm "$@"
fi
# Route only the path operands through trash-put. Flags (-r, -f, etc.) are ignored
# because trash-put's semantics differ; but -r recursion is default for directories
# and -f suppresses errors by our choice.
for arg in "$@"; do
  case "$arg" in
    -*) ;;
    *)  trash-put -- "$arg" 2>/dev/null || /usr/bin/rm -f -- "$arg" ;;
  esac
done
EOF
# Defensive: explicitly clear setuid/setgid bits even though 0755 wouldn't
# set them. Belt-and-suspenders against future edits accidentally bumping
# this to 4755/2755 — this is a root-shadow rm, so any SUID would be a
# textbook escalation primitive.
chmod 0755 /usr/local/bin/rm
chmod a-s /usr/local/bin/rm

# 4. 30-day retention sweep: /usr/local/bin/taos-recycle-sweep + systemd timer
cat > /usr/local/bin/taos-recycle-sweep <<'EOF'
#!/usr/bin/env bash
# Deletes items from /var/recycle-bin older than 30 days.
# Safe to run daily; idempotent.
set -euo pipefail
find /var/recycle-bin/files -mindepth 1 -mtime +30 -print0 2>/dev/null \
  | xargs -0 -r /usr/bin/rm -rf
find /var/recycle-bin/info -mindepth 1 -mtime +30 -type f -name '*.trashinfo' \
  -delete 2>/dev/null || true
EOF
chmod 755 /usr/local/bin/taos-recycle-sweep

cat > /etc/systemd/system/tinyagentos-recycle-sweep.service <<'EOF'
[Unit]
Description=taOS recycle-bin 30-day retention sweep

[Service]
Type=oneshot
ExecStart=/usr/local/bin/taos-recycle-sweep
EOF

cat > /etc/systemd/system/tinyagentos-recycle-sweep.timer <<'EOF'
[Unit]
Description=Daily taOS recycle-bin retention sweep

[Timer]
OnBootSec=30min
OnUnitActiveSec=24h
Persistent=true
Unit=tinyagentos-recycle-sweep.service

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now tinyagentos-recycle-sweep.timer

echo "[recycle-bin] ready; /usr/local/bin/rm now soft-deletes to /var/recycle-bin"
# ===== END recycle-bin install =====

# ---------------------------------------------------------------------------
# 3. systemd unit for the gateway.
# ---------------------------------------------------------------------------
cat > /etc/systemd/system/openclaw.service <<'UNIT'
[Unit]
Description=openclaw gateway
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
EnvironmentFile=-/root/.openclaw/env
ExecStart=/usr/bin/openclaw gateway
Restart=on-failure
RestartSec=3
WorkingDirectory=/root

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
# Enable but do NOT start — the deployer starts the service after writing
# the llm_key to the taOS config (required for the bootstrap endpoint to
# return 200). Starting here would cause the gateway to hit HTTP 409 on
# bootstrap and crash-loop until the deployer has written the key.
systemctl enable openclaw.service

echo "[openclaw] install complete (service enabled, start deferred to deployer)"

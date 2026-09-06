#!/usr/bin/env bash
# install-agent-zero.sh — agent-zero AGENT FRAMEWORK installer
# id: agent-zero | verification_status: alpha
#
# Runs once inside a fresh Debian bookworm LXC container as root.
# Idempotent: safe to re-run on an already-provisioned container.
#
# Upstream:        https://github.com/frdel/agent-zero  (redirects to agent0ai/agent-zero)
# Project:         Autonomous AI agent — self-correcting workflows, tool/skill
#                  creation, computer control. "A full Linux system for your agent."
# Install basis:   OFFICIAL README + docs/setup/dev-setup.md (developer/source install).
#   - README:      https://github.com/frdel/agent-zero/blob/main/README.md
#   - dev-setup:   https://github.com/frdel/agent-zero/blob/main/docs/setup/dev-setup.md
#   - requirements https://github.com/frdel/agent-zero/blob/main/requirements.txt
#   - entrypoint   https://github.com/frdel/agent-zero/blob/main/run_ui.py
#
# Official distribution is a Docker image (agent0ai/agent-zero; formerly
# frdel/agent-zero-run), run with:
#     docker run -p 80:80 -v a0_usr:/a0/usr agent0ai/agent-zero
# Inside an LXC we instead do the documented SOURCE install (git clone + Python
# venv + pip install -r requirements.txt), per docs/setup/dev-setup.md. Python
# >=3.11 is required; upstream recommends 3.12. The web UI is started with
# `python run_ui.py` (default host localhost; port via WEB_UI_PORT/runtime).
set -euo pipefail

# ---- pinned release ---------------------------------------------------------
# Pin to the latest tagged release (https://github.com/frdel/agent-zero/releases).
AGENT_ZERO_REF="v1.20"
AGENT_ZERO_REPO="https://github.com/agent0ai/agent-zero"
AGENT_ZERO_HOME="/opt/agent-zero"
AGENT_ZERO_VENV="${AGENT_ZERO_HOME}/.venv"

echo "[agent-zero] installing agent-zero ${AGENT_ZERO_REF} (source install)"

die() { echo "[agent-zero] FATAL: $*" >&2; exit 1; }

# ---- idempotency guard ------------------------------------------------------
# If a previous run completed (clone at the pinned ref + venv with deps), stop.
if [ -f "${AGENT_ZERO_HOME}/.taos-install-complete" ]; then
  echo "[agent-zero] already installed at ${AGENT_ZERO_HOME} (ref ${AGENT_ZERO_REF}); nothing to do"
  exit 0
fi

# ---------------------------------------------------------------------------
# 1. System deps. Debian bookworm ships Python 3.11 (satisfies upstream's
#    >=3.11 minimum). git to clone; build-essential + python3-dev for any
#    packages that compile; ffmpeg + libmagic are commonly used by the
#    framework's media/file tooling.
# ---------------------------------------------------------------------------
echo "[agent-zero] installing system dependencies (apt)"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq || die "apt-get update failed"
apt-get install -y -qq --no-install-recommends \
  git \
  python3 \
  python3-venv \
  python3-dev \
  python3-pip \
  build-essential \
  ca-certificates \
  curl \
  ffmpeg \
  libmagic1 \
  || die "apt-get install of system dependencies failed"

# Verify Python >=3.11 (upstream minimum; bookworm default is 3.11).
command -v python3 >/dev/null 2>&1 || die "python3 not on PATH after install"
PYVER="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
PYMAJ="${PYVER%%.*}"; PYMIN="${PYVER#*.}"
if [ "$PYMAJ" -lt 3 ] || { [ "$PYMAJ" -eq 3 ] && [ "$PYMIN" -lt 11 ]; }; then
  die "Python >=3.11 required, found ${PYVER}"
fi
echo "[agent-zero] using python ${PYVER}"

# ---------------------------------------------------------------------------
# 2. Clone the pinned release. Idempotent: if the dir already exists, fetch and
#    hard-check out the pinned ref instead of re-cloning.
#    Source: docs/setup/dev-setup.md — "git clone https://github.com/agent0ai/agent-zero"
# ---------------------------------------------------------------------------
if [ -d "${AGENT_ZERO_HOME}/.git" ]; then
  echo "[agent-zero] repo present; fetching + checking out ${AGENT_ZERO_REF}"
  git -C "${AGENT_ZERO_HOME}" fetch --tags --depth 1 origin "${AGENT_ZERO_REF}" \
    || die "git fetch of ${AGENT_ZERO_REF} failed"
  git -C "${AGENT_ZERO_HOME}" checkout --force "${AGENT_ZERO_REF}" \
    || die "git checkout of ${AGENT_ZERO_REF} failed"
else
  echo "[agent-zero] cloning ${AGENT_ZERO_REPO} @ ${AGENT_ZERO_REF}"
  git clone --depth 1 --branch "${AGENT_ZERO_REF}" "${AGENT_ZERO_REPO}" "${AGENT_ZERO_HOME}" \
    || die "git clone of ${AGENT_ZERO_REPO} @ ${AGENT_ZERO_REF} failed"
fi

[ -f "${AGENT_ZERO_HOME}/requirements.txt" ] || die "requirements.txt missing in clone"
[ -f "${AGENT_ZERO_HOME}/run_ui.py" ]        || die "run_ui.py missing in clone"

# ---------------------------------------------------------------------------
# 3. Python virtual environment + dependencies.
#    Source: docs/setup/dev-setup.md — venv + "pip install -r requirements.txt"
# ---------------------------------------------------------------------------
if [ ! -x "${AGENT_ZERO_VENV}/bin/python" ]; then
  echo "[agent-zero] creating virtualenv at ${AGENT_ZERO_VENV}"
  python3 -m venv "${AGENT_ZERO_VENV}" || die "venv creation failed"
fi

echo "[agent-zero] upgrading pip"
"${AGENT_ZERO_VENV}/bin/pip" install --upgrade pip setuptools wheel \
  || die "pip self-upgrade failed"

echo "[agent-zero] installing Python requirements (this can take a while)"
"${AGENT_ZERO_VENV}/bin/pip" install -r "${AGENT_ZERO_HOME}/requirements.txt" \
  || die "pip install -r requirements.txt failed"

# ---------------------------------------------------------------------------
# 4. Playwright Chromium — agent-zero drives a browser for computer control.
#    Source: docs/setup/dev-setup.md —
#    "PLAYWRIGHT_BROWSERS_PATH=./tmp/playwright playwright install chromium"
#    Best-effort: a missing browser doesn't block the framework from starting.
# ---------------------------------------------------------------------------
echo "[agent-zero] installing Playwright Chromium (best-effort)"
if "${AGENT_ZERO_VENV}/bin/python" -c 'import playwright' >/dev/null 2>&1; then
  PLAYWRIGHT_BROWSERS_PATH="${AGENT_ZERO_HOME}/tmp/playwright" \
    "${AGENT_ZERO_VENV}/bin/python" -m playwright install --with-deps chromium \
    || echo "[agent-zero] WARN: playwright chromium install failed; browser tools may be unavailable"
else
  echo "[agent-zero] WARN: playwright not present in requirements; skipping browser install"
fi

# ---------------------------------------------------------------------------
# 5. Verify the entrypoint imports cleanly (sanity, not a full boot).
# ---------------------------------------------------------------------------
echo "[agent-zero] verifying entrypoint is present"
[ -f "${AGENT_ZERO_HOME}/run_ui.py" ] || die "run_ui.py vanished after install"

# ---------------------------------------------------------------------------
# 6. systemd unit for the web UI. Started with `python run_ui.py` per
#    dev-setup.md; WEB_UI_HOST/WEB_UI_PORT are read by run_ui.py (run_ui.py
#    uses runtime.get_arg("host")/WEB_UI_HOST and runtime.get_web_ui_port()).
#    Enabled but NOT started — the deployer starts it after writing model/LLM
#    config, matching the openclaw installer convention.
# ---------------------------------------------------------------------------
cat > /etc/systemd/system/agent-zero.service <<UNIT
[Unit]
Description=agent-zero web UI
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${AGENT_ZERO_HOME}
Environment=WEB_UI_HOST=0.0.0.0
Environment=PLAYWRIGHT_BROWSERS_PATH=${AGENT_ZERO_HOME}/tmp/playwright
ExecStart=${AGENT_ZERO_VENV}/bin/python ${AGENT_ZERO_HOME}/run_ui.py
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
# Enable but do NOT start — deployer starts after writing LLM/model config.
systemctl enable agent-zero.service

# ---- completion marker (idempotency) ---------------------------------------
touch "${AGENT_ZERO_HOME}/.taos-install-complete"

echo "[agent-zero] install complete (ref ${AGENT_ZERO_REF}; service enabled, start deferred to deployer)"

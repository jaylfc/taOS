#!/usr/bin/env bash
# tinyagentos installer for WanGP / Wan2GP (https://github.com/deepbeepmeep/Wan2GP)
# AI video generation (Wan 2.1/2.2, HunyuanVideo, LTX, Flux). Apache-2.0.
# ---------------------------------------------------------------------------
# SERVICE script. Runs ON THE HOST (Linux x86 with NVIDIA CUDA most likely).
# Clones the repo, creates a Python 3.11 venv, installs CUDA torch + the
# upstream requirements, and writes a launcher that serves the Gradio UI on
# port 33109 (override with TAOS_WAN2GP_PORT).
#
# Source of truth — upstream README / docs (read 2026-06-14):
#   https://github.com/deepbeepmeep/Wan2GP
#   https://github.com/deepbeepmeep/Wan2GP/blob/main/docs/INSTALLATION.md
#   https://github.com/deepbeepmeep/Wan2GP/blob/main/docs/CLI.md
#
# Upstream pins (RTX 20xx-50xx profile):
#   Python 3.11.14
#   pip install torch==2.10.0 torchvision==0.25.0 torchaudio==2.10.0 \
#     --index-url https://download.pytorch.org/whl/cu130
#   pip install -r requirements.txt
#   python wgp.py            # Gradio default port 7860
#   flags: --server-port PORT, --server-name NAME, --listen
#
# CPU-only is unsupported upstream; this script targets x86 CUDA (ROCm users
# must override the torch index URL via TAOS_WAN2GP_TORCH_INDEX). The model
# weights are downloaded by wgp.py on first launch, not by this installer.
#
# Pinning / integrity:
#   We pin the git ref (TAOS_WAN2GP_REF) for a reproducible checkout, and rely
#   on pip's own version pins (torch trio + requirements.txt) for dependency
#   integrity. RESIDUAL RISK: upstream publishes no release tarball or detached
#   signature to checksum; the pinned commit + pinned pip versions are the
#   integrity guard. Update TAOS_WAN2GP_REF when bumping. Pinned: 2026-06-14.
# ---------------------------------------------------------------------------
set -euo pipefail

log() { echo -e "\033[1;34m[wan2gp]\033[0m $*"; }
die() { echo -e "\033[1;31m[wan2gp]\033[0m $*" >&2; exit 1; }

PROJECT_DIR="${1:?usage: install-wan2gp.sh <project_dir>}"

WAN2GP_REPO="${TAOS_WAN2GP_REPO:-https://github.com/deepbeepmeep/Wan2GP.git}"
# Pin a commit for a reproducible checkout. Set to a 40-char SHA to lock it;
# defaults to the upstream default branch when unset.
WAN2GP_REF="${TAOS_WAN2GP_REF:-cbef69f9f7e030b325bc56a611985c912c965a6b}"
WAN2GP_PORT="${TAOS_WAN2GP_PORT:-33109}"
WAN2GP_PY="${TAOS_WAN2GP_PYTHON:-python3.11}"
# Upstream RTX 20xx-50xx profile (docs/INSTALLATION.md). Override for ROCm/older GPUs.
TORCH_SPEC="${TAOS_WAN2GP_TORCH_SPEC:-torch==2.10.0 torchvision==0.25.0 torchaudio==2.10.0}"
TORCH_INDEX="${TAOS_WAN2GP_TORCH_INDEX:-https://download.pytorch.org/whl/cu130}"

SRC_DIR="$PROJECT_DIR/Wan2GP"
VENV_DIR="$SRC_DIR/.venv"
LAUNCHER="$PROJECT_DIR/run-wan2gp.sh"
STAMP="$VENV_DIR/.taos-installed"

# --- idempotency: already fully installed -> log + exit 0 --------------------
if [[ -f "$STAMP" ]]; then
    log "wan2gp already installed at $SRC_DIR (stamp present); nothing to do"
    log "launch with: $LAUNCHER   (serves on port $WAN2GP_PORT)"
    exit 0
fi

# --- prerequisites -----------------------------------------------------------
command -v git >/dev/null 2>&1 || die "git not found — install git first"
if ! command -v "$WAN2GP_PY" >/dev/null 2>&1; then
    die "$WAN2GP_PY not found — upstream requires Python 3.11 (set TAOS_WAN2GP_PYTHON to override)"
fi
log "using interpreter: $($WAN2GP_PY --version 2>&1)"

mkdir -p "$PROJECT_DIR"

# --- clone / update repo at pinned ref (idempotent) --------------------------
if [[ -d "$SRC_DIR/.git" ]]; then
    log "repo already present at $SRC_DIR; fetching"
    git -C "$SRC_DIR" fetch --depth 1 origin "$WAN2GP_REF"
else
    log "cloning $WAN2GP_REPO @ $WAN2GP_REF"
    git clone "$WAN2GP_REPO" "$SRC_DIR"
    git -C "$SRC_DIR" fetch --depth 1 origin "$WAN2GP_REF" || true
fi
log "checking out $WAN2GP_REF"
git -C "$SRC_DIR" checkout --quiet "$WAN2GP_REF"

[[ -f "$SRC_DIR/requirements.txt" ]] || die "requirements.txt missing in checkout — wrong ref?"
[[ -f "$SRC_DIR/wgp.py" ]]           || die "wgp.py missing in checkout — wrong ref?"

# --- python venv (idempotent) ------------------------------------------------
if [[ ! -x "$VENV_DIR/bin/python" ]]; then
    log "creating venv at $VENV_DIR"
    "$WAN2GP_PY" -m venv "$VENV_DIR"
else
    log "venv already exists at $VENV_DIR; reusing"
fi
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

log "upgrading pip"
python -m pip install --upgrade pip >/dev/null

# --- CUDA torch (upstream-pinned versions) -----------------------------------
log "installing torch (CUDA): $TORCH_SPEC  [index: $TORCH_INDEX]"
# shellcheck disable=SC2086
python -m pip install $TORCH_SPEC --index-url "$TORCH_INDEX"

# --- project requirements ----------------------------------------------------
log "installing requirements.txt"
python -m pip install -r "$SRC_DIR/requirements.txt"

deactivate

# --- launcher: serve Gradio on the chosen port -------------------------------
log "writing launcher $LAUNCHER (port $WAN2GP_PORT)"
cat > "$LAUNCHER" <<EOF
#!/usr/bin/env bash
# Auto-generated by install-wan2gp.sh — serves WanGP on TAOS_WAN2GP_PORT (default $WAN2GP_PORT).
set -euo pipefail
PORT="\${TAOS_WAN2GP_PORT:-$WAN2GP_PORT}"
cd "$SRC_DIR"
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
# --listen binds 0.0.0.0 so the service is reachable on the LAN (docs/CLI.md).
exec python wgp.py --server-port "\$PORT" --listen "\$@"
EOF
chmod +x "$LAUNCHER"

touch "$STAMP"

log "install complete"
log "  source : $SRC_DIR"
log "  venv   : $VENV_DIR"
log "  launch : $LAUNCHER   (Gradio on http://0.0.0.0:$WAN2GP_PORT)"
log "note: model weights download on first launch (needs network + disk)"

#!/usr/bin/env bash
# tinyagentos installer for the "ltx-video" service
# (Lightricks/LTX-Video — https://github.com/Lightricks/LTX-Video, Apache-2.0)
# ---------------------------------------------------------------------------
# Lightweight open-source AI video generation. Runs ON THE HOST.
#
# Tiers (informational; gating is done by the catalog, not this script):
#   x86-cuda 6GB+  -> full        (2B distilled 0.9.8 fits ~6GB VRAM)
#   rocm           -> full
#   vulkan         -> degraded
#   cpu            -> unsupported
#
# This script (based on the OFFICIAL README install path):
#   1. clones LTX-Video at a PINNED commit
#   2. creates a Python venv
#   3. pip installs CUDA torch + the package's [inference] extra
#   4. downloads the 2B distilled 0.9.8 weights from HuggingFace at a PINNED revision
#   5. installs a minimal HTTP server on TAOS_LTX_PORT (default 7861)
#
# Sources (verified 2026-06-14):
#   README install steps : https://github.com/Lightricks/LTX-Video#installation
#   pyproject (pkg name) : https://raw.githubusercontent.com/Lightricks/LTX-Video/main/pyproject.toml
#   model weights        : https://huggingface.co/Lightricks/LTX-Video
#
# NOTE: Upstream ships NO server/UI — the README directs local users to the
# CLI inference.py (or ComfyUI). The taOS port-7861 server below is a thin
# shim we add on top of the official, unmodified inference.py CLI.
#
# PORT COLLISION: 7861 is also used by the stable-diffusion-cpp service.
# Remap one of them (override here via TAOS_LTX_PORT) before running both.
# ---------------------------------------------------------------------------
set -euo pipefail

# --- pinned constants (update deliberately; verified 2026-06-14) ------------
# Upstream git tags are stale (latest tag ltx-video-0.9.1, Dec 2024) while the
# shipping model line is 0.9.8 — so we pin a commit SHA on main, not a tag.
LTX_REPO_URL="https://github.com/Lightricks/LTX-Video.git"
LTX_COMMIT="4b2d053057623ddd4d0a1d3e9cd28890e9ef487f"   # main @ 2026-01-05

# HuggingFace model repo + pinned revision (commit on main @ 2025-07-16)
LTX_HF_REPO="Lightricks/LTX-Video"
LTX_HF_REVISION="8984fa25007f376c1a299016d0957a37a2f797bb"
# 2B distilled 0.9.8 — ~6.34 GB, fits the 6GB-VRAM minimum tier.
LTX_MODEL_FILE="ltxv-2b-0.9.8-distilled.safetensors"

# CUDA torch wheel index (CUDA 12.x; README targets CUDA 12.2, torch>=2.1.2)
TORCH_INDEX_URL="https://download.pytorch.org/whl/cu121"

PORT="${TAOS_LTX_PORT:-36909}"

log() { echo -e "\033[1;34m[ltx-video]\033[0m $*"; }
die() { echo -e "\033[1;31m[ltx-video]\033[0m $*" >&2; exit 1; }

# --- args -------------------------------------------------------------------
PROJECT_DIR="${1:-}"
[[ -n "$PROJECT_DIR" ]] || die "usage: $0 <project_dir>   (taOS passes its project_dir as \$1)"
mkdir -p "$PROJECT_DIR"
PROJECT_DIR="$(cd "$PROJECT_DIR" && pwd)"

SERVICE_DIR="$PROJECT_DIR/ltx-video"
REPO_DIR="$SERVICE_DIR/LTX-Video"
VENV_DIR="$SERVICE_DIR/venv"
MODEL_DIR="$SERVICE_DIR/models"
SERVER_PY="$SERVICE_DIR/taos_server.py"
PY_BIN="$VENV_DIR/bin/python"
STAMP="$SERVICE_DIR/.installed"

# --- idempotency ------------------------------------------------------------
if [[ -f "$STAMP" ]]; then
    log "already installed at $SERVICE_DIR (stamp present) — nothing to do"
    log "start it with: TAOS_LTX_PORT=$PORT $PY_BIN $SERVER_PY"
    exit 0
fi

# --- prerequisites ----------------------------------------------------------
[[ "$(uname -s)" == "Linux" ]] || die "ltx-video service installs on Linux x86 hosts only; got $(uname -s)"
command -v git    >/dev/null 2>&1 || die "git not found — install git first"
command -v python3 >/dev/null 2>&1 || die "python3 not found — install Python 3.10+ first"

log "installing into $SERVICE_DIR"
mkdir -p "$SERVICE_DIR" "$MODEL_DIR"

# --- 1. clone repo at pinned commit (idempotent) ----------------------------
# README: git clone https://github.com/Lightricks/LTX-Video.git
if [[ ! -d "$REPO_DIR/.git" ]]; then
    log "cloning LTX-Video @ ${LTX_COMMIT:0:12}"
    git clone --filter=blob:none "$LTX_REPO_URL" "$REPO_DIR"
fi
git -C "$REPO_DIR" fetch --depth 1 origin "$LTX_COMMIT" 2>/dev/null || git -C "$REPO_DIR" fetch origin
git -C "$REPO_DIR" checkout -q "$LTX_COMMIT" || die "failed to checkout pinned commit $LTX_COMMIT"
log "checked out $(git -C "$REPO_DIR" rev-parse --short HEAD)"

# --- 2. python venv ---------------------------------------------------------
# README: python -m venv env && source env/bin/activate
if [[ ! -x "$PY_BIN" ]]; then
    log "creating venv at $VENV_DIR"
    python3 -m venv "$VENV_DIR"
fi
"$PY_BIN" -m pip install --quiet --upgrade pip wheel

# --- 3. install CUDA torch + the package's [inference] extra ----------------
# README requires PyTorch >= 2.1.2 w/ CUDA 12.x; install the CUDA wheel first
# so the editable [inference] install doesn't pull a CPU-only torch.
log "installing CUDA torch from $TORCH_INDEX_URL (this can take a while)"
"$PY_BIN" -m pip install --extra-index-url "$TORCH_INDEX_URL" "torch>=2.1.2" torchvision

# README: python -m pip install -e .[inference]   (package name: ltx-video)
log "installing ltx-video (editable) with [inference] extra"
"$PY_BIN" -m pip install -e "$REPO_DIR[inference]"

# huggingface_hub for a reproducible, revision-pinned weight download
"$PY_BIN" -m pip install --quiet "huggingface_hub>=0.23"

# --- 4. download model weights at a pinned revision -------------------------
# Weights live on HuggingFace Lightricks/LTX-Video. We pin by revision so the
# bytes are reproducible (README otherwise lets the pipeline auto-download).
MODEL_PATH="$MODEL_DIR/$LTX_MODEL_FILE"
if [[ ! -f "$MODEL_PATH" ]]; then
    log "downloading $LTX_MODEL_FILE @ rev ${LTX_HF_REVISION:0:12} (~6.3 GB)"
    "$PY_BIN" - "$LTX_HF_REPO" "$LTX_HF_REVISION" "$LTX_MODEL_FILE" "$MODEL_DIR" <<'PYEOF'
import sys
from huggingface_hub import hf_hub_download
repo, rev, fname, dest = sys.argv[1:5]
path = hf_hub_download(repo_id=repo, revision=rev, filename=fname,
                       local_dir=dest, local_dir_use_symlinks=False)
print(path)
PYEOF
else
    log "model already present: $MODEL_PATH"
fi

# --- 5. minimal HTTP server on $PORT (taOS shim over official inference.py) --
# Upstream ships no server. This stdlib-only wrapper exposes:
#   GET  /health           -> {"status":"ok"}
#   POST /generate {prompt,height,width,num_frames,seed}
# and shells out to the UNMODIFIED official inference.py for each job.
if [[ ! -f "$SERVER_PY" ]]; then
    log "writing taOS inference server shim -> $SERVER_PY"
    cat > "$SERVER_PY" <<'PYEOF'
#!/usr/bin/env python3
"""taOS port-7861 shim around Lightricks/LTX-Video's official inference.py.

Upstream provides no server; this stdlib HTTP wrapper invokes the unmodified
CLI per request. Not a fabrication of upstream features — just a launcher.
"""
import json
import os
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = int(os.environ.get("TAOS_LTX_PORT", "36909"))
HERE = Path(__file__).resolve().parent
REPO = HERE / "LTX-Video"
PY = sys.executable
OUT_DIR = HERE / "outputs"
OUT_DIR.mkdir(exist_ok=True)
# README-recommended distilled config for the 2B/13B distilled line.
PIPELINE_CONFIG = REPO / "configs" / "ltxv-13b-0.9.8-distilled.yaml"


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.rstrip("/") in ("/health", ""):
            self._send(200, {"status": "ok", "service": "ltx-video", "port": PORT})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path.rstrip("/") != "/generate":
            self._send(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            req = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError) as e:
            self._send(400, {"error": f"bad request: {e}"})
            return

        prompt = req.get("prompt")
        if not prompt:
            self._send(400, {"error": "'prompt' is required"})
            return

        out_path = OUT_DIR / f"ltx_{int(time.time())}.mp4"
        cmd = [
            PY, str(REPO / "inference.py"),
            "--prompt", str(prompt),
            "--height", str(req.get("height", 512)),
            "--width", str(req.get("width", 704)),
            "--num_frames", str(req.get("num_frames", 121)),
            "--seed", str(req.get("seed", 42)),
            "--pipeline_config", str(PIPELINE_CONFIG),
            "--output_path", str(out_path),
        ]
        proc = subprocess.run(cmd, cwd=str(REPO), capture_output=True, text=True)
        if proc.returncode != 0:
            self._send(500, {"error": "inference failed",
                             "stderr": proc.stderr[-2000:]})
            return
        self._send(200, {"status": "done", "output": str(out_path)})


if __name__ == "__main__":
    print(f"[ltx-video] serving on 0.0.0.0:{PORT}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
PYEOF
fi

# --- done -------------------------------------------------------------------
date -u +%Y-%m-%dT%H:%M:%SZ > "$STAMP"
log "install complete"
log "model:  $MODEL_PATH"
log "start:  TAOS_LTX_PORT=$PORT $PY_BIN $SERVER_PY"
log "health: curl http://127.0.0.1:$PORT/health"
log "NOTE: port $PORT collides with the stable-diffusion-cpp service — remap via TAOS_LTX_PORT if running both"
exit 0

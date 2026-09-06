#!/usr/bin/env bash
# tinyagentos installer for Dify (https://github.com/langgenius/dify)
# ---------------------------------------------------------------------------
# Dify — self-hosted LLMOps platform (RAG + agent builder, visual workflow).
# License: Apache-2.0. taOS app catalog id: dify (service).
#
# Runs ON THE HOST. Dify's only officially supported self-host path is Docker
# Compose: clone the repo at a pinned release tag, `cd docker`, copy
# .env.example -> .env, then `docker compose up -d`.
#
# Sources (verified 2026-06-14):
#   https://docs.dify.ai/en/getting-started/install-self-hosted/docker-compose
#   https://github.com/langgenius/dify/blob/main/docker/README.md
#   https://github.com/langgenius/dify/blob/main/docker/.env.example
#
# Web UI: the compose nginx gateway defaults to host port 80 via the
# EXPOSE_NGINX_PORT env var. taOS expects this service on port 35444, so we
# pin EXPOSE_NGINX_PORT=3000 in the generated .env (only when not already set).
#
# Pinned release tag — bump when validating a newer Dify release:
#   DIFY_VERSION (find latest at https://github.com/langgenius/dify/releases)
#   Pinned: 2026-06-14
# ---------------------------------------------------------------------------
set -euo pipefail

PROJECT_DIR="${1:?[dify] usage: install-dify.sh <project_dir>}"

DIFY_VERSION="${TAOS_DIFY_VERSION:-1.14.2}"
DIFY_REPO="https://github.com/langgenius/dify.git"
DIFY_PORT="${TAOS_DIFY_PORT:-35444}"

# Where the Dify source/compose stack lives, under the taOS project dir.
DIFY_HOME="${PROJECT_DIR%/}/services/dify"
DIFY_SRC="${DIFY_HOME}/dify"
DIFY_DOCKER_DIR="${DIFY_SRC}/docker"
# Compose project name — used to detect an already-running stack.
DIFY_COMPOSE_PROJECT="dify"

log() { echo -e "\033[1;34m[dify]\033[0m $*"; }
die() { echo -e "\033[1;31m[dify]\033[0m $*" >&2; exit 1; }

# --- prerequisites ---------------------------------------------------------
command -v git >/dev/null 2>&1 || die "git not found — install git and retry"
command -v docker >/dev/null 2>&1 || die "docker not found — install Docker Engine: https://docs.docker.com/engine/install/"

# Dify requires the Compose v2 plugin (`docker compose`, not legacy `docker-compose`).
if docker compose version >/dev/null 2>&1; then
    COMPOSE=(docker compose)
else
    die "'docker compose' (v2 plugin) not found — install it: https://docs.docker.com/compose/install/ (Dify requires Compose >= 2.24.0)"
fi

# --- idempotency: already running? -----------------------------------------
# If a dify compose project already has running containers, do nothing.
if running="$(docker ps --filter "label=com.docker.compose.project=${DIFY_COMPOSE_PROJECT}" --format '{{.Names}}' 2>/dev/null)" \
        && [[ -n "$running" ]]; then
    log "dify compose stack already up — skipping"
    log "running containers: $(echo "$running" | tr '\n' ' ')"
    log "web UI: http://localhost:${DIFY_PORT}"
    exit 0
fi

# --- fetch source at the pinned tag ----------------------------------------
if [[ -d "${DIFY_SRC}/.git" ]]; then
    log "dify source present at ${DIFY_SRC}; ensuring tag ${DIFY_VERSION} is checked out"
    git -C "$DIFY_SRC" fetch --depth 1 origin "refs/tags/${DIFY_VERSION}:refs/tags/${DIFY_VERSION}" 2>/dev/null \
        || git -C "$DIFY_SRC" fetch --tags origin
    git -C "$DIFY_SRC" checkout -q "tags/${DIFY_VERSION}" \
        || die "failed to checkout tag ${DIFY_VERSION}"
else
    log "cloning dify ${DIFY_VERSION} into ${DIFY_SRC}"
    mkdir -p "$DIFY_HOME"
    git clone --depth 1 --branch "$DIFY_VERSION" "$DIFY_REPO" "$DIFY_SRC" \
        || die "git clone failed for ${DIFY_REPO} @ ${DIFY_VERSION}"
fi

[[ -d "$DIFY_DOCKER_DIR" ]] || die "expected docker compose dir not found: ${DIFY_DOCKER_DIR}"

# --- generate .env (idempotent) --------------------------------------------
# Official step: cp .env.example .env. We only create it if absent, then make
# sure the nginx gateway is exposed on the taOS-expected port 35444.
ENV_FILE="${DIFY_DOCKER_DIR}/.env"
if [[ ! -f "$ENV_FILE" ]]; then
    [[ -f "${DIFY_DOCKER_DIR}/.env.example" ]] || die ".env.example missing in ${DIFY_DOCKER_DIR}"
    log "creating .env from .env.example"
    cp "${DIFY_DOCKER_DIR}/.env.example" "$ENV_FILE"
else
    log ".env already exists — leaving it untouched"
fi

# Pin the web UI to port ${DIFY_PORT} (compose default is 80 via EXPOSE_NGINX_PORT).
if grep -qE '^EXPOSE_NGINX_PORT=' "$ENV_FILE"; then
    current_port="$(grep -E '^EXPOSE_NGINX_PORT=' "$ENV_FILE" | head -1 | cut -d= -f2)"
    if [[ "$current_port" != "$DIFY_PORT" ]]; then
        log "setting EXPOSE_NGINX_PORT ${current_port} -> ${DIFY_PORT}"
        # portable in-place edit (GNU/BSD sed differ on -i)
        tmp="$(mktemp)"
        sed "s/^EXPOSE_NGINX_PORT=.*/EXPOSE_NGINX_PORT=${DIFY_PORT}/" "$ENV_FILE" > "$tmp" && mv "$tmp" "$ENV_FILE"
    else
        log "EXPOSE_NGINX_PORT already ${DIFY_PORT}"
    fi
else
    log "appending EXPOSE_NGINX_PORT=${DIFY_PORT} to .env"
    printf '\nEXPOSE_NGINX_PORT=%s\n' "$DIFY_PORT" >> "$ENV_FILE"
fi

# --- bring the stack up -----------------------------------------------------
log "starting dify compose stack (this pulls images on first run; may take a while)"
( cd "$DIFY_DOCKER_DIR" && "${COMPOSE[@]}" -p "$DIFY_COMPOSE_PROJECT" up -d ) \
    || die "'docker compose up -d' failed in ${DIFY_DOCKER_DIR}"

log "dify ${DIFY_VERSION} is up"
log "open the web UI at http://localhost:${DIFY_PORT} to finish admin setup"

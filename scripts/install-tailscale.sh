#!/usr/bin/env bash
# tinyagentos installer for Tailscale (https://tailscale.com)
# License: BSD-3-Clause  |  id: tailscale  |  type: service  |  no fixed port
# hardware_tiers: all (arm, x86, cpu-only) — full
# ---------------------------------------------------------------------------
# Zero-config VPN for remote access. Installs the Tailscale binary + daemon
# only; it does NOT run `tailscale up` and does NOT require an auth key. The
# user authenticates interactively afterwards (`tailscale up` / web login).
#
# Linux:  wraps the OFFICIAL installer https://tailscale.com/install.sh,
#         fetched over HTTPS and verified against the pinned SHA256
#         FAIL-CLOSED: a mismatch aborts. When upstream rotates the script,
#         an operator updates TAILSCALE_INSTALL_SHA256 (preferred) or sets
#         TAOS_TAILSCALE_ALLOW_UNPINNED=1 to override one run. The official
#         script auto-detects the distro/arch and configures the correct
#         apt/dnf/zypper/pacman/apk/etc. repo, covering every supported
#         arm + x86 distribution from one path.
# macOS:  no headless CLI install path — point the user at the Mac App Store
#         build (or `brew install --cask tailscale`).
#
# Receives the taOS project_dir as $1 (unused — install is host-global).
#
# Pinned constants — update when Tailscale revises their installer:
#   TAILSCALE_INSTALL_SHA256: SHA-256 of https://tailscale.com/install.sh
#   Verify with: curl -fsSL https://tailscale.com/install.sh | sha256sum
#   RESIDUAL RISK: Tailscale publishes no detached signature for this script,
#   so the pinned SHA256 is the integrity guard (verification is fail-closed;
#   HTTPS transport alone is not trusted). Pinned: 2026-06-14
# ---------------------------------------------------------------------------
set -euo pipefail

PROJECT_DIR="${1:-}"  # taOS project dir; not needed for a host-global install

TAILSCALE_INSTALL_SHA256="${TAOS_TAILSCALE_INSTALL_SHA256:-ada2fe9d54df0d3e5a77879470bda195b2c53d27ecd73aba6de270c795725625}"

log() { echo -e "\033[1;34m[tailscale]\033[0m $*"; }
die() { echo -e "\033[1;31m[tailscale]\033[0m $*" >&2; exit 1; }

verify_sha256() {
    local file="$1" expected="$2" label="$3" actual
    # Prefer sha256sum (Linux); fall back to shasum -a 256 (macOS).
    if command -v sha256sum >/dev/null 2>&1; then
        actual="$(sha256sum "$file" | awk '{print $1}')"
    elif command -v shasum >/dev/null 2>&1; then
        actual="$(shasum -a 256 "$file" | awk '{print $1}')"
    else
        die "no sha256 tool (sha256sum/shasum) available — cannot verify $label"
    fi
    if [[ "$actual" != "$expected" ]]; then
        die "sha256 mismatch for $label: expected $expected, got $actual — refusing to execute"
    fi
    log "sha256 ok for $label (${actual:0:16}…)"
}

# Idempotency: if the binary is already present, log version and exit clean.
if command -v tailscale >/dev/null 2>&1; then
    log "tailscale already installed: $(tailscale version 2>&1 | head -1)"
    log "skipping install; authenticate with 'sudo tailscale up' if not already connected"
    exit 0
fi

case "$(uname -s)" in
    Linux)
        # Official installer: https://tailscale.com/install.sh (source on GitHub
        # for transparency). Detects distro/arch and sets up the matching repo,
        # then installs the tailscale + tailscaled packages and enables the daemon.
        log "downloading official tailscale installer for verification"
        local_tmp="$(mktemp /tmp/tailscale-install.XXXXXX.sh)"
        trap 'rm -f "$local_tmp"' EXIT
        curl -fsSL https://tailscale.com/install.sh -o "$local_tmp"
        # Verify the installer's SHA256, FAIL-CLOSED by default: a mismatch
        # aborts the install. Tailscale rotates install.sh periodically; when
        # that happens an operator who has reviewed the new script should update
        # TAILSCALE_INSTALL_SHA256 (preferred), or set
        # TAOS_TAILSCALE_ALLOW_UNPINNED=1 to proceed past the mismatch for one
        # run. The HTTPS fetch is NOT a substitute for the pin.
        if command -v sha256sum >/dev/null 2>&1; then
            _ts_actual="$(sha256sum "$local_tmp" | awk '{print $1}')"
        else
            _ts_actual="$(shasum -a 256 "$local_tmp" | awk '{print $1}')"
        fi
        if [[ "$_ts_actual" == "$TAILSCALE_INSTALL_SHA256" ]]; then
            log "sha256 ok for tailscale-install.sh (${_ts_actual:0:16})"
        elif [[ "${TAOS_TAILSCALE_ALLOW_UNPINNED:-0}" == "1" ]]; then
            log "WARN: tailscale-install.sh sha256 mismatch (${_ts_actual:0:16}); proceeding because TAOS_TAILSCALE_ALLOW_UNPINNED=1"
        else
            die "tailscale-install.sh sha256 mismatch: expected $TAILSCALE_INSTALL_SHA256, got $_ts_actual. Upstream likely rotated the installer: review it and update TAILSCALE_INSTALL_SHA256, or set TAOS_TAILSCALE_ALLOW_UNPINNED=1 to override this run."
        fi
        sh "$local_tmp"
        rm -f "$local_tmp"
        trap - EXIT

        # The installer enables tailscaled via systemd where present; make sure
        # it's up on systemd hosts (best-effort — non-systemd hosts are skipped).
        if command -v systemctl >/dev/null 2>&1; then
            sudo systemctl enable --now tailscaled 2>/dev/null || \
                log "could not enable tailscaled via systemd (may already be managed)"
        fi
        ;;
    Darwin)
        # No official headless CLI installer for macOS. The supported builds are
        # the Mac App Store app (https://tailscale.com/download/mac) or Homebrew.
        if command -v brew >/dev/null 2>&1; then
            log "installing tailscale via Homebrew cask"
            brew install --cask tailscale || \
                die "brew install failed — install Tailscale from the Mac App Store: https://tailscale.com/download/mac"
        else
            die "macOS: install Tailscale from the Mac App Store (https://tailscale.com/download/mac) or via 'brew install --cask tailscale'"
        fi
        ;;
    *)
        die "tailscale installer doesn't support $(uname -s) yet — see https://tailscale.com/download"
        ;;
esac

# Re-resolve PATH so a freshly installed binary is found in this same run.
hash -r 2>/dev/null || true

if command -v tailscale >/dev/null 2>&1; then
    log "tailscale installed: $(tailscale version 2>&1 | head -1)"
    log "daemon installed. Authenticate when ready: 'sudo tailscale up' (no auth key required)"
else
    log "tailscale installed; binary not yet on PATH for this shell — open a new shell, then 'sudo tailscale up'"
fi

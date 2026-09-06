# Worker AppImage Packaging — Design Spec

## Overview

Ship the taOS worker as a single AppImage binary instead of cloning the git repo and resolving system dependencies at install time. Eliminates the entire class of "libtorrent-rasterbar-devel missing on Fedora 43" / "boost-python too new on Arch" / "python 3.14 breaks pip wheels" failures we keep hitting.

## Problem

Current install flow requires:
- `sudo` for system packages
- OS-level libtorrent, boost, python dev headers
- Git + ~200MB clone of the full repo (most of which the worker doesn't need)
- pip install of libtorrent (often no wheel available → builds from source)
- Bundled ollama tarball download from GitHub releases

Each step is a failure point that varies per distro. Already broken on Fedora 43 (dropped libtorrent-rasterbar-devel). Will break again on every new Fedora/Ubuntu release until someone manually patches the script.

## Design

### AppImage contents

```
taos-worker-x86_64.AppImage
├── AppRun                    # entry point, sets LD_LIBRARY_PATH etc.
├── taos-worker.desktop       # metadata
├── taos-worker.png           # icon
└── usr/
    ├── bin/
    │   ├── python3           # bundled Python (matches build target)
    │   └── ollama            # bundled ollama binary
    ├── lib/
    │   ├── python3.x/        # stdlib + vendored deps
    │   └── libtorrent.so.*   # bundled native libs
    └── share/
        └── taos-worker/      # tinyagentos python package + worker entrypoint
```

One 150-200MB file, runs on any x86_64 Linux. ARM64 variant for Orange Pi etc.

### Build

Use `appimage-builder` or `linuxdeploy` + `python-appimage`. Pinned to a reproducible base (e.g. Ubuntu 22.04 so glibc ≥ 2.35 covers most targets) and cross-compiled for ARM64 separately.

CI pipeline:
```
.github/workflows/worker-appimage.yml
  matrix: [x86_64, aarch64]
  - Build python venv with worker-only deps
  - Bundle ollama binary for the arch
  - Package as AppImage
  - Upload to release artifacts
```

Release cadence: tagged with the controller version (`v1.0.0-worker-x86_64.AppImage`).

### Install script becomes trivial

```bash
# install-worker.sh (new)
set -e
ARCH=$(uname -m)
URL="https://github.com/jaylfc/tinyagentos/releases/latest/download/taos-worker-${ARCH}.AppImage"
INSTALL_DIR=/opt/taos-worker
sudo mkdir -p "$INSTALL_DIR"
sudo curl -fsSL "$URL" -o "$INSTALL_DIR/taos-worker.AppImage"
sudo chmod +x "$INSTALL_DIR/taos-worker.AppImage"
# Create systemd unit
sudo tee /etc/systemd/system/taos-worker.service <<EOF
[Unit]
Description=TinyAgentOS Worker
After=network-online.target
[Service]
ExecStart=$INSTALL_DIR/taos-worker.AppImage --controller $CONTROLLER_URL --name $WORKER_NAME
Restart=always
User=$SUDO_USER
[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now taos-worker
```

~10 lines vs 700+ today. No dnf/apt/pacman logic. No Python detection. No pip. No clone.

### Updates

AppImage supports in-place updates via the AppImageUpdate protocol, but simpler: the worker can self-update by downloading the latest AppImage into its install dir and restarting itself via systemd.

GPU/NPU access is unchanged because AppImage doesn't sandbox — drivers on the host are accessible normally.

## Why not Flatpak / Snap / PyInstaller

- **Flatpak**: sandboxed (portal permissions would block direct GPU/NPU access), designed for GUI apps with desktop integration, heavyweight runtime dependency.
- **Snap**: Ubuntu-centric, mediocre support on Fedora/Arch, sandbox issues with NVIDIA drivers, daily updates forced.
- **PyInstaller**: works for pure Python but libtorrent and bundled ollama make it awkward. AppImage supports these naturally.
- **Docker**: already supported via the existing worker image. AppImage is the native-Linux complement for users who don't want a container runtime.

## Out of Scope

- macOS worker packaging — separate (.dmg or .pkg installer)
- Windows worker — already has its own installer via .ps1 script
- Android worker — Termux install is sufficient
- Multiple arch variants beyond x86_64 and aarch64

## Open Questions

- Do we want to sign the AppImage with a GPG key so users can verify? Standard for AppImages but adds CI complexity.
- Where does the worker's data dir live? `/var/lib/taos-worker` (needs sudo) or `~/.local/share/taos-worker` (per-user)? Probably the latter with the systemd unit running as the invoking user.

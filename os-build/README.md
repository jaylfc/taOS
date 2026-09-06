# TinyAgentOS — Armbian Image Build

Pre-built OS images for supported SBCs, using the Armbian build framework with
TinyAgentOS userpatches overlaid on top.

This directory is **not** a fork of armbian/build. It contains only the
userpatches, extensions, overlay files, and convenience scripts that get copied
into the Armbian build tree before compilation.

## Quick Start

```bash
# From the tinyagentos repo root
cd os-build
./build.sh orangepi5plus
```

The script clones the Armbian build framework (if not already present) into
`../armbian-build/`, copies the userpatches, and starts the build.

## Manual Build

```bash
# Clone Armbian build framework
git clone --depth 1 https://github.com/armbian/build armbian-build
cd armbian-build

# Copy TinyAgentOS userpatches
cp -r ../tinyagentos/os-build/userpatches .

# Build for Orange Pi 5 Plus
./compile.sh BOARD=orangepi5-plus BRANCH=vendor RELEASE=bookworm BUILD_DESKTOP=no \
  ENABLE_EXTENSIONS=tinyagentos
```

## Supported Boards

| Board | SoC | NPU | Status |
|-------|-----|-----|--------|
| Orange Pi 5 Plus | RK3588 | 6 TOPS RKNN | Primary target |
| Orange Pi 5 | RK3588S | 6 TOPS RKNN | Supported |
| Rock 5B | RK3588 | 6 TOPS RKNN | Supported |
| Raspberry Pi 5 | BCM2712 | None (Hailo via M.2) | Planned |

See `boards/` for per-board details and build commands.

## Directory Layout

```
os-build/
├── build.sh                        # Convenience build wrapper
├── README.md                       # This file
├── boards/                         # Per-board config docs
│   ├── orangepi5plus.md
│   ├── orangepi5.md
│   ├── rock5b.md
│   └── rpi5.md
└── userpatches/
    ├── customize-image.sh          # Runs inside chroot during build
    ├── extensions/
    │   └── tinyagentos.sh          # Armbian build extension
    └── overlay/                    # Files copied into image filesystem
        └── etc/
            ├── motd
            ├── skel/.bashrc
            └── systemd/system/tinyagentos.service
```

## Build Output

Images are written to `armbian-build/output/images/` and include:
- `.img.xz` — compressed image
- `.img.xz.sha` — checksum
- `.img.xz.asc` — GPG signature (if keys are configured)

## Requirements

- Debian/Ubuntu build host (or any Linux with Docker)
- ~40 GB free disk space
- Root privileges (Armbian build uses chroot)

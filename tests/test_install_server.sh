#!/usr/bin/env bash
# Regression tests for install-server.sh Intel GPU Mesa Vulkan driver support.
# Verifies: syntax, vulkan-tools baseline, mesa-vulkan-drivers for apt/dnf,
# vulkan-intel for pacman, mesa-vulkan-intel for apk, and correct ordering
# (Intel detection MUST precede the mesa install).
set -euo pipefail
SCRIPT=scripts/install-server.sh

echo "test: bash -n syntax"
bash -n "$SCRIPT"

echo "test: ensure_linux_deps includes vulkan-tools in apt path"
grep -q "vulkan-tools" "$SCRIPT"

echo "test: Intel GPU block installs mesa-vulkan-drivers via apt when available"
grep -q "apt-cache show mesa-vulkan-drivers" "$SCRIPT"

echo "test: Intel GPU block installs mesa-vulkan-drivers via dnf when available"
grep -q "dnf.*mesa-vulkan-drivers" "$SCRIPT"

echo "test: Intel GPU block installs vulkan-intel via pacman when available"
grep -q "vulkan-intel" "$SCRIPT"
grep -q "vulkan-mesa-layers" "$SCRIPT"

echo "test: Intel GPU block installs mesa-vulkan-intel via apk when available"
grep -q "mesa-vulkan-intel" "$SCRIPT"

echo "test: Intel GPU detection runs before mesa install (ordering)"
lspci_line=$(grep -n 'lspci.*Intel Corporation' "$SCRIPT" | head -1 | cut -d: -f1)
mesa_line=$(grep -n 'apt-cache show mesa-vulkan-drivers' "$SCRIPT" | head -1 | cut -d: -f1)
(( lspci_line < mesa_line ))

echo "all tests passed"

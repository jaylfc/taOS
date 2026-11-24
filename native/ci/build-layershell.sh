#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Build LayerShellQt (and the extra-cmake-modules it needs) from pinned source
# against the given Qt, into a private prefix. Hosted Ubuntu ships neither a
# LayerShellQt nor an ECM that is new enough for a Qt 6.12 build.
#   native/ci/build-layershell.sh <qt prefix> <install prefix>
set -euo pipefail
QT=${1:?usage: build-layershell.sh <qt prefix> <install prefix>}
PREFIX=${2:?usage: build-layershell.sh <qt prefix> <install prefix>}
ECM_TAG=${ECM_TAG:-v6.26.0}
LSQT_TAG=${LSQT_TAG:-v6.6.0}
if [ -f "$PREFIX/lib/cmake/LayerShellQt/LayerShellQtConfig.cmake" ] || ls "$PREFIX"/lib/*/cmake/LayerShellQt/LayerShellQtConfig.cmake >/dev/null 2>&1; then
  echo "LayerShellQt already present in $PREFIX"
  exit 0
fi
W=$(mktemp -d)
# Qt 6.12's private Wayland headers name struct wl_fixes (libwayland 1.24); Ubuntu has 1.22.
echo 'struct wl_fixes;' > "$W/shim.h"
git clone --quiet --depth 1 --branch "$ECM_TAG" https://invent.kde.org/frameworks/extra-cmake-modules.git "$W/ecm"
cmake -S "$W/ecm" -B "$W/ecm-b" -G Ninja -DCMAKE_INSTALL_PREFIX="$PREFIX" -DBUILD_TESTING=OFF -DBUILD_HTML_DOCS=OFF -DBUILD_MAN_DOCS=OFF -DBUILD_QTHELP_DOCS=OFF
cmake --install "$W/ecm-b"
git clone --quiet --depth 1 --branch "$LSQT_TAG" https://invent.kde.org/plasma/layer-shell-qt.git "$W/lsqt"
cmake -S "$W/lsqt" -B "$W/lsqt-b" -G Ninja -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_PREFIX_PATH="$QT;$PREFIX" -DCMAKE_INSTALL_PREFIX="$PREFIX" \
  -DCMAKE_CXX_FLAGS="-include $W/shim.h" -DBUILD_TESTING=OFF -DBUILD_WITH_QT6=ON
cmake --build "$W/lsqt-b" --parallel
cmake --install "$W/lsqt-b"
rm -rf "$W"

#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# Install Qt (base only: Core Gui Qml Quick QuickControls2 QuickShapes Test Network DBus,
# qmllint, and the private Wayland client headers LayerShellQt needs) with aqtinstall.
#   native/ci/install-qt.sh <qt root dir>
# The result is <qt root>/<version>/gcc_64, the CMAKE_PREFIX_PATH the builds use.
set -euo pipefail
ROOT=${1:?usage: install-qt.sh <qt root dir>}
QT_VERSION=${QT_VERSION:-6.12.1}
AQT_VERSION=${AQT_VERSION:-3.3.0}
if [ -x "$ROOT/$QT_VERSION/gcc_64/bin/qmllint" ]; then
  echo "Qt $QT_VERSION already present in $ROOT"
  exit 0
fi
V=$(mktemp -d)
python3 -m venv "$V"
"$V/bin/pip" install --quiet "aqtinstall==$AQT_VERSION"
"$V/bin/aqt" install-qt linux desktop "$QT_VERSION" linux_gcc_64 -O "$ROOT"
rm -rf "$V"

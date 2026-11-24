#!/usr/bin/env bash
# Build the Qt layer and run its tests. Run from anywhere.
#   native/qt/check.sh [extra cmake args]
# Needs: cmake, ninja, Qt 6.8+ dev files (Core Gui Network Test), cargo, python3.
# Qt Quick is optional (the demo is built only when found).
set -euo pipefail
cd "$(dirname "$0")"
export PATH="$HOME/.cargo/bin:$PATH"
B="${TAOS_QT_BUILD_DIR:-build}"
cmake -S . -B "$B" -G Ninja -DCMAKE_BUILD_TYPE=Release "$@"
nice cmake --build "$B" -j"${JOBS:-2}"
QT_QPA_PLATFORM=offscreen ctest --test-dir "$B" --output-on-failure -j1

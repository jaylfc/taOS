#!/usr/bin/env bash
# Build the native apps and run their tests (offscreen). Needs the same
# toolchain as native/qt/check.sh.
#   QT_PREFIX=/path/to/qt/gcc_64 BUILD_DIR=/path JOBS=1 native/apps/check.sh
set -euo pipefail
cd "$(dirname "$0")"
export PATH="$HOME/.cargo/bin:$PATH"
B="${BUILD_DIR:-build}"
cmake -S . -B "$B" -G Ninja -DCMAKE_BUILD_TYPE=Release -DTAOS_CARGO_OFFLINE=ON \
  ${QT_PREFIX:+-DCMAKE_PREFIX_PATH=$QT_PREFIX} ${CARGO_TARGET:+-DTAOS_CARGO_TARGET_DIR=$CARGO_TARGET} "$@"
nice cmake --build "$B" -j"${JOBS:-1}"
export XDG_RUNTIME_DIR="$(mktemp -d)"
QT_QPA_PLATFORM=offscreen ctest --test-dir "$B" --output-on-failure -j1 -R '^(apps_|tst_apps)'

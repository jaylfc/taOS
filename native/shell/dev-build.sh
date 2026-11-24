#!/bin/sh
# Configure and build the taOS shell components against a given Qt prefix and
# LayerShellQt prefix. No machine specific paths live in this repository.
#
#   dev-build.sh <qt-prefix> <layershell-prefix> [build-dir]
#
# or through the environment: QT_PREFIX, LAYERSHELL_PREFIX, BUILD_DIR.
# Other environment:
#   TAOS_EXTRA_CXX_FLAGS  extra compiler flags (for example "-include shim.h" for
#                         the libwayland 1.23 vs Qt 6.12 `struct wl_fixes` gap)
#   JOBS                  parallel jobs (default 8, never more)
#   RUN_TESTS=1           run ctest after the build
set -eu
here=$(cd "$(dirname "$0")" && pwd)
QT_PREFIX=${1:-${QT_PREFIX:-}}
LAYERSHELL_PREFIX=${2:-${LAYERSHELL_PREFIX:-}}
BUILD_DIR=${3:-${BUILD_DIR:-$here/build}}
if [ -z "$QT_PREFIX" ] || [ -z "$LAYERSHELL_PREFIX" ]; then
    echo "usage: $0 <qt-prefix> <layershell-prefix> [build-dir]" >&2
    exit 2
fi
JOBS=${JOBS:-8}
[ "$JOBS" -gt 8 ] && JOBS=8
gen=""
command -v ninja >/dev/null 2>&1 && gen="-G Ninja"
# shellcheck disable=SC2086
cmake -S "$here" -B "$BUILD_DIR" $gen \
    -DCMAKE_BUILD_TYPE=RelWithDebInfo \
    -DCMAKE_PREFIX_PATH="$QT_PREFIX;$LAYERSHELL_PREFIX" \
    -DCMAKE_CXX_FLAGS="${TAOS_EXTRA_CXX_FLAGS:-}"
cmake --build "$BUILD_DIR" --parallel "$JOBS"
if [ "${RUN_TESTS:-0}" = 1 ]; then
    (cd "$BUILD_DIR" && ctest --output-on-failure)
fi
echo "built: $BUILD_DIR/bar/taos-bar"
echo "run it with LD_LIBRARY_PATH=<qt>/lib:<layershell>/lib/<arch> and QT_PLUGIN_PATH=<layershell plugins>:<qt>/plugins"

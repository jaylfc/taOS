#!/bin/sh
# Usage: ./build.sh <alpine|debian>   (run on the arm64 host from the qt/ dir;
# ../common/poc-launch.c must sit next to it.) Builds the dev image once, then
# configures and builds Release with ninja inside it. Output: out/<base>/.
set -eu
base="${1:-}"
case "$base" in
alpine|debian) ;;
*) echo "usage: $0 <alpine|debian>" >&2; exit 2 ;;
esac
here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/.." && pwd)
img="poc-qt-$base"

if ! docker image inspect "$img" >/dev/null 2>&1; then
  echo "== building image $img"
  if [ "$base" = alpine ]; then
    docker build -t "$img" - <<'DF'
FROM alpine:edge
RUN apk add --no-cache build-base pkgconf cmake samurai wayland-dev wayland-protocols \
    qt6-qtbase-dev qt6-qtbase-wayland qt6-qtdeclarative-dev qt6-qtdeclarative \
    layer-shell-qt-dev layer-shell-qt
DF
  else
    docker build -t "$img" - <<'DF'
FROM debian:trixie
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential pkg-config cmake ninja-build libwayland-dev wayland-protocols \
    qt6-base-dev qt6-declarative-dev qt6-wayland qt6-wayland-dev qt6-qpa-plugins \
    liblayershellqtinterface-dev qml6-module-qtqml qml6-module-qtqml-workerscript \
    qml6-module-qtquick qml6-module-qtquick-window qml6-module-qtquick-templates \
    qml6-module-qtquick-controls qml6-module-qtquick-shapes qml6-module-qtquick-effects \
    qml6-module-qtquick-layouts \
 && rm -rf /var/lib/apt/lists/*
DF
  fi
fi

rm -rf "$here/build-$base" "$here/out/$base"
mkdir -p "$here/out/$base"
docker run --rm --cpus 6 --user "$(id -u):$(id -g)" -v "$root:/w" -w /w/qt "$img" sh -ec "
  cmake -S . -B build-$base -G Ninja -DCMAKE_BUILD_TYPE=Release
  cmake --build build-$base
  cp build-$base/taos-poc-qt out/$base/taos-poc-qt
  cc -O2 -o out/$base/poc-launch ../common/poc-launch.c
  strip out/$base/taos-poc-qt out/$base/poc-launch
"
ls -l "$here/out/$base"

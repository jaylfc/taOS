#!/bin/sh
# Usage: ./build.sh <alpine|debian>
# Run on the arm64 host from ~/poc-build/gtk (with ~/poc-build/common next to it).
# Builds image poc-gtk-<base> if missing, builds release --offline --locked against
# ./vendor, copies the binary to out/<base>/taos-poc-gtk, and ALSO compiles
# ../common/poc-launch.c into out/<base>/poc-launch with `cc -O2 -o`, inside the same container.
set -eu
base="${1:-}"
case "$base" in alpine|debian) ;; *) echo "usage: $0 <alpine|debian>" >&2; exit 2;; esac
here="$(cd "$(dirname "$0")" && pwd)"
parent="$(dirname "$here")"
img="poc-gtk-$base"
cd "$here"
if ! docker image inspect "$img" >/dev/null 2>&1; then
    docker build -t "$img" -f "docker/Dockerfile.$base" docker
fi
mkdir -p "out/$base"
docker run --rm --cpus 6 --user "$(id -u):$(id -g)" \
    -e HOME=/tmp -e CARGO_HOME=/tmp/cargo -e CARGO_TARGET_DIR="/work/gtk/target/$base" \
    -v "$parent:/work" -w /work/gtk "$img" sh -ec '
    rustc --version; cargo --version
    cargo build --release --offline --locked
    cp "target/'"$base"'/release/taos-poc-gtk" "out/'"$base"'/taos-poc-gtk"
    cc -O2 -o "out/'"$base"'/poc-launch" ../common/poc-launch.c
'
ls -l "out/$base"

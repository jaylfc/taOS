#!/bin/sh
# Usage: ./build.sh <alpine|debian>  (run on the arm64 host in this directory, which must hold
# vendor/ and ../common/). Builds offline inside poc-qtrust-<base>. Output: out/<base>/.
set -eu
base="${1:-}"
case "$base" in alpine|debian) ;; *) echo "usage: $0 <alpine|debian>" >&2; exit 2 ;; esac
here=$(cd "$(dirname "$0")" && pwd)
img="poc-qtrust-$base"
[ -n "${CLEAN:-}" ] && rm -rf "$here/target-$base"
rm -rf "$here/out/$base"; mkdir -p "$here/out/$base"
docker run --rm --cpus 6 --user "$(id -u):$(id -g)" -v "$here:/w" -w /w "$img" sh -ec "
  export QMAKE=\$(command -v qmake6) CARGO_HOME=/tmp/cargo CARGO_TARGET_DIR=/w/target-$base
  cargo build --release --offline
  cp target-$base/release/taos-poc-qt-rust out/$base/
"
ls -l "$here/out/$base"

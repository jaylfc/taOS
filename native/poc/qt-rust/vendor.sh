#!/bin/sh
# Regenerates Cargo.lock + vendor/ (needs network; run once, on any host with docker). Resolution is
# MSRV-aware (rust-version = 1.85 in Cargo.toml), so the stock Debian trixie rustc 1.85.1 builds it.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
rm -rf "$here/vendor" "$here/Cargo.lock"
docker run --rm --user "$(id -u):$(id -g)" -e CARGO_HOME=/tmp/cargo -e CARGO_NET_OFFLINE=false \
  -v "$here:/w" -w /w poc-qtrust-alpine sh -ec '
  mv .cargo .cargo.off; trap "mv .cargo.off .cargo" EXIT
  CARGO_RESOLVER_INCOMPATIBLE_RUST_VERSIONS=fallback cargo vendor vendor >/dev/null'
du -sh "$here/vendor"

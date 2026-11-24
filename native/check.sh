#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-or-later
# CI-friendly checks for the native workspace. Run from anywhere.
#
#   native/check.sh
#
# Runs fmt, clippy (warnings are errors), the tests, and, when the 1.85
# toolchain is installed, builds with the declared minimum Rust version
# (install it with: rustup toolchain install 1.85 --profile minimal).
set -euo pipefail

cd "$(dirname "$0")"
CARGO="${CARGO:-cargo}"
if ! command -v "$CARGO" >/dev/null 2>&1 && [ -x "$HOME/.cargo/bin/cargo" ]; then
  CARGO="$HOME/.cargo/bin/cargo"
fi

echo "== fmt"
"$CARGO" fmt --check
echo "== clippy"
"$CARGO" clippy --all-targets -- -D warnings
echo "== test"
"$CARGO" test
echo "== msrv (1.85)"
if "$CARGO" +1.85 --version >/dev/null 2>&1; then
  "$CARGO" +1.85 check --all-targets
  echo "msrv: ok"
else
  echo "msrv: skipped (toolchain 1.85 not installed)"
fi

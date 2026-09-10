#!/usr/bin/env bash
# bats test suite for Sparkle framework integration fixes.
# Tests the three defects fixed in BASE.
# Run with: bats tests/sparkle_tests.bats

REPO_ROOT="$(cd "$(dirname "$BATS_TEST_FILENAME")/.." && pwd)"

setup() {
    set -u
    BATS_TEST_TMPDIR="$(mktemp -d -t sparkle-tests-XXXXXX)"
    export BATS_TEST_TMPDIR
    export PATH="$BATS_TEST_TMPDIR/bin:$PATH"
    mkdir -p "$BATS_TEST_TMPDIR/bin"

    cat > "$BATS_TEST_TMPDIR/bin/curl" <<'CURL'
#!/usr/bin/env bash
while [[ $# -gt 0 ]]; do
    case "$1" in
        -o) dst="$2"; shift 2 ;;
        *) shift ;;
    esac
done
touch "$dst"
CURL
    chmod +x "$BATS_TEST_TMPDIR/bin/curl"

    cat > "$BATS_TEST_TMPDIR/bin/unzip" <<'UNZIP'
#!/usr/bin/env bash
while [[ $# -gt 0 ]]; do
    case "$1" in
        -o) shift ;;
        -d) dest="$2"; shift 2 ;;
        *.zip) zipfile="$1"; shift ;;
        *) shift ;;
    esac
done
mockdir="${zipfile%.zip}"
if [[ -d "$mockdir" ]]; then
    mkdir -p "$dest"
    cp -R "$mockdir"/* "$dest/" 2>/dev/null || true
fi
UNZIP
    chmod +x "$BATS_TEST_TMPDIR/bin/unzip"

    cat > "$BATS_TEST_TMPDIR/bin/shasum" <<'SHASUM'
#!/usr/bin/env bash
echo "a5088d48a37ba415081335502e009dece75acae9d130705fee6c6988b90d0877  $1"
SHASUM
    chmod +x "$BATS_TEST_TMPDIR/bin/shasum"

    cat > "$BATS_TEST_TMPDIR/bin/sha256sum" <<'SHA256SUM'
#!/usr/bin/env bash
echo "a5088d48a37ba415081335502e009dece75acae9d130705fee6c6988b90d0877  $1"
SHA256SUM
    chmod +x "$BATS_TEST_TMPDIR/bin/sha256sum"
}

teardown() {
    rm -rf "$BATS_TEST_TMPDIR"
}

@test "fetch_sparkle.sh extracts the xcframework layout" {
    local staging_dir="$BATS_TEST_TMPDIR/staging"
    mkdir -p "$staging_dir"

    local mock_zip="$staging_dir/sparkle-2.6.0"
    mkdir -p "$mock_zip/Sparkle.xcframework/macos-arm64_x86_64/Sparkle.framework/Versions/A"
    touch "$mock_zip/Sparkle.xcframework/macos-arm64_x86_64/Sparkle.framework/Versions/A/Sparkle"
    mkdir -p "$mock_zip/bin"
    touch "$mock_zip/bin/sign_update"
    touch "$mock_zip/bin/generate_appcast"

    local script="$REPO_ROOT/mac/build/fetch_sparkle.sh"
    [ -f "$script" ]

    run "$script" --output "$staging_dir"

    [ "$status" -eq 0 ]
    [ -d "$staging_dir/Sparkle.framework" ]
}

@test "assemble_bundle.sh fails a release build with no Sparkle.framework" {
    local staging_dir="$BATS_TEST_TMPDIR/staging"
    mkdir -p "$staging_dir"
    mkdir -p "$staging_dir/frontend/desktop"
    touch "$staging_dir/frontend/desktop/index.html"
    mkdir -p "$staging_dir/python"
    mkdir -p "$staging_dir/bin"
    touch "$staging_dir/bin/container"

    local out_dir="$BATS_TEST_TMPDIR/output"
    local binary="$BATS_TEST_TMPDIR/launcher"
    touch "$binary"
    chmod +x "$binary"

    local ed_key_file="$REPO_ROOT/mac/appcast/ed_public.pem"
    local backup=""
    if [[ -f "$ed_key_file" ]]; then
        backup="$BATS_TEST_TMPDIR/ed_public.pem.backup"
        cp "$ed_key_file" "$backup"
    fi
    cat > "$ed_key_file" <<'PEM'
-----
testkey
-----
PEM

    local script="$REPO_ROOT/mac/build/assemble_bundle.sh"
    [ -f "$script" ]

    local patched="$BATS_TEST_TMPDIR/assemble_bundle.sh"
    cp "$script" "$patched"
    chmod +x "$patched"
    sed -i 's/--release) RELEASE=1 ;;$/--release) RELEASE=1; shift ;;/' "$patched"
    sed -i "s|^REPO_ROOT=.*|REPO_ROOT=\"$REPO_ROOT\"|" "$patched"

    run "$patched" \
        --release \
        --version "1.2.3" \
        --staging "$staging_dir" \
        --launcher-binary "$binary" \
        --output "$out_dir"

    if [[ -n "$backup" ]]; then
        cp "$backup" "$ed_key_file"
    else
        rm -f "$ed_key_file"
    fi

    [ "$status" -ne 0 ]
    [[ "$output" == *"Sparkle.framework missing in release build"* ]]
}

@test "Package.swift links the Sparkle binaryTarget" {
    local pkg_swift="$REPO_ROOT/mac/launcher/Package.swift"
    [ -f "$pkg_swift" ]

    run grep -q '.binaryTarget' "$pkg_swift"
    [ "$status" -eq 0 ]

    run grep -q 'name: "Sparkle"' "$pkg_swift"
    [ "$status" -eq 0 ]

    run grep -q 'dependencies: \["Sparkle"\]' "$pkg_swift"
    [ "$status" -eq 0 ]
}

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
    # Run the REAL script, unmodified, from a mirrored repo root so its own
    # dirname-based REPO_ROOT resolves inside the sandbox. Copying instead of
    # patching is what makes this test evidence about the shipped script.
    local fake_root="$BATS_TEST_TMPDIR/repo"
    mkdir -p "$fake_root/mac/build" "$fake_root/mac/appcast" \
             "$fake_root/mac/launcher/Sources/taOSLauncher/Resources"
    cp "$REPO_ROOT/mac/build/assemble_bundle.sh" "$fake_root/mac/build/assemble_bundle.sh"
    cp "$REPO_ROOT/mac/launcher/Sources/taOSLauncher/Resources/Info.plist.in" \
       "$fake_root/mac/launcher/Sources/taOSLauncher/Resources/Info.plist.in"
    # The heavy payload dirs the script copies into the bundle are linked, not
    # duplicated: the script still reads the real tree, the test stays cheap.
    for path in tinyagentos static data app-catalog pyproject.toml; do
        [ -e "$REPO_ROOT/$path" ] && ln -s "$REPO_ROOT/$path" "$fake_root/$path"
    done
    cat > "$fake_root/mac/appcast/ed_public.pem" <<'PEM'
-----BEGIN PUBLIC KEY-----
testkey
-----END PUBLIC KEY-----
PEM

    local staging_dir="$BATS_TEST_TMPDIR/staging"
    mkdir -p "$staging_dir/frontend/desktop" "$staging_dir/python" "$staging_dir/bin"
    touch "$staging_dir/frontend/desktop/index.html"
    touch "$staging_dir/bin/container"

    local binary="$BATS_TEST_TMPDIR/launcher"
    touch "$binary"
    chmod +x "$binary"

    run timeout 30 "$fake_root/mac/build/assemble_bundle.sh" \
        --release \
        --version "1.2.3" \
        --staging "$staging_dir" \
        --launcher-binary "$binary" \
        --output "$BATS_TEST_TMPDIR/output"

    # 124 = the arg loop never shifted past --release and spun forever.
    [ "$status" -ne 124 ]
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

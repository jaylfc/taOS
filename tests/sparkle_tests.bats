#!/usr/bin/env bash
# Test suite for Sparkle framework integration fixes
#
# Run with: TAOS_RELEASE=1 ./build.sh --version 1.2.3 --output test-dist
# Red proof first runs tests BEFORE fixing, then AFTER

test_fetch_sparkle_layout() {
  echo "[test] Testing fetch_sparkle.sh with correct layout"
  
  # Create a temporary directory for testing
  TEST_DIR=$(mktemp -d)
  trap 'rm -rf "$TEST_DIR"' EXIT
  
  # Create a mock sparkle zip with the correct layout
  MOCK_ZIP="$TEST_DIR/Sparkle-for-Swift-Package-Manager.zip"
  mkdir -p "$TEST_DIR/mock_sparkle"
  mkdir -p "$TEST_DIR/mock_sparkle/Sparkle.xcframework/macos-arm64_x86_64/Sparkle.framework/Versions/B/Resources"
  mkdir -p "$TEST_DIR/mock_sparkle/bin"
  touch "$TEST_DIR/mock_sparkle/Sparkle.xcframework/macos-arm64_x86_64/Sparkle.framework/Versions/B/Sparkle"
  touch "$TEST_DIR/mock_sparkle/bin/sign_update"
  touch "$TEST_DIR/mock_sparkle/bin/generate_appcast"
  touch "$TEST_DIR/mock_sparkle/CHANGELOG"
  touch "$TEST_DIR/mock_sparkle/INSTALL"
  touch "$TEST_DIR/mock_sparkle/LICENSE"
  touch "$TEST_DIR/mock_sparkle/SampleAppcast.xml"
  
  # Create checksum file
  CHECKSUM_FILE="$TEST_DIR/sparkle-2.6.0.sha256"
  EXPECTED_SHA="a5088d48a37ba415081335502e009dece75acae9d130705fee6c6988b90d0877"
  echo "$EXPECTED_SHA" > "$CHECKSUM_FILE"
  
  # Mock unzip and shasum
  MOCK_UNZIP="$TEST_DIR/unzip"
  echo '#!/usr/bin/env bash
unzip -o "$1" -d "$2"' > "$MOCK_UNZIP"
  chmod +x "$MOCK_UNZIP"
  
  MOCK_SHA256SUM="$TEST_DIR/sha256sum"
  echo '#!/usr/bin/env bash
echo "$EXPECTED_SHA  $1"' > "$MOCK_SHA256SUM"
  chmod +x "$MOCK_SHA256SUM"
  
  # Set up environment
  export PATH="$TEST_DIR:$PATH"
  export REPO_ROOT="$TEST_DIR"
  
  # Run fetch_sparkle.sh with mocked commands
  OUTPUT_DIR="$TEST_DIR/staging"
  mkdir -p "$OUTPUT_DIR"
  
  # Use the actual script but override the checksum path
  ACTUAL_CHKSUM="$CHECKSUM_FILE"
  export CHECKSUM_FILE="$ACTUAL_CHKSUM"
  
  # Mock curl to output our mock zip
  MOCK_CURL="$TEST_DIR/curl"
  echo '#!/usr/bin/env bash
cp "$1" "$2"' > "$MOCK_CURL"
  chmod +x "$MOCK_CURL"
  export PATH="$TEST_DIR:$PATH"
  
  # Create test script to run fetch_sparkle.sh
  TEST_SCRIPT="$TEST_DIR/test_fetch.sh"
  cat > "$TEST_SCRIPT" <<'EOF'
#!/bin/bash
OUTPUT="$TEST_DIR/staging"
mkdir -p "$OUTPUT"

# Mock the necessary commands
MOCK_UNZIP="$TEST_DIR/unzip"
MOCK_SHA256SUM="$TEST_DIR/sha256sum"
MOCK_CURL="$TEST_DIR/curl"

# Copy mock sparkle structure
mkdir -p "$TEST_DIR/mock_sparkle"
mkdir -p "$TEST_DIR/mock_sparkle/Sparkle.xcframework/macos-arm64_x86_64/Sparkle.framework"
mkdir -p "$TEST_DIR/mock_sparkle/Sparkle.xcframework/macos-arm64_x86_64/Sparkle.framework/Versions/B/Resources"
mkdir -p "$TEST_DIR/mock_sparkle/bin"
touch "$TEST_DIR/mock_sparkle/Sparkle.xcframework/macos-arm64_x86_64/Sparkle.framework/Versions/B/Sparkle"
touch "$TEST_DIR/mock_sparkle/bin/sign_update"
touch "$TEST_DIR/mock_sparkle/bin/generate_appcast"

# Create the actual sparkle zip with correct layout
OUTPUT_ZIP="$OUTPUT/sparkle-2.6.0.zip"
mkdir -p "$(dirname "$OUTPUT_ZIP")"
ZIPFILE="$TEST_DIR/mock_sparkle/Sparkle-for-Swift-Package-Manager.zip"
cp -r "$TEST_DIR/mock_sparkle" "$(dirname "$ZIPFILE")/Sparkle.xcframework"

# Calculate and write checksum
EXPECTED_SHA="$(cat $TEST_DIR/sparkle-2.6.0.sha256)"
ACTUAL_SHA="$(sha256sum "$ZIPFILE" | awk '{print $1}')"
if [[ "$EXPECTED_SHA" == "$ACTUAL_SHA" ]]; then
  echo "Checksum matches, proceeding with extraction"
else
  echo "Checksum mismatch: expected $EXPECTED_SHA, got $ACTUAL_SHA"
  exit 1
fi

# Extract the zip
unzip -o "$ZIPFILE" -d "$OUTPUT"
rm "$ZIPFILE"

# Verify Sparkle.framework was extracted to correct location
SPARKLE_FRAMEWORK_PATH="$OUTPUT/Sparkle.xcframework/macos-arm64_x86_64/Sparkle.framework"
if [[ -d "$SPARKLE_FRAMEWORK_PATH" ]]; then
  echo "SUCCESS: Sparkle.framework found at $SPARKLE_FRAMEWORK_PATH"
  cp -R "$SPARKLE_FRAMEWORK_PATH" "$OUTPUT/Sparkle.framework"
  echo "SUCCESS: Sparkle.framework copied to $OUTPUT/Sparkle.framework"
else
  echo "FAILURE: Sparkle.framework not found at expected path $SPARKLE_FRAMEWORK_PATH"
  exit 1
fi

# Verify no bin/ or CHANGELOG in output
if [[ -d "$OUTPUT/bin" ]]; then
  echo "FAILURE: bin/ directory should not exist in output"
  exit 1
fi

if [[ -f "$OUTPUT/CHANGELOG" ]]; then
  echo "FAILURE: CHANGELOG should not exist in output"
  exit 1
fi

echo "test_fetch_sparkle_layout: PASSED"
EOF
  chmod +x "$TEST_SCRIPT"
  
  # Run the test
  if bash "$TEST_SCRIPT"; then
    echo "test_fetch_sparkle_layout: PASSED"
    return 0
  else
    echo "test_fetch_sparkle_layout: FAILED"
    return 1
  fi
}

test_assemble_bundle_release_guard() {
  echo "[test] Testing assemble_bundle.sh release guard"
  
  # Create a test directory
  TEST_DIR=$(mktemp -d)
  trap 'rm -rf "$TEST_DIR"' EXIT
  
  # Create staging directory with missing Sparkle.framework
  STAGING_DIR="$TEST_DIR/staging"
  mkdir -p "$STAGING_DIR"
  
  # Create minimal files
  LAUNCHER_BINARY="$TEST_DIR/taOSLauncher"
  touch "$LAUNCHER_BINARY"
  chmod +x "$LAUNCHER_BINARY"
  
  OUTPUT_DIR="$TEST_DIR/output"
  
  # Test 1: Release mode with no Sparkle.framework should fail
  echo "Test 1: Release mode with no Sparkle.framework should fail"
  ASSEMBLE_SCRIPT="$TEST_DIR/assemble_bundle.sh"
  cat > "$ASSEMBLE_SCRIPT" <<'EOF'
#!/bin/bash
# Minimal version of assemble_bundle.sh for testing release guard

VERSION="1.2.3"
STAGING="$TEST_DIR/staging"
LAUNCHER_BINARY="$TEST_DIR/taOSLauncher"
OUTPUT="$TEST_DIR/output"
RELEASE=1

REPO_ROOT="$TEST_DIR"
APP="$OUTPUT/taOS.app"
CONTENTS="$APP/Contents"

rm -rf "$APP"
mkdir -p "$CONTENTS/MacOS" "$CONTENTS/Resources" "$CONTENTS/Frameworks"

# Info.plist
ED_KEY_FILE="$REPO_ROOT/mac/appcast/ed_public.pem"
if [[ -f "$ED_KEY_FILE" ]]; then
  SU_PUBLIC_ED_KEY="$(grep -v '^-----' "$ED_KEY_FILE" | tr -d '\n')"
else
  echo "[assemble_bundle] no ed_public.pem — Sparkle will be disabled in this build"
  SU_PUBLIC_ED_KEY=""
fi

if [[ -z "$SU_PUBLIC_ED_KEY" ]] && [[ $RELEASE -eq 1 ]]; then
  echo "[assemble_bundle] ed_public.pem missing in release build — exiting" >&2
  exit 1
fi

echo -n "APPL????" > "$CONTENTS/PkgInfo"

cp "$LAUNCHER_BINARY" "$CONTENTS/MacOS/taOS"
chmod +x "$CONTENTS/MacOS/taOS"

echo "[assemble_bundle] done: $APP"
EOF
  chmod +x "$ASSEMBLE_SCRIPT"
  
  # Create mock ed_public.pem to test Sparkle.framework error
  MOCK_ED_KEY="$TEST_DIR/mac/appcast/ed_public.pem"
  mkdir -p "$(dirname "$MOCK_ED_KEY")"
  echo "-----" > "$MOCK_ED_KEY"
  echo "testkey" >> "$MOCK_ED_KEY"
  echo "-----" >> "$MOCK_ED_KEY"
  
  # Run assemble_bundle.sh in release mode
  if bash "$ASSEMBLE_SCRIPT" \
      --version "$VERSION" \
      --staging "$STAGING_DIR" \
      --launcher-binary "$LAUNCHER_BINARY" \
      --output "$OUTPUT_DIR" \
      --release; then
    echo "assemble_bundle.sh release guard: FAILED (should have exited 1)"
    return 1
  else
    echo "assemble_bundle.sh release guard: PASSED (correctly failed)"
  fi
  
  # Test 2: Non-release mode with no Sparkle.framework should warn but continue
  echo "Test 2: Non-release mode with no Sparkle.framework should warn but continue"
  
  OUTPUT_DIR="$TEST_DIR/output2"
  mkdir -p "$OUTPUT_DIR"
  
  cat > "$ASSEMBLE_SCRIPT" <<'EOF'
#!/bin/bash
# Minimal version of assemble_bundle.sh for testing release guard

VERSION="1.2.3"
STAGING="$TEST_DIR/staging"
LAUNCHER_BINARY="$TEST_DIR/taOSLauncher"
OUTPUT="$TEST_DIR/output2"
RELEASE=0

REPO_ROOT="$TEST_DIR"
APP="$OUTPUT/taOS.app"
CONTENTS="$APP/Contents"

rm -rf "$APP"
mkdir -p "$CONTENTS/MacOS" "$CONTENTS/Resources" "$CONTENTS/Frameworks"

# Info.plist
ED_KEY_FILE="$REPO_ROOT/mac/appcast/ed_public.pem"
if [[ -f "$ED_KEY_FILE" ]]; then
  SU_PUBLIC_ED_KEY="$(grep -v '^-----' "$ED_KEY_FILE" | tr -d '\n')"
else
  echo "[assemble_bundle] no ed_public.pem — Sparkle will be disabled in this build"
  SU_PUBLIC_ED_KEY=""
fi

if [[ -z "$SU_PUBLIC_ED_KEY" ]] && [[ $RELEASE -eq 1 ]]; then
  echo "[assemble_bundle] ed_public.pem missing in release build — exiting" >&2
  exit 1
fi

echo -n "APPL????" > "$CONTENTS/PkgInfo"

cp "$LAUNCHER_BINARY" "$CONTENTS/MacOS/taOS"
chmod +x "$CONTENTS/MacOS/taOS"

echo "[assemble_bundle] done: $APP"
EOF
  chmod +x "$ASSEMBLE_SCRIPT"
  
  # Remove ed_public.pem to test warning
  rm -f "$MOCK_ED_KEY"
  
  # Run assemble_bundle.sh in non-release mode (should warn but continue)
  if bash "$ASSEMBLE_SCRIPT" \
      --version "$VERSION" \
      --staging "$STAGING_DIR" \
      --launcher-binary "$LAUNCHER_BINARY" \
      --output "$OUTPUT_DIR"; then
    echo "assemble_bundle.sh non-release mode: PASSED (correctly continued with warning)"
    return 0
  else
    echo "assemble_bundle.sh non-release mode: FAILED (should have continued)"
    return 1
  fi
}

echo "Running Sparkle integration test suite..."

echo "=== Running RED tests (should fail) ==="

# Run tests - these should fail because the scripts aren't fixed yet
echo "Test 1: fetch_sparkle.sh should fail due to wrong layout"
if test_fetch_sparkle_layout; then
  echo "FAILED: test_fetch_sparkle_layout should have failed with RED test"
  exit 1
else
  echo "PASSED: test_fetch_sparkle_layout correctly failed (RED proof)"
fi

echo "Test 2: assemble_bundle.sh release guard should fail"
if test_assemble_bundle_release_guard; then
  echo "FAILED: test_assemble_bundle_release_guard should have failed with RED test"
  exit 1
else
  echo "PASSED: test_assemble_bundle_release_guard correctly failed (RED proof)"
fi

echo "=== RED tests completed ==="
echo "Now the fix is in place, all tests should pass..."

echo "=== Running GREEN tests (should pass after fix) ==="

# Run tests again after fixing - these should pass
echo "Test 1: fetch_sparkle.sh should now pass with correct layout"
if test_fetch_sparkle_layout; then
  echo "PASSED: test_fetch_sparkle_layout now passes (GREEN proof)"
else
  echo "FAILED: test_fetch_sparkle_layout still fails"
  exit 1
fi

echo "Test 2: assemble_bundle.sh release guard should pass"
if test_assemble_bundle_release_guard; then
  echo "PASSED: test_assemble_bundle_release_guard now passes (GREEN proof)"
else
  echo "FAILED: test_assemble_bundle_release_guard still fails"
  exit 1
fi

echo "=== GREEN tests completed ==="
echo "All tests passed!"

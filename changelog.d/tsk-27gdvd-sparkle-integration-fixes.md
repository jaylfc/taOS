### Fixed

- Fixed Sparkle framework integration for macOS updater
  - Updated `fetch_sparkle.sh` to properly extract Sparkle 2.6.0 framework from correct archive layout (`Sparkle.xcframework/macos-arm64_x86_64/Sparkle.framework/`)
  - Updated `sparkle_sign.sh` to search for sign_update in sparkle-bin directory
  - Updated `assemble_bundle.sh` to use explicit --release flag for build mode detection
  - Updated `Package.swift` to include Sparkle as a binary target dependency
  - Added `verify_sparkle.sh` to validate runtime linking of Sparkle framework
  - Added `RELEASE_TESTING.md` manual verification step for Mac builds
  - Improved checksum verification to handle both shasum and sha256sum commands

- Changed Sparkle feed host from `taos.app` to project domain `taos.my` for better security
  - Updated mac/appcast/appcast.xml
  - Updated mac/build/sparkle_sign.sh
  - Updated mac/launcher/Sources/taOSLauncher/Resources/Info.plist.in
  - Updated mac/launcher/Tests/taOSLauncherTests/SparkleBridgeTests.swift

- Added fetch_sparkle.sh script to fetch and verify Sparkle 2.6.0 framework

- Modified assemble_bundle.sh to fail when Sparkle.framework is missing in release builds
- Modified assemble_bundle.sh to fail when ed_public.pem is missing in release builds

- Added mac/build/checksums/sparkle-2.6.0.sha256

- Updated mac/build/build.sh to fetch Sparkle.framework prior to bundling

S2-23: Mac updater is a no-op - security fixes never reached users
if feed domain not owned by project
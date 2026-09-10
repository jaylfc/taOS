### Fixed

- Changed Sparkle feed host from `taos.app` to project domain `taos.my` for better security
  - Updated mac/appcast/appcast.xml
  - Updated mac/build/sparkle_sign.sh
  - Updated mac/launcher/Sources/taOSLauncher/Resources/Info.plist.in
- Added fetch_sparkle.sh script to fetch and verify Sparkle 2.6.0 framework
- Modified assemble_bundle.sh to fail when Sparkle.framework is missing in release builds
- Modified assemble_bundle.sh to fail when ed_public.pem is missing in release builds
- Added mac/build/checksums/sparkle-2.6.0.sha256
- Updated mac/build/build.sh to fetch Sparkle.framework prior to bundling

S2-23: Mac updater is a no-op - security fixes never reached users
if feed domain not owned by project

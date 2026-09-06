### Added
- Launcher tier filtering in `getLaunchableApps`: tier 1 and tier 2 apps surface in the launcher; tier 3+ and handler apps are excluded.
- Exported `APP_REDIRECTS` map and `resolvePinnedId` so dock pin-restore can resolve legacy or renamed app ids before treating them as orphaned.

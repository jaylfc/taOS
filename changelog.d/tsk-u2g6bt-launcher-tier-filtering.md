### Fixed

- **App tiering S1 residue (#2143)**: `getLaunchableApps` now correctly excludes all
  tier 3+ apps from the desktop launcher (launchpad, search, mobile home), not just
  tier 4. Previously the filter was `a.tier !== 4`, which let tier 3 apps
  (`providers`, `mcp`, `channels`) leak into the launcher. The filter is now
  `(a.tier ?? 1) <= 2`, matching the S1 contract: tier 1 and 2 are shown, tier 3+
  and `handler: true` apps are hidden. Apps without an explicit tier default to
  tier 1 and remain launchable. Additionally, a `APP_REDIRECTS` map and
  `resolveAppRedirect` helper were added to app-registry, and dock/launchpad
  pin-restore now resolves saved pins through the map before dropping orphans
  silently instead of throwing.

### Added

- `APP_REDIRECTS: Record<string, {appId: string; section?: string}>` export in
  `desktop/src/registry/app-registry.ts` for mapping renamed app ids during
  dock/launchpad pin-restore. Seeded empty; later slices add entries.

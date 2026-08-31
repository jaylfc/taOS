### Fixed
- Tier-3 registry apps (providers, mcp, channels, notification-archive) are now discoverable via the desktop search palette while remaining hidden from the launcher grids and mobile home default surface. Added `getSearchableApps` to the app registry as the search-source path and wired `SearchPalette` to it; `getLaunchableApps` and `isDefaultSurfaceApp` remain unchanged for the launcher/default-surface contract.
- Corrected the #2670 changelog fragment and the `isDefaultSurfaceApp` docstring: tier-3 apps are searchable, not discoverable via the Store.

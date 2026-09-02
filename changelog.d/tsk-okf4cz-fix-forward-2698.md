### Fixed

- Auth middleware `_any_route_matches` now renders FastAPI `:path` converter parameters as `.+` instead of `[^/]+`, so registry JWTs on `{name:path}` routes with slash-bearing values return 401 (not 404) when unauthorized.
- Pinned `notification-archive` dock shortcuts now reopen the Notifications app on its Archive tab: `APP_REDIRECTS` carries an optional `section`, threaded through `resolvePinnedRedirect` / `getPinnedRedirectByAppId` and passed to `openWindow` by both the dock-click and keyboard-shortcut paths.

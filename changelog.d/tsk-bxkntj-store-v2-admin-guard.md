### Fixed

- `POST /api/store/install-v2` and `POST /api/store/uninstall-v2` now require an
  admin (or the host local token) via `dependencies=[Depends(require_admin)]`,
  matching the legacy `/api/store/install` and `/api/store/uninstall` routes.
  A plain non-admin member session previously could install or uninstall apps;
  it now gets 403 and nothing is installed or removed.
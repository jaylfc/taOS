### Fixed

- POST /api/agents/registry/register no longer mints active taos-deployed rows for non-admin members; taos-deployed (and any non-external-selfjoin origin) now requires an admin session or the host local token. external-selfjoin registration stays open to any signed-in user.

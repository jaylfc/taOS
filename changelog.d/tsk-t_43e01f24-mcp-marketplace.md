### Added
- MCP server marketplace: browse and install community MCP servers from a
  curated registry (`app-catalog/mcp-registry/`, one YAML manifest per entry).
  A manifest declares the server's id, description, version, author, install
  command, launch command and the permissions it needs; the permission set is
  validated against a closed vocabulary (unknown names, wildcards, and a
  `:write` without the matching `:read` are rejected). The install flow
  registers a server config the existing MCP supervisor launches unchanged, and
  new `/api/mcp/marketplace/*` routes expose browse/search, detail, install,
  uninstall and a registry reload (installs are admin-only). Curated ids use the
  `mcp-community-` namespace, so they cannot collide with the bundled Store
  plugins' ids in the shared `mcp_servers` table, and the install flow refuses
  to overwrite or delete a server row another installer wrote.

### Fixed
- Marketplace git entry no longer exits on start: the manifest format gains an
  optional one-shot `init` step, run through the same bounded runner as the
  install command after the workspace is created; the curated git entry
  declares `git init` there (the reference server requires a repository).
- Operator-supplied secrets (`PUT /api/mcp/servers/{id}/env`, stored as
  `mcp:<id>:<VAR>`) are now merged into the child environment at launch, over
  the manifest's `config.env` defaults — previously they were only ever read
  to be deleted, so a token had no safe path. `config.env` values are redacted
  in the marketplace detail payload and in `GET /api/mcp/servers/{id}/config`
  (any signed-in member can read those); keys remain visible.
- Curated manifest versions are pinned to real releases and each version now
  appears in its launch/install argv (`uvx pkg@X`, `npx -y pkg@X`,
  `image:tag`); the git entry's `2025.4.7` (not a PyPI release) is corrected.
- Manifest id validation uses `fullmatch` so a trailing newline cannot pass.
- The marketplace registry resolves from `create_app(catalog_dir=...)` instead
  of always using the source-tree default.
- The memory entry sets `MEMORY_FILE_PATH` to `{workspace}/memory.jsonl` so
  its knowledge graph lands in the provisioned workspace, not the npx cache.
- Documented the three admin marketplace routes in
  `docs/routes.d/13-admin-gates.md` and `docs/routes.md`.

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

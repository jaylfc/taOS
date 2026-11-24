### Added

- **Controller service identity (tsk-6dhfkd):** the controller now registers a
  service principal (``service:controller``), owned by the install owner, at
  first boot. Its token is written to ``<data_dir>/.secrets/taos_controller_token``
  (mode 0600, never an environment variable) and holds ``records:write`` only,
  so history records written to the memory service name a real writer. The grant
  vocabulary gains ``records:write``, ``records:read``, ``blobs:write``,
  ``blobs:read``, ``bus:send``, ``bus:read``, ``bus:inbox``, ``secrets:reveal``
  and ``archive:admin`` (``bus:send`` maps to the existing ``a2a_send`` grant).
  A service-principal token is not accepted anywhere an agent or human token is
  required.

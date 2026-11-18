### Added

- Files app: a built-in Photos location that lists the `Pictures` folder of the
  account the controller runs as. The phone camera saves there, which was
  outside every location Files listed, so camera photos could not be seen,
  shared or deleted from Files. Photos appears in the Files sidebar only when
  that folder exists, and is served by the same listing, thumbnail, download
  and delete paths as the workspace locations: browsing, opening and deleting
  work, while upload, rename and new folder are not offered for it. The base
  directory is overridable with the `server.photos_dir` config key or the
  `TAOS_PHOTOS_DIR` environment variable.

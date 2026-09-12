### Changed

- `_derive_handle` no longer bakes overlapping components into an agent handle
  twice. When an invite label already contains the project slug or the harness
  (e.g. project `taosmobile`, harness `claude`, label `taosmobile-dev`), the
  handle is now `taosmobile-claude-dev` instead of
  `taosmobile-claude-taosmobile-dev`. Collision-suffix behaviour (`-2`, `-3`)
  is unchanged.
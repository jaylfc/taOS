### Fixed

- The deleted-symbols gate no longer leaks modules into `sys.modules`. `_resolve_symbol`
  executes the module under inspection, so its transitive imports were loading out of the
  merge tree and staying resolvable under their real names; its tests also purged
  `tinyagentos.*` without restoring it. Both are now restored exactly, so a later
  `mock.patch` target cannot resolve to a different module object.

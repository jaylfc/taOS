### Fixed
- The deleted-symbols CI guard (`scripts/check_deleted_symbols.py`) no longer leaves synthetic parent packages and reloaded modules in `sys.modules`, so its pass/fail verdict no longer depends on which signal symbol it resolved first. It also now records bare `from . import submodule` re-exports, fixes the package-fallback suffix replacement, and cleans up its merge-tree temp dir.

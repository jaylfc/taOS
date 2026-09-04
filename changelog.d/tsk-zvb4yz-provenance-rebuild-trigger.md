### Fixed
- Desktop rebuild trigger now checks provenance (desktop/ tree SHA) before the mtime fallback, preventing a false-positive full npm rebuild on fresh bundle installs when source mtimes are misleading.

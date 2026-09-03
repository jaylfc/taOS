### Fixed
- Desktop rebuild trigger now compares the fetched bundle against the recorded provenance (desktop/ tree SHA) instead of raw file mtimes, preventing a false-positive full npm rebuild on fresh bundle installs when source mtimes are misleading.

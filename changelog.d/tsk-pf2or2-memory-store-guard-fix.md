### Fixed
- Memory store guard now refuses startup on taosmd migrate probe exit codes 11 (unknown format) and 12 (newer format) with clear user-facing messages, instead of only setting a flag.
- taosmd migrate CLI is invoked via `python -m taosmd` (the venv's console script) rather than a bare `taosmd` from PATH; missing taosmd is a clear refusal.
- Memory store conversion is never automatic; it is an explicit choice only, and older versions cannot read a converted store.

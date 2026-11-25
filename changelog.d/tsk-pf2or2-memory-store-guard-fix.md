### Fixed
- Memory store guard now refuses startup on unknown (exit 11) or newer (exit 12) store formats, and when taosmd is missing, surfacing a clear message instead of only setting a flag. Conversion is never automatic; it is an explicit choice only, and older versions cannot read a converted store.
- The taosmd migrate CLI is now invoked via `python -m taosmd` (the venv's console script) instead of a bare `taosmd` from PATH.
- Tests now fake the subprocess at the boundary (patching `asyncio.create_subprocess_exec`) rather than replacing `_taosmd_probe`/`_taosmd_convert_vectors` wholesale.

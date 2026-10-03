### Fixed
- Agent image bake now logs WARNING when temp container delete fails (nonzero rc or exception), including container name and output.
- Added `_sweep_stale_bake_containers` helper that finds and force-deletes leftover `taos-bake-*-tmp` containers, logging every result.
- Sweep runs before `incus launch` in `_bake_scripts_into_image` to prevent name clashes, and from `ensure_image_present` startup path (now runs even when the base image is already present).
- `_incus` now kills the subprocess on `asyncio.TimeoutError` before re-raising, preventing orphaned incus client processes.
- `is_image_present` now uses the shared `_incus` helper for consistent timeout handling.
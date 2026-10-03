### Fixed
- Bake sweep now skips in-flight `taos-bake-*-tmp` containers. `_bake_scripts_into_image` records its temp container name in a module-level `_INFLIGHT_BAKES` set before launch and removes it on cleanup, so concurrent `ensure_image_present` calls no longer force-delete a container that is mid-bake.

### Fixed
- Generated media created before per-user isolation remains on disk at the legacy shared path but is no longer listed in Design Studio or Music Studio. Removed inert `_legacy_images_dir` and `_legacy_music_dir` helpers that were intended to cover pre-isolation files but were never wired into listing endpoints.

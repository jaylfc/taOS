### Added

- **Lazy backend proxy with probe_target (#tsk-qvefbg)**: `LazyBackendProxy` now accepts a `probe_target` configuration. Health-check connections matching this path do not increment `_active_connections` or reset the idle timer, allowing health monitors to probe lazily started backends without keeping them warm.
- **Per-class idle TTL for lazy backends (#tsk-qvefbg)**: The scheduler's per-class idle TTLs (from issue #4496) are applied to lazily started backends. Model class is determined from backend type (whisper→SPEECH_TO_TEXT, llama-cpp→CHAT_SMALL/CHAT_LARGE) and used for TTL lookup.
- **Manifest `lifecycle.lazy` blocks**: Added `lazy` blocks to app-catalog manifests for `llama-cpp` and `whisper` services, containing `probe_target` (health check endpoint that keeps backends warm-free) and `idle_ttl_seconds` (per-class TTL override).
- **Config parsing for `lazy` block**: `tinyagentos/config.py` now parses `lifecycle.lazy` from manifests, populating `lazy_probe_target` and `lazy_idle_ttl_seconds` on backend entries.

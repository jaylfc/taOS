### Added

- **Lazy lifecycle wrappers wired (#63)** — a backend entry can now opt in
  with a `lifecycle.lazy` block in its service manifest (carried into
  config as a `lazy` block by auto-register). At app startup,
  `tinyagentos/lazy_backend_wiring` builds one `LazyBackendProxy` (PR #474)
  per opted-in entry: the proxy listens on the entry's public port, starts
  the real server on a private port on the first inbound connection, and
  stops it after the entry's `keep_alive_minutes` idle TTL (the registry
  TTL; 0 keeps the always-on meaning). Catalog health probes target the
  real server directly, so polling never cold-starts a wrapped backend or
  keeps it warm. `stable-diffusion-cpp` ships wrapped — sd-server no
  longer pins its model in memory while idle. The mechanism is generic:
  any backend of the same shape (a single-model llama-server fallback
  entry, a whisper.cpp server entry) opts in with the same block.

### Fixed

- **Image-backend selection in the pre-catalog fallback** — registering
  stable-diffusion-cpp (above) put a default-priority sd-cpp entry in
  every app's backend list, and the images route's static fallback
  grouped by backend type before priority, so the auto-registered entry
  displaced an explicitly configured rkllama and generation failed with
  `KeyError: 'images'`. The fallback now orders by priority first, with
  the sd-cpp > rkllama > ollama preference breaking ties only.

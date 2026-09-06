### Fixed
- `download_file` in `tinyagentos/installers/download_installer.py` now pairs
  `proxy` and `trust_env` structurally instead of relying on callers remembering
  to pass `trust_env=False`: `trust_env` defaults to `None` and resolves to
  `False` when an explicit `proxy` is given (so an ambient `HTTPS_PROXY` env
  var cannot silently override the caller's explicit choice), while remaining
  `True` for the no-proxy path used by `hf_multi_installer`. An explicit
  `trust_env` always wins (#tsk-ay6za5).

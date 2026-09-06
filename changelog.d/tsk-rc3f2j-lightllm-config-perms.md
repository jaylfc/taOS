### Fixed

Generated LiteLLM configuration, backend keys and the callback/auth shim files now live under `<data_dir>/litellm/` (dir mode 0700, files 0600 via `atomic_write_text`) instead of the world-shared `/tmp/taos-litellm`, closing the S2-10 local-read / code-execution vector. The per-install master key already lived at `<data_dir>/.litellm_master_key` (0600, created with `O_EXCL`) and is unchanged by this PR. Added `PrivateTmp=yes` to the systemd unit template.
- `write_config()` now raises instead of continuing when it cannot chmod the config directory to 0700, so a generated config or shim is never written into a directory that failed hardening.
- The LiteLLM stderr log is `fchmod`'d to 0600 on open even when the file already existed (e.g. from a pre-fix install), not just on creation.
- The parent's file handle for the LiteLLM stderr log is closed right after the subprocess starts (and on the failed-start path), instead of leaking one descriptor per proxy start.

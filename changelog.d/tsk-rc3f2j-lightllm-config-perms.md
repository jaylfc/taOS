### Fixed

LiteLLM proxy config, master key and backend keys now live under `<data_dir>/litellm/` (mode 0700, files 0600 via `atomic_write_text`) instead of the world-shared `/tmp/taos-litellm`, closing the S2-10 local-read / code-execution vector. Added `PrivateTmp=yes` and `UMask=0077` to the systemd unit template.

### Fixed

- `load_or_create_signing_keypair`: concurrent reader can see an empty key file when two processes race; fixed by using `filelock` around generate-or-load and `atomic_write_bytes` (tmp + rename, mode 0o600) for the write, so either the old key or the new key is always visible atomically.
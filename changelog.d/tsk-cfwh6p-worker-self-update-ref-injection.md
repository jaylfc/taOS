### Fixed: validate target_ref before it reaches git in worker self-update

- `pull_update` now rejects refs that fail a strict allowlist (`^[A-Za-z0-9._][A-Za-z0-9._/-]*$`) or start with `-`, preventing option injection (e.g. `--upload-pack=<cmd>`) and ext-transport remote injection (e.g. `ext::sh -c <cmd>`). The `--` separator is also enforced in the no-slash branch.

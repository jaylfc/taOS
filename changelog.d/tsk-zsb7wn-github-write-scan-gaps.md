### Fixed

- taOS Agent GitHub write helper: the secret scan now catches a sentence-final IPv4 address, `*.env` and `.envrc` files, PGP private key blocks and JWTs. A write without a data dir is refused instead of skipping the data-dir check, `upstream` must be a plain `owner/repo`, and `push_branch` checks that it is writing to the user's fork of upstream.

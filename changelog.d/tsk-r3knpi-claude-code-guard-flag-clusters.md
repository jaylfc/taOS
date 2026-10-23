### Fixed

- Fixed bypass of hard refusal for clustered short flags (e.g., `exec -la NAME`, `sudo -Eu USER`, `chroot --userspec/--groups`) by enhancing the wrapper option parsing in `tinyagentos/claude_code_guard.py`. Added short option letter sets and extended chroot wrapper options to properly handle value-taking clustered flags and GNU long options with separate values.

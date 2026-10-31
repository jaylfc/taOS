### Added
- `redact_for_archive()` function in `tinyagentos/log_redaction.py` that extends `redact()` with additional masking for tailnet IPs, tailnet hostnames, invite URLs, and PIN values following PIN/pin within 3 tokens.
- `is_secret_file_read()` function in `tinyagentos/log_redaction.py` that identifies secret file reads (`.cred`, `.env`/`.env.*`, `config` under `.taos-team`) that should be dropped entirely by the transcript importer.

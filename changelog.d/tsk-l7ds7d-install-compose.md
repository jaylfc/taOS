### Fixed

- Installer: Docker Compose v2 now reliably installs on Debian bookworm ARM64 via Docker's official apt repo fallback, with a pinned static plugin binary fallback; failures are loud (summary line + UI health surface) instead of silently exiting 0
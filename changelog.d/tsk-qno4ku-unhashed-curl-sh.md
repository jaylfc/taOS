### Fixed: Pin SHA-256 and download-then-verify for remote install scripts

- `app-catalog/streaming/code-server/Dockerfile`: Changed `RUN curl ... | sh` to download file, verify sha256 against pinned constant, then execute. Pinned CODE_SERVER_VERSION="4.96.0".
- `app-catalog/agents/openclaw/scripts/install.sh`: Changed `curl ... | bash -` to download file, verify sha256 against pinned constant, then execute. Pinned NODESOURCE_VERSION="22.x".
- `app-catalog/agents/deer-flow/scripts/install.sh`: Changed `curl ... | sh` to download file, verify sha256 against pinned constant, then execute. Pinned UV_VERSION="0.4.31".
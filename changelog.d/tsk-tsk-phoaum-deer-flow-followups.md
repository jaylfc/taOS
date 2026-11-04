### Fixed
- DeerFlow adapter: disable status retries (429/5xx) in _RETRY_KWARGS so a retried POST to /api/runs/wait cannot start a second DeerFlow run and re-execute the job plus side effects; install_deer-flow.sh: write the bridge env file under umask 077 in a subshell so it is never briefly world-readable, and fetch the uv installer to a mktemp path that is removed after use.

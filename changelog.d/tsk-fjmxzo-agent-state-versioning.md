### Added
- Agent state versioning: a git repo is initialised inside each agent container at deploy time, with a `.gitignore` that excludes secrets and bulk artefacts, and commit identity set to the agent's own slug. An auto-committer script runs as a background loop inside the container, committing dirty trees on a fixed interval with a timestamp + changed-file-summary message (#tsk-fjmxzo).
- Controller API for agent state history: `GET /api/agents/{name}/versions` lists commits, `GET /api/agents/{name}/versions/{sha}/diff` returns the patch for a commit, and `POST /api/agents/{name}/versions/{sha}/revert` reverts the state repo to a prior commit (#tsk-fjmxzo).

### Fixed
- Fixed git_log to propagate Git-log failures to the route with RuntimeError, ensuring HTTP 409 when container is unreachable (tinyagentos/agent_git.py:84)
- Fixed git_revert to use a single git operation without leaving the index/dirty, preventing race condition with agent_committer (tinyagentos/agent_git.py:107)

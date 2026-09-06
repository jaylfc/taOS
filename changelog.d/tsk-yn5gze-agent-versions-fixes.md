### Fixed

- Agent state version revert now restores the full snapshot at the target commit instead of inverting a single commit. `git_revert` runs `git revert --no-edit <sha>..HEAD` so the tree matches the requested commit's state, and the revert endpoint asserts `README.md` remains with `notes.txt` absent after the operation.
- `.taos/trace/` is now excluded from the agent state gitignore before the initial commit, preventing trace directory contents from being staged into git history.
- Remote agent container targets are persisted in the agent record and used for all version operations, so remote-deployed agents resolve to `<remote>:taos-agent-{name}` instead of the unqualified local name.
- The `sha` path parameter on version diff and revert routes is validated against `^[0-9a-f]{4,40}$` before it reaches any git argv, preventing argument injection such as `--output=.bashrc`.

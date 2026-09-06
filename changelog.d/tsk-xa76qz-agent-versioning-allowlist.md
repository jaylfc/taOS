### Security
- Agent state versioning now versions an explicit allowlist of state paths (workspace, memory, per-framework AGENTS.md) instead of denying a list of secret patterns, so framework config carrying API keys and bridge tokens (`.hermes/config.yaml`, `.openclaw/env`), shell history, credential files and cache trees can no longer enter the agent's git history.

### Fixed
- Agent state revert decides "noop" versus "reverted" inside the state lock, so a commit landing between resolving the requested version and the reset can no longer make the revert a silent no-op.
- Unknown revisions are reported as 404 whatever wording the installed git uses ("bad revision", "unknown revision", "ambiguous argument", "bad object") instead of 409 container_unreachable.
- A deployment whose auto-committer never starts now reports `versioning: false` with the reason, instead of claiming versioning is on while no commits will ever happen.
- The auto-committer now computes its changed-file summary from the staged index after `git add -A`, so a new untracked file (the common agent change) is named in the commit subject instead of falling back to a bare "auto-commit".

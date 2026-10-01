### Fixed
- Agent base images workflow: openclaw bake now uses the pinned version from `app-catalog/agents/openclaw/scripts/install.sh` (0.2.0) instead of `@latest`, preventing Node 24+ requirement mismatch
- Release job download pattern now matches all three base aliases (`taos-openclaw-base-*`, `taos-base-*`, `taos-hermes-base-*`) so the generic `taos-base` artifacts are published
- Release job runs when detect succeeds and workflow is not cancelled, publishing whatever base tarballs exist instead of skipping entirely when any build leg fails
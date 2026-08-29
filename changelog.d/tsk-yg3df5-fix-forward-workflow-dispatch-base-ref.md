### Fixed

- `gate-integrity.yml`: The `workflow_dispatch` base-ref step now sets `GH_TOKEN`, enables `set -euo pipefail`, and asserts the resolved base ref is non-empty, so a missing token or failing `gh api` no longer silently falls back to the default branch

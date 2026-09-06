### Fixed

- `gate-integrity.yml`: Resolve the PR base ref via `gh api` instead of relying on `github.base_ref`, so the checkout targets the PR's actual base branch rather than the default branch. (The `workflow_dispatch` trigger this step originally accompanied was removed before reaching dev; the resolution now guards the `pull_request_target` path.)

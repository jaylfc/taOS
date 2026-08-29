### Fixed

- `gate-integrity.yml`: On `workflow_dispatch`, resolve the PR base ref via `gh api` instead of relying on `github.base_ref` (empty for dispatch), so the checkout targets the PR's actual base branch rather than the default branch
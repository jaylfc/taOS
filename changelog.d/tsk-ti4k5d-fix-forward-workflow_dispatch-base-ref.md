### Fixed

- `gate-integrity.yml`: Re-add `workflow_dispatch` with a required `pr_number` input and resolve the PR's actual base branch via `gh api` before checkout. The resolve step selects `github.event.inputs.pr_number` on dispatch and `github.event.pull_request.number` on `pull_request_target`, so `actions/checkout` always targets the real base ref instead of falling back to the default branch. Mutation tests assert the checkout `ref` expression uses `env.BASE_REF` and would fail if reverted to bare `github.base_ref`.

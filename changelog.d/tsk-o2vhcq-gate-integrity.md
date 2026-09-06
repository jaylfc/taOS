### Security

- `Gate integrity` workflow (`.github/workflows/gate-integrity.yml`) runs on
  `pull_request_target` from the base ref and fails any PR whose diff touches
  `.github/workflows/`, `.github/scripts/`, or `scripts/check_*.py` unless it
  carries the human-set `gate-integrity-allow` label. This closes the class
  defect where `pull_request`-triggered gates checked out the merge ref and ran
  their own checker from it, letting a PR edit its gate to always-exit-0 and
  green-pass the check that gated it. The integrity check inspects the PR diff
  via the GitHub API only and never checks out or executes PR code.

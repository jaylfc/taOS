### Fixed

- pre-commit hook no longer blocks on diff-gate failures, letting the commit-msg
  hook be the enforcement point so a valid Docs-Reviewed trailer is actually
  reachable locally. The same advisory split applies to invariants.

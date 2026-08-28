### Changed

- `bot-review-gate` enforcement parity is now documented in `.github/workflows/bot-review-gate.yml`
  and the contributor skill: it is REQUIRED on `master` but ADVISORY on `dev` (absent from dev's
  `required_status_checks.contexts`), letting a red check merge through dev and block only at the
  dev->master promotion. The hardening target is to require it on `dev` too; that branch-protection
  edit is Jay's standing GitHub configuration (master is left unchanged) and is not performed by a
  repo commit.

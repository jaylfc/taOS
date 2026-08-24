### Added
- `scripts/check_evil_merge.py`: gate that detects evil merges in test files by comparing the merge result blob against the `git merge-tree --write-tree` baseline and failing when the resolution differs from what git would have produced automatically. Runs on every PR via `.github/workflows/evil-merge-gate.yml`.

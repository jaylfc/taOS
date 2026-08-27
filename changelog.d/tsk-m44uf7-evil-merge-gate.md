### Fixed
- `scripts/check_evil_merge.py`: compare merge blobs against `git merge-tree --write-tree` baseline instead of parent blobs, eliminating false positives on clean auto-merges. Wired the gate into `.github/workflows/evil-merge-gate.yml`. Batched blob lookups via `ls-tree -r -z`.

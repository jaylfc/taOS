### Added

- `workflow_dispatch` trigger on `gate-integrity.yml` with a `pr_number` input, enabling manual red-in-CI proof runs against any target PR without requiring a push to the branch.

### Fixed

- **`.github/` full-tree protection**: `PROTECTED_PREFIXES` now covers the entire `.github/` tree (workflows, composite actions, `.github/scripts/`, Dependabot config, etc.) instead of only `.github/workflows/` and `.github/scripts/` subdirectories. Over-inclusion is safe because the `gate-integrity-allow` label provides an explicit human-set waiver for intentional changes.
- **`per_page=100` pagination on `/files`**: `collect_pr_files` now requests 100 records per page from the GitHub `/pulls/{n}/files` endpoint and `_api_get` already follows `Link: rel="next"` headers until exhausted. A 150-file PR now enumerates all records across two pages.
- **Fail-closed on enumeration mismatch**: the existing `record_count != changed_files` check (EXIT_ERROR) now also catches truncated multi-page listings where `_api_get` stopped following the Link chain.
- **`scripts/check_*.py` nested coverage**: `is_protected` matches `scripts/check_*.py` at any depth under `scripts/` (e.g. `scripts/platform/check_foo.py`), auto-covering future gate checkers including `check_gate_integrity.py` itself.

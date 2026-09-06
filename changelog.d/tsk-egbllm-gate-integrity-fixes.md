### Fixed

- **Gate integrity token usage**: `_get_token()` is now called as fallback in `check_gate_integrity()` so the workflow's `GITHUB_TOKEN` env is actually used when authorizing API requests.
- **Protected paths expanded**: `docs/doc-gate.toml`, `pyproject.toml`, and `tests/conftest.py` added to `PROTECTED_PREFIXES` so gate rules are enforced for these data files.
- **pull_request_target rationale corrected**: Comments updated to accurately state that `pull_request_target` checkout defaults to the base branch, not the merge ref, while keeping the explicit `ref: ${{ github.base_ref }}` pin.
- **SKILL.md whitespace reverted**: Re-indent of the two `Tests-Skipped-Intentionally` lines reverted from 3-space back to 2-space.
### Tests

- Fixed a CI flake in `tests/test_merge_attribution.py`: two assertions checked that an excluded PR number ("41") was a bare substring of `result.stdout`, but the fixture commit shas are generated at runtime, so a sha for the in-scope PR could coincidentally contain "41" and fail the assertion for a reason unrelated to the actual reconciliation logic. Both now assert on the exact `"#41"` PR-reference token the checker prints, which cannot collide with a hex sha substring.

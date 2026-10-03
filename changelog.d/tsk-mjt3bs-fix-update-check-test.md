### Fixed
- Fixed `test_update_check_hides_docs_only_diff` to properly mock the preflight check and subprocess return codes. The test now verifies that the update check endpoint correctly handles preflight validation and suppresses docs-only updates.

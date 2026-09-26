### Added
- Guard-discrimination gate (`scripts/check_non_discriminating.py`, `guard-discrimination-gate` workflow): a test marked `@pytest.mark.guards("module:function", replace=[...])` is re-run against a broken variant of the function it guards and must fail there; a guard that cannot see its defect exits 1, an uncheckable guard exits 2.
### Fixed
- `test_release_does_not_free_another_holders_lease` now exercises an agent (`a2a:`) lease with a spoofed `holder`, so it fails on the admin-reads-holder-as-identity defect it was written to guard.
